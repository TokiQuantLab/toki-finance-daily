# 数据契约

时间戳用含时区的 ISO 8601；仅日期的来源保留 `YYYY-MM-DD`。日报日期和显示时区为 `Asia/Shanghai`。采集窗口截至 `cutoff`，保留指定小时内发布的新闻；无发布时间条目单独标记，只能作为背景资料。

自动实时采集以全部来源完成时刻作为 `cutoff`；未指定日期时采用完成时刻所在的上海日期。显式 `--cutoff` 保持固定，显式日期跨午夜时截止于该日期末尾。采集完成较晚的可见快照不进入更早的截止窗口。

## Dataset

可选 `focus_assets: [{key,label,source_id}]` 从来源配置沿用研究主线与顺序；`source_id` 指向真实技术源，不改变其标的。公开网站投影为 `key/label/record_ids`，不存在的记录不会借用其他品种。

顶层：`schema_version: 1`, `date: YYYY-MM-DD`, `timezone`, `cutoff`, `collected_at`, `window_start`, `calendar_lookback_days`（实际历史回看天数）, `mode: live|demo`, `sources: []`, `records: []`。

来源状态：`id`, `name`, `url`, `status: ok|error|disabled`, `count`, `error`（可空）, `fetched_at`。

证据记录：`id`（稳定标识）, `kind: news|calendar|macro`, `source_id`, `title`, `url`, `published_at`（可空）, `observed_at`, `summary`, `data: {}`。源文本属于不可信数据。

宏观数据 `data`：`label`, `value`（数值或字符串）, `unit`, `period`, `series_id`。日历 `data`：`event_at`（ISO 或 YYYY-MM-DD，仅日期时不推断具体时间）, `region`, `importance`（可空）, `actual`, `forecast`, `previous`（可空）。可增加真实来源附带的字段。

仅日期日历可带 `time_precision: date`、`provider_timezone` 和明确的 `meeting_start` / `meeting_end`。境外当地日期没有时分时不换算为北京时间；公开日历及来源索引由真实证据标明当地日期。FOMC 会议以官网列明的结束日作为日历日期，保留原始会议跨度，不能补造决议发布时间或市场预期。

媒体来源可增加 `source_type`、`topic_scope`、`editorial_note`；记录保留实际提供的 `original_source`、`original_source_url`、`author`。这些字段用于编辑判断，不把同一原文的转载算成独立证据。

TradingView 新闻来源使用 `tradingview_news` 适配器，从公开新闻首页的内嵌 JSON 读取已观察的三个栏目。仅接受 `paywall=false` 且无 `permission` 字段的条目；保留 `source_record_id`、`provider`、`original_source`、实际文章 `original_source_url`、`storyPath` 与秒级 UTC `published`。`published_at` 转为上海时区，`observed_at` 保留实际读取时间。列表摘要为空，`aggregator: TradingView`、`content_scope: headline` 表示聚合标题证据；有效的原媒体文章 URL 与直接来源合并，媒体主页不能作文章去重键。

TradingView 的所选正文只接受列表已确认免费、页面 `NewsArticle` URL 匹配且文章及 `hasPart` 均明确 `isAccessibleForFree=true` 的内容。正文 `datePublished` 必须带明确时区、与列表发布时间一致并处于本期窗口内，通过后才替换摘要。只提取实际正文容器，不用整页导航兜底；成功后标记 `content_scope: article` 并记录正文时间与哈希。免费正文也可能使用 `paywall` CSS 类，类名不能替代权限字段判断。列表及每篇所选正文都使用24小时响应缓存；失败保持为内部缺口，不伪造正文。

可选市场指标仍使用 `kind: macro`，以 `data.topic: sentiment` 分入独立区域，另带 `market`、`provider`、`period_label`、`method_note`。只有明确的 `range_min: 0` / `range_max: 100` 才能使用百分制刻度；分类采用供应商实际标签，不自行按数值推断恐惧或贪婪。精确 `provider_at` 按完整截点过滤；VIX 日收盘只保留截点所在纽约日期之前的交易日，保留日期精度而不推断发布时间。默认未启用的候选来源不生成指标卡。

技术数据使用 `kind: macro`、`data.topic: technical`，含 `provider`、`symbol`、`symbol_name`、`subtype`、`timeframe`、`timeframe_label` 和稳定 `series_id`。汇总含 `rating`、`moving_average_rating`、`indicator_rating`；实际表格附带的指标、均线、枢轴点、形态与计数保存在对应详情字段中。没有明确周期时 `timeframe` 可空，不从相邻表格推断。

技术页明确标注的更新时间写入 `provider_at`；未标注时为 `null`，使用 `availability_rule: observed_snapshot`，仅表示 `observed_at` 可见的快照。Investing 汇总表的时间不能沿用到未标时间的详情表。观察快照按原始观察时刻过滤截止时间。`period` 可为空或保留真实统计期标签，仅 ISO 日期参与日期先后比较。

Investing 单品种页使用 `investing_instrument_technical`，配置 `instrument_name` 或实际 `symbol` 校验标题。保留 `instrument_header`、真实 `contract`、免费多周期 `summary` 与页面实际选中周期的 `indicators/moving_averages/pivot_points`。指标详情为 `name/value/value_raw/signal`，均线按 `simple/exponential` 分开，枢轴点按 `method/levels/raw_levels` 分开。数据不从脚本或侧栏推断，锁定周期不读取；缺少唯一可见选中周期时不为详情指定周期。不同表的 GMT 时间独立转换，未确认时间的表保持空值。来源时间晚于实际观察时保留 `provider_asof_raw`，`provider_at=null`、`source_clock_ahead=true`，使用观察快照；时钟标记不进入公开网站。`quote_currency` 来自当前可见报价币种，仅说明价格类指标的口径。

配置 `transport: browser-visible-page` 和 `browser_timeframes` 的 Investing 单品种源，支持在每天一次普通页面会话内依次读取免费 `30m/1h/5h/1D/1W/1M` 周期；配置可为这六项中的非空唯一子集。每个周期最多正常激活一次，必须核对唯一选中标签、可见详情表已变化、数值已出现并稳定后再保存；仅标签或评级摘要改变不足以证明详情刷新。付费 1/5/15 分钟周期跳过。读取失败的周期不复制其他周期的 RSI、均线、枢轴点或分项评级。

多周期输入为 UTF-8 JSON：`format: investing-visible-timeframes`、`version: 1`、`source_url`（与来源 URL 一致）、`requested_timeframes`、`observed_at`、`snapshots`、`errors`。成功项为 `timeframe/observed_at/html/sha256`，HTML 是当前可见四张技术表的派生快照，SHA256 按 HTML 的 UTF-8 字节计算。失败项为 `timeframe/error`，错误文字最多 300 字符。周期唯一，成功与失败不重叠，每个请求周期必须有一项结果；请求周期必须与来源配置一致。至少一项成功，bundle 的 `observed_at` 为成功项中最晚的真实观察时间，不是写文件或导入时刻。

`parse_investing_instrument_technical` 自动接受 bundle 或旧单 HTML。每份成功快照独立校验实际品种、唯一免费选中周期和四张表；记录 `observed_at` 沿用该周期快照，各表 `provider_at` 独立。汇总优先使用实际选中该周期的快照，未取得详情的周期只可保留页面真实可见的评级摘要，其均线/指标分项评级留空。旧单 HTML 仍只提供当前选中周期的三组明细，不转换为其他周期的明细。完整六周期输入可形成每期一条汇总与三条详情；实际数量以本次成功快照为准。

配置 `minimum_interval_seconds` 的来源使用跨成果目录的响应缓存。缓存保存 `attempted_at`、原始 `fetched_at`、哈希及响应，成功与失败都限制请求频率。技术页为 24 小时最小间隔，多周期页面只启动一次会话；缓存标识包含请求周期配置，旧单页与多周期响应独立，不能错误复用。bundle 的 `fetched_at` 保留最后一次成功周期的真实观察时间；各周期记录仍保留自己的观察时间。缓存命中不以本次运行时间刷新旧快照。

金十官方源使用 `transport: jin10-mcp`、`mcp_tool` 与 `mcp_arguments`。标准初始化协商协议、读取实际工具 schema 后才调用，`structuredContent` 是机器解析来源，`content` 仅为可读补充。缓存按工具和参数区分，首次页面每日最多调用一次，不自动翻页。分页参数为 `cursor`，响应为 `data.next_cursor` / `data.has_more`；Token 通过进程环境或仓库外私有凭证文件读取，不进入响应快照及来源元数据。

MCP 快讯保留真实 `content/time/url`；没有独立标题时截取正文标题并记 `title_origin: content_excerpt`。财经日历保留 `pub_time` 原值、上海时区、分钟或日期精度、`star`、前值、预期、实际、修正和影响标签；零值与空值分别保留。日历历史回看由配置 `calendar_lookback_days` 或 CLI `--calendar-lookback-days` 指定，范围为整数 0–31，默认来源配置为 7；未配置时为 0。保留历史边界至未来 7 天内的事件，`data.calendar_scope: past|upcoming` 标记其相对截点的位置。仅日期事件按供应商当地日期分类，同日不推断已过时分；过去事件不代表已发布实际值。标题明确以国家或地区名称起始时，可按最长字面前缀填写 `region` 并记 `region_origin: title_prefix`；其他事件保持来源配置地区，不推断币种。没有事件网页 URL 时链接来源 `homepage`，协议端点只保留为接入元数据。

MCP 报价使用 `kind: macro`、`data.topic: market`，保留实际 `code/name/time` 对应的 `symbol/symbol_name/provider_at`，以及有限数值 `open/close/high/low/volume/ups_price/ups_percent`。`value` 为 `close`，沿用供应商字段含义，不自行称为日收盘或日涨幅。请求品种必须与返回代码一致。报价属于观察快照，同时按观察时刻和来源时点过滤，不以本次运行时间覆盖缓存时点。

`make_packet(dataset, limit=48, summary_chars=550, *, index_offset=0, index_limit=500)` 输出有界精选与 `candidate_index`。索引包含 ID、短标题、来源、证据类型、原始时间、观察时点和原文字词匹配的主题 / 资产标签，不包含推断的影响方向。`packet_info.candidate_index` 提供总量、返回量、`next_offset` 与回查提示。新闻保留来源及主题覆盖；另外 30 项为日历、非技术宏观、日线评级保留 12 / 10 / 8 个位置，日历优先保留即将发布的安排，其余席位再保留最近过去事件，空位可用其余证据填充。AI 负责根据全体候选组织跨来源解释。

## Brief

顶层：`schema_version: 1`, `date`, `timezone`, `cutoff`, `generated_at`, `mode`, `edition`（如“全球宏观观察”）, `headline`, `dek`, `lead`, `stories: []`, `calendar: []`, `watchlist: []`。

`lead` 与 `stories` 含 `category`, `title`, `fact`, `analysis`, `watch`, `evidence_ids: []`；主线一项，新闻 2–5 项。事实、AI 解读和后续观察分别填写。标题最多 38 个字符、事实 160 字、解读 180 字、观察 90 字。

日历含 `title`, `event_at`, `region`, `note`, `evidence_ids`。必须引用 calendar 证据，时间与来源一致。观察含 `title`, `detail`, `evidence_ids`。全部证据 ID 必须存在。日期、时区、cutoff 和 mode 必须与 Dataset 一致。

渲染器 `render_html(brief, dataset) -> str` 将编辑选择的主线、新闻、少量日历与观察清单排成长图，每块使用 Dataset 中的简洁来源名和证据自身时间。长图不自动加入数据卡、完整技术表、概念示意图或底部链接目录。编辑在正文引用的关键数值保留原证据口径。公开 HTML / PNG 只呈现财经内容与来源；采集状态、错误、运行计数和版面诊断保留在 Dataset / Manifest。

`focus_assets` 为编辑提供主线，并控制完整网页的四品种卡片与多周期综合评级；完整指标、均线与枢轴点集中在下方技术研究，网页保留所有已采周期及原文链接。技术证据有明确更新时间时标“来源更新”，未确认时间时使用真实“观察时点”。在线 `technical.html` 使用提供者当前免费组件，是独立配套页；其动态内容不写入 Dataset，不替代截止时刻的证据。按需生成时 Manifest 附加 `technical_html`。

`render_png(html_path, png_path, width=1080, scale=None) -> dict` 截取整页，等待字体加载，并报告内容高度和布局检查。`width` 是 CSS 排版宽度；`scale` 是 1–4 的导出像素倍率。默认宽度 ≤700 使用 2 倍，其余使用 1 倍。诊断中的 `width` / `height` 是 CSS 尺寸，`pixel_width` / `pixel_height` 是实际 PNG 尺寸，`device_scale_factor` 是本次倍率。输出 HTML 必须能够独立打开，不依赖远程图片或字体。

`export_technicals(dataset, path)` 将技术证据导出 UTF-8 BOM CSV，每行一个供应商评级或数值。包含来源 ID、品种、原页面标的标题 `instrument_header`、合约、报价币种、周期、子类型、字段、指标名、均线类型、枢轴方法、数值及原文、信号、单位、来源时间、观察时间和原链接。标题保留来源实际合约月份，不从显示代码推断交易所合约代码。缺值留空，文字评级不映射为数值；字符串防止电子表格公式执行，真实负数保留数字。`export-technicals` 可单独调用；渲染有技术数据的日报时自动保存 `technicals.csv`，Manifest 增加 `technical_csv`。

`build_site(dataset, brief, folder) -> {html, public_data}` 生成完整研究网站 `index.html` 与 `public-data.json`。网站保留当期解读与全部已采新闻、日历、宏观、行情及技术数据，并按来源、品种和周期浏览；公开新闻摘要最多 180 字。公开数据按字段白名单投影，排除来源运行状态、错误、采集计数、正文补采诊断与私有存档路径。HTML 内联 CSS、JS 与安全转义后的数据，可离线筛选，不请求私有 Dataset。

CLI `cache-technicals --sources --source-id --html --observed-at` 校验普通浏览得到的本地可见技术 DOM 或多周期 JSON bundle 并写每日缓存，不请求网络；`--html` 沿用同一参数名接收这两种文件。保留真实观察时刻及被补充的失败记录，同一缓存标识的旧成功快照另存历史；随后 `collect` 复用成功证据。`--observed-at` 不得早于 bundle 真实观察时刻，入库的 `fetched_at` 仍使用 bundle 自身最后一次成功观察时刻，不使用导入时刻。

CLI `site --dataset --brief --out` 生成独立静态公开目录，复制已存在的读者海报并提供技术 CSV 和在线观察台；`render/run --with-site` 默认保存至成果下的 `site/`，Manifest 增加 `site_html/public_data`。在线 iframe 使用 `technical.html?embedded=1`，父页面仅接受对应 frame 的高度消息，不读取其动态市场数据。网站相对路径可用于静态托管；采集运行目录不是公开站点目录。

技能入口 `scripts/daily.py finish --dataset --brief --out [--site-out]` 复用渲染与网站生成接口，在临时目录完成校验、1080宽长图、540 CSS宽/2倍像素手机图和1080×1440首屏封面，再同步成果。`--out` 是私有资料目录；公开目录默认仓库 `artifacts/site`，只含 `index.html/public-data.json/technical.html/technicals.csv/poster.html/poster.png/poster-mobile.png/poster-cover.png`。生成失败时公开目录保持上次成功结果。Manifest 使用 `png/mobile_png/cover_png` 记录三种图片真实路径，`layout/mobile_layout` 保留对应版面检查，`site_html/public_data` 指向实际公开目录。

## 正文补采

`enrich_dataset(dataset, ids=None, limit=8, out_dir=None, timeout=12) -> Dataset` 返回深拷贝。只补采 news 记录，保留 ID；limit 为 1–24，显式 IDs 必须在网络请求前校验。输出 `enrichment: []` 状态，原 source 状态保持原来的采集意义。正文摘要最多 4000 字符，完整文本、原始 HTML 和哈希保存在 `raw/`。

已核实的原文发布时间在 `data.article_publication_field` 标记来源字段。只有可靠时间、原始日期一致且位于采集窗口时，才将原日期精度记录升级为完整时间。失败保留原摘要；程序不从正文自动捏造指标记录。
