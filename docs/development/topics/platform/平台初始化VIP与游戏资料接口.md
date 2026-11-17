# 平台门店初始化：VIP 和游戏资料

平台独立账号须具有 `stores:manage`，从统一后台平台接口访问。所有目标 ID、版本和配置均属于路径指定门店；操作保留平台意图／结果及门店审计。跨库结果不明确时按接口返回重新读取，不能假设已回滚。

`GET /api/admin/platform/store-management/{store_id}/setup` 返回 `membership.vip_enabled`、`membership.version` 和 `account_field_targets`。VIP 开关与预存／VIP 等级共用会员配置版本。

`PUT /api/admin/platform/store-management/{store_id}/setup/membership/vip-switch` 请求体为 `{"version":"读取所得版本","id":null,"configuration":{"vip_enabled":false}}`。该入口只适用于首次开业前、尚无老板账号的门店；已运营门店使用本店会员配置流程，保留既有权益和重算规则。

`GET /api/admin/platform/store-management/{store_id}/setup/account-fields/{scope}/{target_id}` 读取游戏或服务的版本和字段；`scope` 为 `game` 或 `service`。PUT 形如 `{"version":0,"fields":["game_region","game_nickname"]}`，字段还可选 `game_uid`。服务的 `fields: null` 表示继承游戏配置；空数组表示明确不强制填写。版本变化时应重新读取并确认。

开业检查使用本店已保存的服务资料规则；预约未配置视为未启用，已配置才须确认。积分使用实际启用时须有有效整组抵扣规则；关闭使用时不强制。相关接口和开业流程尚待最终联调。

同一会员初始化入口支持 `kind=vip-settings`：`{"version":"读取所得版本","id":null,"configuration":{"base_points_rate":100,"birthday_multiplier":2}}`。两个字段可分别填写，使用本店会员配置现有校验与同一乐观版本；该写入同样仅允许未开业且无老板账号。积分抵扣未配置且使用开关未明确启用时视为暂不可用，不因此阻止开业；明确启用但缺规则时仍需先补齐。
