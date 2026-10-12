# 发布已完成的日报

先按主技能完成 collect、prepare、AI 成稿和 finish。发布目标来自用户要求或 `<repo>/config/publishing.local.json`，包含 `repository`、`branch`、`url`，可选 `custom_domain`。个人发布配置不提交到源码仓库。普通生成任务不触发发布。

运行仓库 Python 执行 `<skill>/scripts/pages.py prepare --site <repo>/artifacts/site --out <out>/pages --expected-date <本期日期> --url <线上网址>`。配置了 `custom_domain` 时同时传入 `--cname <custom_domain>`，网址须是该域名的 HTTPS 根网址；程序生成域名配置 `CNAME`，每日发布必须保留它。定时当日日报的预期日期采用任务开始时的北京时间当天日期；历史日报使用用户明确指定的日期，不能从旧成果反推预期日期。程序冻结八个读者文件与 `.nojekyll`（自定义域名时另含 `CNAME`），核对网页和公开 JSON 的日期、真实模式与内容一致，忽略来源目录中其他文件。准备快照前检查公开记录的主线品种六周期综合评级和三组独立明细，存在缺口或无效数值时停止，不生成发布清单。

Manifest 保存于 `<out>/pages/manifest.json`。本次采集或生成失败、日期不符时停止发布，线上保留上一期。

Codex 已连接 GitHub 时，在 `functions.exec` 内用 `tools.exec_command` 读取 `<skill>/scripts/publish.js`，将源码交给 `new Function(source + '; return publishPages;')()`，然后调用返回的函数并传入 `tools` 及选项：`repository`、`branch`、`manifestPath`、`helperPath`（pages.py 的绝对路径）、`workdir`（仓库绝对路径）。程序通过已连接的 GitHub 工具读取发布分支、上传冻结文件并提交完整白名单；源码和二进制内容在工具内处理，不打印到对话中，不需要另配 GitHub Token。

首次发布创建指定分支；GitHub Pages 应使用该分支根目录并部署静态文件。后续只替换发布分支，源码分支保持独立。程序使用当前分支 SHA 更新，遇到并发变化时停止，核对远端新内容后再决定是否重新发布。相同内容不重复提交。GitHub 工具不可用时说明具体缺口，不改用未授权凭证或发布到其他账号。

提交成功只表示发布分支已更新。使用 GitHub 工具查看本次提交 SHA 对应的 Pages 部署结果，再运行仓库 Python 执行 `<skill>/scripts/pages.py verify --manifest <out>/pages/manifest.json --attempts 12 --interval 30`。该命令只核验线上网页、公开 JSON 和读者文件与冻结快照一致；以短时间返回的工具调用启动并继续读取运行结果，等待时不重复采集、生成或发布。部署未结束或线上未取得本期文件，不能报告网页已更新；核验超过等待范围时说明具体状态，之后只继续核验。首次上线或改发布配置后打开真实网址，分别验证电脑与手机显示、下载和在线观察台。
