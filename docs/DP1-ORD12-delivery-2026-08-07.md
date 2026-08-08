# DP-1 押金退还 + ORD-1/2 预约 — 交付说明

> 交付日期：2026-08-07 ｜ 范围：仅后端 `services/backend/` ｜ 状态：**已实现、已验证、未提交**

## TL;DR

补齐两个后端功能缺口：运营后台可按指定金额退还陪玩押金；顾客可预约陪玩未来时段并在落库时做时间冲突检测，确认后转为正式订单。13 个文件改动，729 个测试全绿。

**上线前必须先跑历史押金回补脚本**，否则老陪玩的押金退不出来（详见「遗留风险 1」）。

---

## 交付内容

### DP-1 押金退还

运营后台端点，支持退还指定金额（含全额），用于陪玩注销/封禁场景。

- 端点：`POST /api/admin/escorts/{id}/deposit-refund/`
- 权限点：`escort:deposit_refund`
- 账务口径：沿用 `DEPOSIT` / `DEPOSIT_INCOME` 两个枚举，**不新开 `DEPOSIT_REFUND`**，保持恒等式 `sum(DEPOSIT) == -sum(DEPOSIT_INCOME) == -当前在缴押金总额`，对账脚本一条聚合走到底。
- 在途订单只做**软提示**（响应回 `active_order_count`），不硬拦 —— 押金与产能是两码事，硬拦会让「退完押金再下线」变成死循环。

### ORD-1/2 预约

新建 `Reservation` 独立实体，与 `Order` 解耦，不改动 `Order` 模型。

- 7 条路由：创建 / 列表 / 详情 / 取消 / 确认 / 拒绝 / 转单
- 冲突判定左闭右开 `[start, end)`：端点相接放行，重叠 / 包含 / 被包含 / 左右相交拒绝，终态已释放放行
- 并发安全走 `EscortProfile.select_for_update()` 悲观锁（MySQL 不支持 Postgres 的 `tstzrange` + `ExclusionConstraint`，时间重叠只能靠行锁 + 查询）
- 状态机收敛到 `orders/reservation_state_machine.py`，禁止 View 散写状态判断

---

## 文件清单

**新增（9）**

| 文件 | 说明 |
|---|---|
| `console/deposit_refund.py` | DP-1 退还唯一实现 |
| `orders/reservation_state_machine.py` | 预约状态机白名单 |
| `orders/reservation_services.py` | 时间校验 + 冲突检测 + 创建 + 转单 |
| `orders/reservation_serializers.py` | 预约序列化器 |
| `orders/reservation_views.py` | 7 条端点 |
| `orders/migrations/0026_reservation_and_status_log.py` | 建表迁移 |
| `wallet/test_deposit_refund.py` | 退还测试 20 例 |
| `orders/tests/test_reservation_conflict.py` | 冲突测试 32 例 |
| `orders/tests/test_reservation_api.py` | API 测试 43 例 |

**修改（4）**：`console/permissions.py`（+权限点）、`console/views.py`（+`EscortViewSet.deposit_refund`）、`orders/models.py`（追加 `Reservation` / `ReservationStatusLog`，不动 `Order`）、`orders/urls.py`（+6 路由）

---

## 验证结论

以下为主理人独立复核结果（非转述实现方自测）。

**已验证通过**

| 项 | 证据 |
|---|---|
| 锁序：业务方在前、平台在后 | `deposit_refund.py` `EscortProfile`:125 → 陪玩钱包:131 → 平台钱包:134 |
| 恒等式成立、未新开枚举 | 陪玩 `DEPOSIT` +amount:186 ／ 平台 `DEPOSIT_INCOME` -amount:200 |
| 四件事同生共死 | 同一 `transaction.atomic()`:119 |
| 两段式幂等 | `peek`:105（事务外、状态校验前）→ `claim`:152（校验后、动钱:173 前） |
| 五道额度护栏 | 金额≤0:93 ／ 无押金:137 ／ 超已缴:139 ／ 平台总余额:144 ／ 押金池专款:147 |
| 转单无重复占键、无重复扣款 | 幂等键只占一次 `scope='reservation.convert'` `reservation_services.py:403` |
| 无「用券不核销」资金漏洞 | `user_coupon_id` 可选字段未传 → `coupon_discount=0`（`serializers.py:349`） |
| 迁移与模型无漂移 | 默认 settings 下 `makemigrations --check` → No changes detected |
| 全量测试 | `Ran 729 tests — OK`（独立复跑） |
| 改动范围 | 13 文件全在 `services/backend/`，`apps/boss-miniapp/` 零触碰 |

**设计亮点**：`_platform_deposit_pool`（`deposit_refund.py:59-71`）只按 `DEPOSIT_INCOME` 聚合而非取平台钱包总余额，确保押金池专款专用，退押金不会悄悄花掉平台的抽成收入。

---

## 遗留风险

### 1. 🔴 上线前置依赖 — 必须先跑回补脚本

`deposit_refund.py:147` 的护栏② 按 `Sum(DEPOSIT_INCOME)` 计算平台押金池，池子不足即拒退。

生产库存在历史欠账：早期押金缴纳记录缺少对应的平台 `DEPOSIT_INCOME` 流水，`backfill_deposit_platform_income.py --commit` 至今未在 MySQL 环境执行。

**后果**：老陪玩退押金时会被误判为「平台押金账户余额不足」而失败，新陪玩正常。

**处置**：回补脚本必须先于退还功能上线。护栏逻辑本身正确，不需要改代码。

### 2. 🟡 0026 迁移从未被真实执行

`config/test_settings.py:26` 的 `MIGRATION_MODULES = {app: None}` 让测试库按 models 直接建表，因此跑多少测试都不会验证 `migrate` 路径本身，也不验证 `CheckConstraint` 是否真正生效。

**处置**：在有 MySQL 的环境跑一次 `migrate`，并反向验证约束（插入非法值必须被拒绝）。

### 3. 🟡 转单扣款是复制而非复用

`convert_reservation_to_order`（`reservation_services.py:421-460`）只借用 `CreateOrderSerializer` 做定价校验，扣款 / 建单 / 流水是自己写的一份副本，与 `CreateOrderView`（`views.py:464-506`）并行存在。

当下两边账务同构，但将来修改 C 端建单字段或分账逻辑时容易漏改这一条，属静默漂移风险。**改 C 端下单时请同步检查此处。**

### 4. 🟢 功能限制（非缺陷）

- 转单不支持优惠券与 `support_contact`（客服卡片）
- 预约创建不校验 `MAX_CONCURRENT_ORDERS`。注意 C 端 `CreateOrderView` 直接指定陪玩时本来也不查（查 MAX 的只有抢单、后台派单、接单池可见性三处），故此为沿袭既有行为，非本次引入

---

## 本期未做

- 前端 UI（小程序预约页、后台退还操作页）
- P1 范围：可用性查询接口、console 预约管理、`EXPIRED` 自动过期、Django Admin 注册
- 封禁 / 注销时自动触发退还
- 在途订单硬拦退还（当前为软提示）
