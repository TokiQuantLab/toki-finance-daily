/* node --test tests/test_pages_publish.cjs */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const source = fs.readFileSync(path.join(__dirname, "../skills/toki-finance-daily/scripts/publish.js"), "utf8");
const publishPages = new Function(source + ";return publishPages;")();
const names = ["public-data.json", "technical.html", "technicals.csv", "poster.html",
  "poster.png", "poster-mobile.png", "poster-cover.png", "index.html", ".nojekyll"];
const original = "1".repeat(40), next = "2".repeat(40), treeSha = "3".repeat(40);
const options = {
  repository: "TokiQuantLab/toki-finance-daily", branch: "gh-pages",
  manifestPath: "/tmp/Toki's $(echo private)`path`/manifest.json",
  helperPath: "/tmp/reader's scripts/pages.py", workdir: "/tmp/project",
};
const blobSha = data => crypto.createHash("sha1").update("blob " + data.length + "\0").update(data).digest("hex");
const structured = value => ({structuredContent: value});
const fetched = value => structured({content: JSON.stringify(value)});

function fixture(settings = {}) {
  const publishNames = settings.domain ? [...names, "CNAME"] : names;
  const calls = [], data = new Map(publishNames.map(name => [name, name === ".nojekyll" ? Buffer.alloc(0)
    : name === "CNAME" ? Buffer.from((settings.badCname ? settings.domain.toUpperCase() : settings.domain) + "\n")
    : name === "poster.png" ? Buffer.alloc(65535 * 2 + 7, 173) : Buffer.from("Public " + name + " 财经日报")]));
  const manifest = {
    date: "2026-10-11", mode: "live", timezone: "Asia/Shanghai",
    cutoff: "2026-10-11T22:30:00+08:00",
    url: settings.domain ? "https://" + settings.domain + "/" : "https://tokiquantlab.github.io/toki-finance-daily/",
    chunk_size: 65535, chunk_encoding: "base64",
    files: publishNames.map(name => ({name, path: "/tmp/staging/files/" + name, size: data.get(name).length,
      sha256: crypto.createHash("sha256").update(data.get(name)).digest("hex"),
      chunks: Math.ceil(data.get(name).length / 65535)})),
  };
  if (settings.domain) manifest.custom_domain = settings.domain;
  let head = original, active = 0, maximum = 0, inMutation = false;
  const wrap = (name, callback) => async args => {
    const mutation = name !== "fetch" && name !== "exec_command";
    assert.ok(!mutation || !inMutation, "GitHub mutations must stay sequential");
    if (mutation) inMutation = true;
    calls.push({name, args});
    try { return await callback(args); }
    finally { if (mutation) inMutation = false; }
  };
  const tools = {
    exec_command: wrap("exec_command", async args => {
      assert.equal(args.workdir, options.workdir);
      assert.equal(args.yield_time_ms, 30000);
      assert.ok(args.cmd.includes("Toki'\\''s $(echo private)`path`"), "paths must be shell quoted");
      if (args.cmd.startsWith("python3 -c ")) return {exit_code: 0, output: JSON.stringify(manifest)};
      assert.equal(args.max_output_tokens, 65000);
      assert.ok(args.cmd.includes("reader'\\''s scripts/pages.py"));
      const match = args.cmd.match(/--name '([^']+)' --offset (\d+) --size (\d+)$/);
      assert.ok(match);
      assert.equal(Number(match[3]), 65535);
      active++; maximum = Math.max(maximum, active);
      await new Promise(resolve => setTimeout(resolve, 1));
      active--;
      let output = data.get(match[1]).subarray(Number(match[2]), Number(match[2]) + Number(match[3])).toString("base64");
      if (settings.truncate && match[1] === "poster.png") output = output.slice(0, -4);
      if (settings.badExit && match[1] === "poster.png") return {exit_code: 1, output};
      return {exit_code: 0, output};
    }),
    mcp__codex_apps__github_fetch: wrap("fetch", async args => {
      if (args.url.includes("/git/trees/")) {
        const previous = publishNames.map(name => ({path: name, mode: "100644", type: "blob",
          sha: settings.unchanged ? blobSha(data.get(name)) : "4".repeat(40)}));
        if (settings.remoteCname && !settings.domain) previous.push({path: "CNAME", mode: "100644", type: "blob", sha: "5".repeat(40)});
        return fetched({truncated: false, tree: previous});
      }
      const branch = args.url.endsWith("/main") ? "main" : "gh-pages";
      if (branch === "gh-pages" && head === original && !settings.existing) {
        return {isError: true, structuredContent: {error: "GitHub API error 404", error_code: "NOT_FOUND",
          error_data: {message: "Branch not found", status: "404"}}};
      }
      if (settings.fetchError) return {isError: true, structuredContent: {error_code: "UNAUTHORIZED", error: "denied"}};
      return fetched({ref: "refs/heads/" + branch, object: {type: "commit", sha: head}});
    }),
    mcp__codex_apps__github_create_blob: wrap("create_blob", async args => {
      assert.equal(args.encoding, "base64");
      assert.equal(args.repository_full_name, options.repository);
      const index = calls.filter(call => call.name === "create_blob").length - 1;
      assert.deepEqual(Buffer.from(args.content, "base64"), data.get(publishNames[index]));
      const expectedChunks = manifest.files.reduce((count, file) => count + Math.max(1, file.chunks), 0);
      assert.equal(calls.filter(call => call.name === "exec_command" && !call.args.cmd.startsWith("python3 -c ")).length, expectedChunks,
        "all chunks must be checked before the first mutation");
      if (settings.blobForbidden) return {isError: true, structuredContent: {error: "denied", error_code: "FORBIDDEN",
        error_data: {status: "403", message: "Resource not accessible by integration"}}};
      return structured({sha: blobSha(Buffer.from(args.content, "base64"))});
    }),
    mcp__codex_apps__github_create_tree: wrap("create_tree", async args => {
      assert.ok(!("base_tree_sha" in args), "the reader tree cannot inherit private source files");
      assert.deepEqual(args.tree_elements.map(item => item.path), publishNames);
      return structured({sha: treeSha});
    }),
    mcp__codex_apps__github_create_commit: wrap("create_commit", async args => {
      assert.equal(args.parent_sha, original);
      assert.equal(args.tree_sha, treeSha);
      return structured({sha: next});
    }),
    mcp__codex_apps__github_create_branch: wrap("create_branch", async args => {
      assert.equal(args.branch_name, "gh-pages"); assert.equal(args.sha, next);
      assert.ok(!("base_ref" in args));
      head = next;
      return structured({ref: "refs/heads/gh-pages"});
    }),
    mcp__codex_apps__github_update_ref: wrap("update_ref", async args => {
      assert.equal(args.force, false); assert.equal(args.expected_sha, original); assert.equal(args.sha, next);
      if (settings.leaseError) return {isError: true, structuredContent: {error: "lease mismatch", error_code: "CONFLICT"}};
      head = next;
      return structured({ref: "refs/heads/gh-pages"});
    }),
  };
  return {tools, calls, manifest, data, get maximum() { return maximum; }};
}

test("first publication creates a complete reader branch from main, with no private files", async () => {
  const f = fixture();
  const result = await publishPages(f.tools, options);
  assert.deepEqual(result, {commit: next, branch: "gh-pages", date: f.manifest.date,
    cutoff: f.manifest.cutoff, url: f.manifest.url, filecount: 9});
  assert.equal(f.calls.filter(c => c.name === "create_branch").length, 1);
  assert.equal(f.calls.filter(c => c.name === "update_ref").length, 0);
  assert.ok(f.maximum > 1 && f.maximum <= 8);
  assert.ok(!JSON.stringify(result).includes("base64"));
});

test("an existing reader branch uses a fast-forward lease", async () => {
  const f = fixture({existing: true});
  assert.equal((await publishPages(f.tools, options)).commit, next);
  assert.equal(f.calls.filter(c => c.name === "update_ref").length, 1);
  assert.equal(f.calls.filter(c => c.name === "create_branch").length, 0);
});

test("identical public files keep the current commit", async () => {
  const f = fixture({existing: true, unchanged: true});
  assert.equal((await publishPages(f.tools, options)).commit, original);
  assert.equal(f.calls.filter(c => ["create_tree", "create_commit", "update_ref", "create_branch"].includes(c.name)).length, 0);
});

test("truncated chunks and failed chunk reads stop before all GitHub mutations", async () => {
  for (const settings of [{truncate: true}, {badExit: true}]) {
    const f = fixture(settings);
    await assert.rejects(publishPages(f.tools, options), /截断|读取公开文件分块/);
    assert.equal(f.calls.filter(c => c.name.startsWith("create_") || c.name === "update_ref").length, 0);
  }
});

test("a rejected lease stops without a retry or forced ref update", async () => {
  const f = fixture({existing: true, leaseError: true});
  await assert.rejects(publishPages(f.tools, options), /并发冲突/);
  const updates = f.calls.filter(c => c.name === "update_ref");
  assert.equal(updates.length, 1);
  assert.equal(updates[0].args.force, false);
  assert.equal(f.calls.at(-1).name, "update_ref");
});

test("a failed fetch is not treated as a missing branch", async () => {
  const f = fixture({existing: true, fetchError: true});
  await assert.rejects(publishPages(f.tools, options), /读取 gh-pages/);
  assert.equal(f.calls.filter(c => c.name.startsWith("create_") || c.name === "update_ref").length, 0);
});

test("a denied blob reports its exact step and API reason without updating the branch", async () => {
  const f = fixture({existing: true, blobForbidden: true});
  await assert.rejects(publishPages(f.tools, options), /public-data.json.*FORBIDDEN.*403.*Resource not accessible by integration/);
  assert.equal(f.calls.filter(c => c.name === "create_blob").length, 1);
  assert.equal(f.calls.filter(c => ["create_tree", "create_commit", "update_ref", "create_branch"].includes(c.name)).length, 0);
});

test("the exact reader whitelist rejects private paths, extra entries, duplicates and demo data", async () => {
  const changes = [
    f => { f.manifest.files[0].name = "dataset.json"; },
    f => { f.manifest.files[0].name = "../../.env"; },
    f => { f.manifest.files.push({...f.manifest.files[0], name: "brief.json"}); },
    f => { f.manifest.files[0] = {...f.manifest.files[1]}; },
    f => { f.manifest.mode = "demo"; },
    f => { f.manifest.date = "2026-02-30"; },
    f => { f.manifest.chunk_size = 65536; },
  ];
  for (const change of changes) {
    const f = fixture(); change(f);
    await assert.rejects(publishPages(f.tools, options), /清单|契约/);
    assert.equal(f.calls.filter(c => c.name !== "exec_command").length, 0);
  }
});

test("a declared custom domain publishes exactly the reader files and matching CNAME", async () => {
  for (const existing of [false, true]) {
    const f = fixture({domain: "tokiquantlab.com", existing});
    const result = await publishPages(f.tools, options);
    assert.equal(result.filecount, 10);
    assert.equal(result.url, "https://tokiquantlab.com/");
    const tree = f.calls.find(c => c.name === "create_tree").args.tree_elements;
    assert.deepEqual(tree.map(item => item.path), [...names, "CNAME"]);
    assert.equal(tree.at(-1).sha, blobSha(Buffer.from("tokiquantlab.com\n")));
  }
});

test("identical custom-domain files keep the existing commit", async () => {
  const f = fixture({domain: "tokiquantlab.com", existing: true, unchanged: true});
  const result = await publishPages(f.tools, options);
  assert.equal(result.commit, original);
  assert.equal(result.filecount, 10);
  assert.equal(f.calls.filter(c => ["create_tree", "create_commit", "update_ref", "create_branch"].includes(c.name)).length, 0);
});

test("a base manifest cannot silently remove an existing root CNAME", async () => {
  const f = fixture({existing: true, remoteCname: true});
  await assert.rejects(publishPages(f.tools, options), /已有 CNAME.*重新 prepare/);
  assert.equal(f.calls.filter(c => c.name.startsWith("create_") || c.name === "update_ref").length, 0);
  assert.equal(f.calls.filter(c => c.name === "exec_command").length, 1);
});

test("CNAME bytes must equal the declared domain and one newline before any mutation", async () => {
  const f = fixture({domain: "tokiquantlab.com", badCname: true});
  await assert.rejects(publishPages(f.tools, options), /CNAME 内容/);
  assert.equal(f.calls.filter(c => c.name.startsWith("create_") || c.name === "update_ref").length, 0);
});

test("custom domains reject mismatched URLs, non-root URLs, malformed DNS and github.io", async () => {
  const changes = [
    f => { f.manifest.custom_domain = null; },
    f => { f.manifest.custom_domain = "TOKIQUANTLAB.COM"; },
    f => { f.manifest.custom_domain = "127.0.0.1"; f.manifest.url = "https://127.0.0.1/"; },
    f => { f.manifest.custom_domain = "a.123"; f.manifest.url = "https://a.123/"; },
    f => { f.manifest.custom_domain = "toki_quantlab.com"; },
    f => { f.manifest.custom_domain = "-tokiquantlab.com"; },
    f => { f.manifest.custom_domain = "a".repeat(64) + ".com"; },
    f => { f.manifest.custom_domain = "tokiquantlab.github.io"; f.manifest.url = "https://tokiquantlab.github.io/"; },
    f => { f.manifest.custom_domain = "github.io"; f.manifest.url = "https://github.io/"; },
    f => { f.manifest.url = "https://another-domain.com/"; },
    f => { f.manifest.url = "https://tokiquantlab.com/daily/"; },
    f => { f.manifest.url = "https://tokiquantlab.com:443/"; },
    f => { f.manifest.url = "https://tokiquantlab.com/?q=1"; },
    f => { f.manifest.files.pop(); },
    f => { delete f.manifest.custom_domain; },
  ];
  for (const change of changes) {
    const f = fixture({domain: "tokiquantlab.com"}); change(f);
    await assert.rejects(publishPages(f.tools, options), /custom_domain|清单|契约/);
    assert.equal(f.calls.filter(c => c.name !== "exec_command").length, 0);
  }
});
