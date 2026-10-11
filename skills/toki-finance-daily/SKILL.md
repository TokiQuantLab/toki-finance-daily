---
name: toki-finance-daily
description: 采集财经新闻、快讯、官方经济数据与财经日历，围绕黄金、原油、比特币和纳指100生成 Toki 财经日报长图、完整研究网站并更新本地预览。用于当日日报制作、财经资料整理和已有日报数据更新。
---

# Toki 财经日报

程序负责采集、证据存档、校验和固定版式，你负责选题、核对原文与中文成稿。日常更新沿用项目现有模板，只更新数据、解读与生成产物；页面改版属于用户另行指定的任务。长图聚焦新闻与市场传导，网站保留本期完整已采资料。选题从全部来源的重要事件出发，四品种增加相关性关注，不设置固定品种席位或地缘头条。

主线品种是黄金 `XAUUSD`、原油 `USOIL`、比特币 `BTCUSD`、纳指100 `NAS100`。现货、现金指数与期货沿用供应商实际标的、合约和时间，交易关注代码不替代来源口径。

## 入口与固定目录

本技能随完整仓库使用。先解析安装链接，命令中的 `<skill>` 是本文件所在的真实目录，`<repo>` 是 `<skill>` 的上两级目录。`scripts/daily.py` 根据自身真实路径定位仓库并优先使用其 `.venv`；需要本地终端、Python 3.10+、网络与可写目录，技术页浏览和 PNG 导出需要 Playwright Chromium。

未指定成果目录时，使用绝对路径 `<repo>/artifacts/live` 作为 `<out>`；它保存私有 Dataset、Brief、原文与诊断。固定公开目录为 `<repo>/artifacts/site`，供本地预览与后续静态托管使用。保持这两个目录的用途，日常更新直接替换当前生成文件，已有成果由程序保存到私有 `history/`。用户指定其他目录时，沿用其明确指定的工作目录和公开目录。

## 采集与成稿

运行 `python3 <skill>/scripts/daily.py collect --out <out>`。当日日报默认使用当前上海日期与实际截止时刻、48小时新闻窗口、最近7天及未来7天日历。按用户要求传 `--date`、`--cutoff`、`--hours`、`--calendar-lookback-days`；历史截止时间是固定边界，不能纳入之后观察的技术快照。

读取新 `dataset.json` 的实际来源状态与四品种六周期覆盖。程序使用公开数据源、官方发布和已有有效缓存；金十官方 MCP 通过本机私有凭证或 `JIN10_MCP_TOKEN` 读取快讯、日历和报价，Token 接入见项目 [README](../../README.md)。采集失败意味着覆盖缺口，按具体来源排查，不能从记忆补写当天事实。

TradingView 新闻首页已作为候选来源，每24小时读取一次公开列表，只接受明确免费的条目，保留原媒体、原文链接与发布时间。列表记录的 `content_scope: headline` 表示只有标题，具体事实与关键判断先按需 `enrich` 补采已确认免费正文，或回查原始发布方。同栏目重复和上游转载合并，不增加独立证据权重。动态 News Flow 可作人工筛选入口，不单独重复采集；候选仍与全部来源一起按重要性选题。

技术页按来源配置的六个免费周期 `30m/1h/5h/1D/1W/1M` 独立读取。普通采集未取得、但正常浏览可见的页面，按 [普通浏览补充技术页](references/browser-capture.md) 保存可见快照并运行 `cache-technicals`。已有有效六周期缓存可直接复用，原始观察时间保持不变。缓存导入只更新缓存：若浏览补采发生在本次 `collect` 之后，成稿前重新运行 `collect --out <out>` 生成新的 Dataset。当日实时更新不沿用第一次冻结的 `--cutoff`，历史请求仍保留用户指定的截止边界。最终以新 Dataset 中实际进入的表格与缺口为准。

运行 `python3 <skill>/scripts/daily.py prepare --dataset <out>/dataset.json --out <out>`，读取 `prompt.md`、Schema 和候选索引。审视全部来源，结合完整 Dataset 按事件与宏观变量组织证据链；索引还有 `next_offset` 时继续回查剩余记录。比较实际新变化、影响范围、四品种传导、证据质量与时效，决定主线和其他重要消息。选题与事实核对要求见 [编辑要求](references/editorial.md)，主要候选的取舍理由保存在私有 `editorial-notes.md`。

关键判断需要原文时，运行 `enrich --dataset <out>/dataset.json --ids <新闻ID列表> --out <out>` 补采所选正文，再运行 `prepare --dataset <out>/dataset.json --out <out> --summary-chars 1800`。核对对应 `raw/` 文本，按契约写 `<out>/brief.json`。新增来源或人工补充证据时读取 [证据规范](references/evidence.md)。Brief 的日期、时区、截止时刻与模式沿用本次 Dataset；来源事实、解读和后续观察分别写明。

## 完成交付与更新预览

成稿后运行 `python3 <skill>/scripts/daily.py finish --dataset <out>/dataset.json --brief <out>/brief.json --out <out>`。公开目录需要另设时加 `--site-out <公开目录>`。该命令校验成稿，调用现有渲染器生成完整长图、手机长图、封面、技术 CSV 与完整静态网站，生成成功后再同步固定公开目录。它不采集或调用模型，AI 成稿仍由你完成。

交付包含 `poster.html`、1080像素宽 `poster.png`、540 CSS宽度按2倍像素绘制的 `poster-mobile.png`、1080×1440首屏 `poster-cover.png`、`technicals.csv` 与 `site/index.html`。Manifest 记录实际路径与版面检查结果。公开目录只同步读者页面、图片、公开数据和 CSV，原文、凭证与采集诊断留在私有工作目录。生成失败时修正具体问题后重试，固定预览继续保留上一份成功结果。

网页顶部只保留四品种卡片与多周期综合评级，完整指标、移动平均与枢轴点集中在下方技术研究。资讯默认6则、日历默认12项，可逐批阅读、全量展开和从顶部收起；手机栏目响应式排列，宽表在自身区域内左右滚动。复用这些程序行为，每天无需重新写页面。

本地预览固定使用 `http://127.0.0.1:8785/`，服务目录为公开目录。已有本项目预览正常运行时直接刷新；首次使用或需切换本项目服务目录时，以仓库 Python 运行 `-m http.server 8785 --bind 127.0.0.1 --directory <公开目录>`。核对已有端口对应的进程和目录后再切换，本项目之外的服务保持原状。只提供公开目录，不能服务私有工作目录。

打开长图确认中文、标题与新闻密度；打开预览核对日期、截止时刻、资讯、日历与技术周期。模板需要保持当前配色、排版与交互，缺失数据按来源实际覆盖说明。把图片文件与固定网页链接交付用户，必要覆盖缺口在说明中解释；报错、成功失败计数和程序运行信息只留在内部记录。

## 单项更新

用户已有 Dataset 与 Brief、只要求重建输出时，直接运行 `finish`，使用原有截止时刻与真实资料，不把它描述成重新采集。用户仅要求网站数据更新时，运行 `site --dataset <out>/dataset.json --brief <out>/brief.json --out <公开目录>`；该命令使用相同模板并更新公开数据、技术 CSV、观察台和已有海报下载。

只需技术 CSV 时运行 `export-technicals --dataset <out>/dataset.json --out <out>/technicals.csv`。独立在线观察页使用 `workbench --out <out>`；联网组件展示供应商当前内容，不进入 Dataset，也不作为日报截至时刻的历史证据。仅看视觉样张使用 `demo --out <演示目录>`，明确标记离线演示。已配置模型接口时可用 `run` 自动成稿，再运行 `finish` 完成三种图片与固定网站交付；普通 Codex 技能调用使用当前 AI，无需另配模型 API。参数见 `--help`；本技能不发邮件、不创建定时任务、不推送或发布外部网站。
