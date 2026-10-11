/* Loaded by Codex functions.exec; filesystem and GitHub access are injected. */
async function publishPages(tools, options) {
  const names = [
    "public-data.json", "technical.html", "technicals.csv", "poster.html",
    "poster.png", "poster-mobile.png", "poster-cover.png", "index.html", ".nojekyll",
  ];
  const chunkSize = 65535;
  const gitTools = ["fetch", "create_blob", "create_tree", "create_commit", "update_ref", "create_branch"];
  if (!tools || typeof tools.exec_command !== "function" || gitTools.some(
    name => typeof tools["mcp__codex_apps__github_" + name] !== "function"
  )) throw new Error("缺少 exec_command 或 GitHub connector 工具。");
  if (!options || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(options.repository)
      || (options.branch !== undefined && options.branch !== "gh-pages")) {
    throw new Error("repository 必须是 owner/name，发布分支必须是 gh-pages。");
  }
  for (const key of ["manifestPath", "helperPath", "workdir"]) {
    if (typeof options[key] !== "string" || !options[key] || options[key].includes("\0")) {
      throw new Error("缺少有效的 " + key + "。");
    }
  }
  const branch = "gh-pages";
  const repository = options.repository;
  const api = "https://api.github.com/repos/" + repository;
  const quote = value => "'" + value.replace(/'/g, "'\\''") + "'";
  const sha = (value, action) => {
    if (typeof value !== "string" || !/^[0-9a-f]{40}$/.test(value)) {
      throw new Error(action + " 未返回有效的 Git SHA。");
    }
    return value;
  };
  const unpack = (result, action) => {
    const data = result && result.structuredContent;
    if (!result || result.isError || result.error || !data || data.error) {
      throw new Error(action + " 失败，已停止发布。");
    }
    if (typeof data.content === "string") {
      try { return JSON.parse(data.content); }
      catch (_) { throw new Error(action + " 未返回有效的结构化 JSON。"); }
    }
    return data;
  };
  const run = async (cmd, maxTokens, action) => {
    const result = await tools.exec_command({
      cmd, workdir: options.workdir, max_output_tokens: maxTokens, yield_time_ms: 30000,
    });
    if (!result || result.exit_code !== 0 || result.session_id !== undefined
        || typeof result.output !== "string") {
      throw new Error(action + " 失败，已停止发布。");
    }
    return result.output;
  };

  // prepare is a separate, authorized action. This reads its frozen manifest only.
  const readManifest = "import sys;from pathlib import Path;sys.stdout.write(Path(sys.argv[1]).read_text(encoding='utf-8'))";
  let manifest;
  try {
    manifest = JSON.parse(await run(
      "python3 -c " + quote(readManifest) + " " + quote(options.manifestPath), 8000, "读取发布清单"
    ));
  } catch (error) {
    if (error instanceof SyntaxError) throw new Error("发布清单不是完整的 JSON。");
    throw error;
  }
  const validDate = typeof manifest.date === "string"
    && /^\d{4}-\d{2}-\d{2}$/.test(manifest.date)
    && !Number.isNaN(Date.parse(manifest.date + "T00:00:00Z"))
    && new Date(manifest.date + "T00:00:00Z").toISOString().slice(0, 10) === manifest.date;
  if (!validDate || manifest.mode !== "live" || manifest.chunk_size !== chunkSize
      || manifest.chunk_encoding !== "base64" || typeof manifest.cutoff !== "string"
      || Number.isNaN(Date.parse(manifest.cutoff)) || typeof manifest.url !== "string"
      || !/^https:\/\/[^/?#@\s]+(?:\/[^?#\s]*)?$/.test(manifest.url)) {
    throw new Error("发布清单的日期、live 模式、截止时间、网址或分块契约不正确。");
  }
  let customDomain;
  if (Object.prototype.hasOwnProperty.call(manifest, "custom_domain")) {
    customDomain = manifest.custom_domain;
    const rootUrl = manifest.url.match(/^https:\/\/([^/?#@:\s]+)\/?$/);
    if (typeof customDomain !== "string" || customDomain.length > 253
        || customDomain !== customDomain.toLowerCase() || customDomain.split(".").length < 2
        || customDomain.split(".").some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))
        || !/[a-z]/.test(customDomain.split(".").at(-1))
        || /^(?:\d+\.)+\d+$/.test(customDomain)
        || customDomain === "github.io" || customDomain.endsWith(".github.io")
        || !rootUrl || rootUrl[1].toLowerCase() !== customDomain) {
      throw new Error("custom_domain 必须是与根 HTTPS 网址一致的小写 ASCII DNS 域名，不能使用端口、IP 或 github.io。");
    }
    names.push("CNAME");
  }
  if (!Array.isArray(manifest.files) || manifest.files.length !== names.length
      || manifest.files.some(item => !item || !names.includes(item.name))
      || new Set(manifest.files.map(item => item.name)).size !== names.length) {
    throw new Error("发布清单必须恰好包含基础 9 个公开文件" + (customDomain ? "与 CNAME。" : "。"));
  }
  const files = names.map(name => manifest.files.find(item => item.name === name));
  for (const file of files) {
    if (!Number.isSafeInteger(file.size) || file.size < 0
        || (file.name !== ".nojekyll" && file.size === 0)
        || (file.name === ".nojekyll" && file.size !== 0)
        || (file.name === "CNAME" && file.size !== customDomain.length + 1)
        || file.chunks !== Math.ceil(file.size / chunkSize)
        || typeof file.path !== "string" || !file.path.endsWith("/files/" + file.name)
        || typeof file.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(file.sha256)) {
      throw new Error("公开文件清单格式不正确：" + file.name);
    }
  }

  const refUrl = api + "/git/ref/heads/" + branch;
  const refResult = await tools.mcp__codex_apps__github_fetch({url: refUrl});
  const refError = refResult && refResult.structuredContent;
  const missing = !!(refResult && refResult.isError && refError
    && refError.error_code === "NOT_FOUND"
    && (!refError.error_data || refError.error_data.status === undefined
        || String(refError.error_data.status) === "404"));
  const ref = missing
    ? unpack(await tools.mcp__codex_apps__github_fetch({url: api + "/git/ref/heads/main"}), "读取 main 分支")
    : unpack(refResult, "读取 gh-pages 分支");
  if (ref.ref !== "refs/heads/" + (missing ? "main" : branch)
      || !ref.object || ref.object.type !== "commit") {
    throw new Error("发布分支引用不是预期的 commit。");
  }
  const parent = sha(ref.object.sha, "读取发布分支");
  let oldTree;
  if (!missing) {
    oldTree = unpack(await tools.mcp__codex_apps__github_fetch({
      url: api + "/git/trees/" + parent + "?recursive=1",
    }), "读取现有公开文件树");
    if (oldTree.truncated !== false || !Array.isArray(oldTree.tree)) {
      throw new Error("现有公开文件树不完整，已停止发布。");
    }
    if (!customDomain && oldTree.tree.some(item => item.path === "CNAME")) {
      throw new Error("远端 gh-pages 已有 CNAME；请按当前域名重新 prepare，声明 custom_domain 后再发布，不能删除现有域名配置。");
    }
  }

  // Read and validate every chunk before any GitHub mutation. The helper checks
  // each file's exact path, byte count and SHA-256 against the frozen snapshot.
  const jobs = [];
  const chunks = new Map(files.map(file => [file.name, []]));
  for (const file of files) {
    for (let i = 0; i < Math.max(1, file.chunks); i++) {
      jobs.push({file, index: i, offset: i * chunkSize});
    }
  }
  let nextJob = 0;
  await Promise.all(Array.from({length: Math.min(8, jobs.length)}, async () => {
    while (nextJob < jobs.length) {
      const job = jobs[nextJob++];
      const output = await run(
        "python3 " + quote(options.helperPath) + " blob --manifest " + quote(options.manifestPath)
        + " --name " + quote(job.file.name) + " --offset " + job.offset + " --size " + chunkSize,
        65000, "读取公开文件分块：" + job.file.name
      );
      const length = Math.ceil(Math.min(chunkSize, job.file.size - job.offset) / 3) * 4;
      if (output.length !== length
          || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(output)) {
        throw new Error("公开文件分块被截断或编码不完整：" + job.file.name);
      }
      chunks.get(job.file.name)[job.index] = output;
    }
  }));
  if (customDomain) {
    // DNS labels are ASCII. Encode the exact single CNAME line without relying
    // on Node Buffer, browser APIs or an external network/runtime dependency.
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    const line = customDomain + "\n";
    let expected = "";
    for (let i = 0; i < line.length; i += 3) {
      const a = line.charCodeAt(i), b = line.charCodeAt(i + 1), c = line.charCodeAt(i + 2);
      expected += alphabet[a >> 2] + alphabet[((a & 3) << 4) | (Number.isNaN(b) ? 0 : b >> 4)]
        + (Number.isNaN(b) ? "=" : alphabet[((b & 15) << 2) | (Number.isNaN(c) ? 0 : c >> 6)])
        + (Number.isNaN(c) ? "=" : alphabet[c & 63]);
    }
    if (chunks.get("CNAME").join("") !== expected) {
      throw new Error("CNAME 内容必须恰好是 custom_domain 加一个换行，已停止发布。");
    }
  }

  const tree = [];
  for (const file of files) {
    const blob = unpack(await tools.mcp__codex_apps__github_create_blob({
      repository_full_name: repository, encoding: "base64", content: chunks.get(file.name).join(""),
    }), "上传公开文件：" + file.name);
    tree.push({path: file.name, mode: "100644", type: "blob", sha: sha(blob.sha, "上传公开文件")});
    chunks.delete(file.name);
  }
  const result = commit => ({
    commit, branch, date: manifest.date, cutoff: manifest.cutoff, url: manifest.url, filecount: names.length,
  });
  if (oldTree && oldTree.tree.length === tree.length && tree.every(entry => {
    const previous = oldTree.tree.find(item => item.path === entry.path);
    return previous && previous.mode === entry.mode && previous.type === entry.type && previous.sha === entry.sha;
  })) return result(parent);

  const publishedTree = unpack(await tools.mcp__codex_apps__github_create_tree({
    repository_full_name: repository, tree_elements: tree,
  }), "创建公开文件树");
  const commit = unpack(await tools.mcp__codex_apps__github_create_commit({
    repository_full_name: repository,
    message: "Publish finance daily " + manifest.date + " (" + manifest.cutoff + ")",
    parent_sha: parent, tree_sha: sha(publishedTree.sha, "创建公开文件树"),
  }), "创建日报发布提交");
  const commitSha = sha(commit.sha, "创建日报发布提交");
  if (missing) {
    unpack(await tools.mcp__codex_apps__github_create_branch({
      repository_full_name: repository, branch_name: branch, sha: commitSha,
    }), "创建 gh-pages 分支");
  } else {
    unpack(await tools.mcp__codex_apps__github_update_ref({
      repository_full_name: repository, branch_name: branch,
      sha: commitSha, expected_sha: parent, force: false,
    }), "更新 gh-pages 分支（并发冲突时须检查远端后重新发布）");
  }
  const confirmed = unpack(await tools.mcp__codex_apps__github_fetch({url: refUrl}), "核验发布分支");
  if (confirmed.ref !== "refs/heads/" + branch || !confirmed.object
      || confirmed.object.sha !== commitSha) throw new Error("远端发布分支与新提交不一致。");
  return result(commitSha);
}
