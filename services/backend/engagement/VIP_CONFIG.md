# 门店 VIP 自定义发放规则

管理员通过 `POST /api/admin/vip-tiers/creation-preview/` 核对新增等级，再将返回的 `token` 作为 `recalculation_token` 提交至 `POST /api/admin/vip-tiers/`；已有等级使用对应 PATCH 接口，门槛变更沿用重算预览。等级顺序为 1–32767，门槛、老板折扣与积分倍率仍使用现有字段及老板分级；`monthly_coupon_count`、`monthly_discount`、`season_discount`、`upgrade_rewards` 和服务绑定仍为独立字段。

调整单个等级顺序时，先向该等级的 `recalculation-preview/` 提交 `{"level": 新顺序}`，再以返回令牌单独 PATCH `level` 与 `recalculation_token`。交换或同时调整多个等级时，向 `/api/admin/vip-tiers/reorder-preview/` 提交 `{"mapping":[{"id":等级ID,"level":新顺序}, ...]}`，映射须包含全部等级且顺序互不重复；随后将相同 `mapping` 和返回的 `recalculation_token` 一次 POST 到 `/api/admin/vip-tiers/reorder/`。后者在同一事务内完成全部改序并仅按最终顺序重算，不经过可见的中间等级。预览后会员或配置变化需要重新核对。顺序变更按等级 ID 保留升级发放身份和已发权益；模板等级改序时保留原权益模板，已发月券按既有条款冻结规则处理。

`benefits.issuance` 是可选对象，支持 `companion_quantity`（0–100）、`companion_variant`（最多 60 字）、`birthday`、`safety_box`、`safety_box_refresh` 和 `highlighted`（均为布尔值），以及 `birthday_discount`（1–100）、`birthday_max_discount`、`monthly_max_discount`（账务最小单位，0 表示不封顶）。出现该对象时，省略的数量和封顶为 0、文本为空、开关为 false，生日折扣默认 90；重复发放安全箱须同时启用安全箱。升级陪玩券仍须通过 `companion_service_ids` 绑定可用服务，安全箱券须绑定 `safe_box_service_ids`。未提供此对象的 1–6 级沿用六级初始化模板（生日券 90 折及两类券封顶 10000 账务单位）；更高等级无隐含发放权益。初始化模板不是新门店必须采用的权益配置。

生日券每个账号和业务周期在生日自然年最多发放一次；`birthday_valid_days` 控制自生日当天零点起的有效天数（1–365，默认 1）。`birthday_multiplier` 控制生日当天支付的本金消费积分倍率（1–10，新显式配置默认 1）；乘以付款时的 VIP 积分倍率并保存付款快照。旧六级模板继续使用本店全局生日倍率。`birthday_reward_id` 可绑定本店启用的生日礼物项目，`birthday_reward_quantity` 配置份数（0–100，默认 0）；已发券的折扣、封顶和有效期，以及已创建的奖励交付记录不因后续配置变化改写。

月券记录每个门店账号、业务周期和月份的一次发放决定，包括零张；记录保存当时等级、数量及折扣。月中升级不会补发；等级变化冻结已有券条款，下一次发放按新等级配置。升级权益的 `MemberBenefitGrant.details` 保存创建时 `benefits`，实际券和交付记录保存各自条款；退款降级保留已发券与未交付权益。

自定义权益和第 7 级以上需要会员门槛重算、条款保护及月券决策功能全部启用，并有本店月券决策边界。首次初始化空业务库时，在这些功能已启用的情况下自动创建边界；已有老板、消费或券记录的数据库不会自动推测历史发放。功能开关关闭时沿用原路径，不能将本文件视为这些功能已对外开放。
