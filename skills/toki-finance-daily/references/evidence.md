# 补充原文证据

采集器优先读取公开结构化数据。需要补充原文或用户新增来源时，将资料规范成 `dataset.json` 的 records 记录再引用。

记录字段：`id`（本次唯一稳定标识）、`kind`（news / calendar / macro）、`source_id`、`title`、`url`（原始公开页面）、`published_at`（已核实的含时区时间，无时间则 null）、`observed_at`（原始读取时刻，缓存复用时保持）、`summary`（与原文一致的必要摘要）、`data`（结构化字段）。来源在 sources 中补充 `id`、`name`、`url`、`status: ok`、`count`、`error: null`、`fetched_at`。追加原文快照到本次 `raw/` 目录。

新闻只纳入 window_start 至 cutoff 之间的发布时间；无发布时间可用作明确标记的背景资料。宏观数据注明 value、unit、period 和 series_id。日历注明 event_at、region，保留原始日期精度与时区。期望值只有原来源明确提供时才填写 forecast；政策发布日、统计所属期和采集时刻是不同字段。

境外日历只提供当地日期时，保留 `YYYY-MM-DD` 与 `data.provider_timezone`，在文案中说明当地日期，不猜测时分或换算为上海日期。会议日程保留明确的开始日与结束日。

技术证据带 `data.topic: technical`、提供者、品种与原始周期，分别保留综合、均线和技术指标评级。明细表的更新时间以该表自身标记为准；无更新时间时写 `provider_at: null`、`availability_rule: observed_snapshot`，由 `observed_at` 表示可见快照。形态日期与指标周期分别保存。官方接口需要 Token 时先确认免费账户额度，凭证通过环境变量配置。

本地契约校验检查引用和字段，无法自动证明摘要或推理符合原文。补充后核对标题、数值、时间和事实关系，再成稿。
