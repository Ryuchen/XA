# KOOK 授权未知人工核对

仅独立平台超级管理员可使用 `GET /api/admin/platform/store-management/{store_id}/license-kook-review?kind=PENDING|REVIEWED` 查看本店授权未知事实及核对结果；已核对记录同时展示平台审计是否待恢复。向 `POST /api/admin/platform/store-management/{store_id}/license-kook-review/{source_id}/resolve` 提交 `request_id`（UUID）、`outcome`（`ALLOW` 或 `DENY`）及 `reason`（原时点授权核对依据）。来源必须是本店系统记录的 UNKNOWN 事件，明确 DENY 的来源不能补发；重复相同请求恢复或返回原结果，不重复入账。平台审计失败后用原请求重试，保留最初核对人。

新事实记录账号和业务周期、在线频道、真实计时区间、积分开关及更新时间、累计基数和发放结果；审计与核对记录通过证据摘要绑定。确认 ALLOW 时，区间按原时长回补至当前同一周期的累计，达到原两小时门槛才记积分流水；原发放点未知则按原始累计凭证恢复计算。原积分开关关闭时不发放，账号退店或陪玩注销后重新进入时不向新周期恢复积分；各结果和原因留痕。原始证据不足的旧记录可列出、可依据真实授权作不发放结案，不能猜测金额或 ALLOW 补发。

发放前每个账户重新核验 License；明确拒绝或未知的时段不混入正常累计。若一个监听间隔跨越新授权生效时刻，无法从单次心跳区分该间隔各部分，整个间隔记为 UNKNOWN 待核对，不自动视为续期后可发放时长。批次到期期间未发的完整积分不会在续期后自动补发；原有已发积分及已有权益不受影响。

新增 `engagement.0040_kook_license_points_review` 结构迁移文件，尚未执行。仅完成 Python 语法解析，按开发期要求未测试或迁移；仍需在隔离数据库验证跨店、控制库故障及审计补记、原周期退店并发、事件顺序和真实 KOOK 监听。历史资料不足的 UNKNOWN 记录需要外部真实证据，当前接口不会猜测补发。
