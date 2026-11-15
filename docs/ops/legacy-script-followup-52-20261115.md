# 52 个旧脚本的混合调用复核（2026-11-15）

本轮逐项检查上一轮保留的 52 个脚本，删除 1 个只用于退役老板端的展示脚本；其余 51 个仍包含业务操作、有效断言或跨端同步，保留原文件及调用。未开发或运行功能测试。

## 本轮拆除

- `scripts/native-quick-double-message-live.cjs`：移除 `test_quick_double_message_browser.py` 中的旧老板原生页面步骤，保留消息记录查找、陪玩端步骤和后续订单、报单及资金断言。旧页面的跳转及展示覆盖已移除，没有新增当前老板端覆盖。
- 既有 `services/backend/orders/tests/test_provider_assignment_sound_network.py` 中 `test_quick_double_dispatch_notifies_boss_of_both_providers` 已断言同一派单消息包括两名陪玩及订单参与人，`test_selection_notice_workflow.py` 另有前缀和消息内容断言；本轮未运行这些测试。

## 尚需替换的 51 个脚本

这些脚本都仍引用已移除的旧端，不能用于声称当前端已通过验收。保留的原因不是它们可直接执行，而是调用方仍依赖其操作或断言；完成替换需要另行编写并验证覆盖，本轮按要求不进行功能测试开发。

| 脚本 | 保留原因 | 调用方 |
| --- | --- | --- |
| `admin-chat-order-lookup-live.cjs` | 会话已读、订单号识别、权限与订单查询断言混合 | `services/backend/chat/test_admin_order_lookup_http.py:61`<br>`services/backend/chat/test_provider_frontend_http.py:69` |
| `admin-dashboard-staff-queue-375-live.cjs` | 后台筛选和派单操作驱动后续陪玩流程 | `services/backend/orders/tests/test_dashboard_staff_shortcut_browser.py:64` |
| `admin-dashboard-withdraw-mobile-live.cjs` | 后台认领和审核提现驱动后续资金断言 | `services/backend/console/test_dashboard_withdraw_browser.py:86` |
| `admin-double-report-approve-375-live.cjs` | 报单驳回、通过动作驱动订单及结算断言 | `services/backend/orders/tests/test_checkout_provider_status_live.py:152` |
| `admin-order-cancel-prompt-live.cjs` | 取消确认与其他角色的订单状态和实时事件交错 | `services/backend/orders/tests/test_admin_cancel_prompt_live.py:64` |
| `admin-provider-chat-image-live.cjs` | 客服图片与会话页面核验，存在多处调用 | `services/backend/chat/test_provider_frontend_http.py:74`<br>`services/backend/chat/test_provider_frontend_http.py:127` |
| `admin-reservation-convert-conflict-live.cjs` | 预约确认、拒绝和并发冲突操作驱动状态断言 | `services/backend/orders/tests/test_reservation_dialog_live_browser.py:93`<br>`services/backend/orders/tests/test_reservation_dialog_live_browser.py:100`<br>`services/backend/orders/tests/test_reservation_dialog_live_browser.py:105`<br>`services/backend/orders/tests/test_reservation_dialog_live_browser.py:110` |
| `admin-reward-identity-375-live.cjs` | 奖罚操作生成钱包流水，后续断言依赖其结果 | `services/backend/orders/tests/test_checkout_provider_status_live.py:417` |
| `admin-timeout-dashboard-live.cjs` | 与老板、陪玩页面并行同步的超时看板 | `services/backend/orders/tests/test_provider_timeout_live_page.py:95` |
| `admin-wallet-recharge-375-live.cjs` | 钱包身份、充值记录和界面操作断言混合 | `services/backend/orders/tests/test_checkout_provider_status_live.py:392` |
| `native-account-only-evaluation-live.cjs` | 账号评价页面操作与订单评价状态交错 | `services/backend/orders/tests/test_account_only_native_reservation.py:67`<br>`services/backend/orders/tests/test_account_only_native_reservation.py:115` |
| `native-aftersale-concurrent-live.cjs` | 售后请求及并发竞争驱动真实接口结果 | `services/backend/orders/tests/test_account_only_native_reservation.py:57`<br>`services/backend/orders/tests/test_native_aftersale_concurrent_http.py:31` |
| `native-boss-refund-realtime-live.cjs` | 退款动作与老板页面、实时事件共同驱动结果 | `services/backend/orders/tests/test_admin_cancel_prompt_live.py:139`<br>`services/backend/orders/tests/test_admin_cancel_prompt_live.py:162` |
| `native-boss-transfer-realtime-live.cjs` | 后台转单请求与老板页面实时结果共同核对 | `services/backend/orders/tests/test_admin_cancel_prompt_live.py:192` |
| `native-browse-gift-live.cjs` | 赠礼操作产生礼物订单及钱包流水 | `services/backend/orders/tests/test_team_gift_workflow.py:91` |
| `native-chat-read-scope-live.cjs` | 会话读取与消息已读范围和分页断言混合 | `services/backend/chat/test_boss_read_scope_http.py:54` |
| `native-checkin-order-realtime.cjs` | 签到门槛配置、订单和页面实时更新交错 | `services/backend/users/test_native_checkin_order_realtime.py:109` |
| `native-checkout-confirm-team-live.cjs` | 双陪确认与提交入口断言依赖旧页面 | `services/backend/orders/tests/test_native_checkout_service_refresh.py:45` |
| `native-checkout-coupon-live.cjs` | 优惠券配置变动、结算页刷新及订单操作交错 | `services/backend/orders/tests/test_native_checkout_coupon_refresh.py:42`<br>`services/backend/orders/tests/test_native_checkout_coupon_refresh.py:118`<br>`services/backend/orders/tests/test_native_checkout_coupon_refresh.py:149` |
| `native-checkout-double-status-live.cjs` | 陪玩状态切换驱动双陪结算页更新 | `services/backend/orders/tests/test_checkout_provider_status_live.py:48` |
| `native-checkout-package-change-live.cjs` | 套餐配置变更驱动已打开结算页状态 | `services/backend/orders/tests/test_native_checkout_service_refresh.py:131`<br>`services/backend/orders/tests/test_native_checkout_service_refresh.py:161` |
| `native-checkout-provider-status-live.cjs` | 陪玩状态变更和下单页反馈依赖同一流程 | `services/backend/orders/tests/test_checkout_provider_status_live.py:454` |
| `native-checkout-requirement-change-live.cjs` | 服务资料要求变更驱动结算页刷新 | `services/backend/orders/tests/test_native_checkout_service_refresh.py:74` |
| `native-checkout-service-live.cjs` | 服务选择、报价与下单结果依赖旧页面 | `services/backend/orders/tests/test_native_checkout_service_refresh.py:192` |
| `native-checkout-zero-breakdown-live.cjs` | 零价订单配置与结算金额明细断言混合 | `services/backend/orders/tests/test_native_checkout_coupon_refresh.py:84` |
| `native-checkout-zero-price-change-live.cjs` | 价格调整及零价报价刷新依赖旧页面 | `services/backend/orders/tests/test_native_checkout_coupon_refresh.py:182` |
| `native-coupon-admin-realtime.cjs` | 优惠券管理请求与实时更新、数据库断言相连 | `services/backend/coupons/test_coupon_availability_workflow.py:1286` |
| `native-coupon-claim-race-live.cjs` | 跨账号领券并发及结果断言依赖旧页面 | `services/backend/coupons/test_native_claim_race_http.py:38` |
| `native-coupon-live-driver.cjs` | 优惠券变更、页面请求和后续结果断言相连 | `services/backend/coupons/test_coupon_availability_workflow.py:1409` |
| `native-coupon-return-realtime.cjs` | 订单退款、券返还与实时页面状态交错 | `services/backend/coupons/test_coupon_availability_workflow.py:1317` |
| `native-coupon-vip-realtime.cjs` | VIP 配置、发券及页面实时更新交错 | `services/backend/coupons/test_coupon_availability_workflow.py:1165` |
| `native-gameplay-picker-live.cjs` | 玩法选择和陪玩筛选结果依赖旧页面 | `services/backend/users/test_gameplay_picker_workflow.py:163`<br>`services/backend/users/test_gameplay_picker_workflow.py:210` |
| `native-membership-empty-real.cjs` | 空等级会员页面及真实接口结果断言 | `services/backend/engagement/test_account_only_vip_native_realtime.py:34` |
| `native-provider-tags-realtime.cjs` | 陪玩标签编辑、过滤和实时页面更新交错 | `services/backend/engagement/test_provider_tag_boss_realtime.py:69`<br>`services/backend/engagement/test_provider_tag_boss_realtime.py:103`<br>`services/backend/engagement/test_provider_tag_boss_realtime.py:132` |
| `native-reservation-live.cjs` | 预约分页、提交和其他角色接口动作混合 | `services/backend/orders/tests/test_account_only_native_reservation.py:32`<br>`services/backend/orders/tests/test_native_reservation_http.py:50`<br>`services/backend/orders/tests/test_reservation_pagination_live.py:62` |
| `native-reservation-notice-live.cjs` | 消息读取、预约状态和通知目标断言混合，多处调用 | `services/backend/console/test_message_account_only_live.py:48`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:215`<br>`services/backend/orders/tests/test_reservation_notices_live.py:205`<br>`services/backend/orders/tests/test_reservation_notices_live.py:220` |
| `native-reservation-recharge-live.cjs` | 预约转单、充值及钱包余额关联验证 | `services/backend/orders/tests/test_native_reservation_recharge_live.py:61` |
| `native-reservation-refresh-live.cjs` | 预约状态变更、转单与页面实时刷新交错 | `services/backend/orders/tests/test_reservation_refresh_live.py:83` |
| `native-subsidy-aftersale-real.cjs` | 免费权益售后发起及退款展示，驱动后续售后断言 | `services/backend/coupons/test_subsidy_ui_live.py:53`<br>`services/backend/coupons/test_subsidy_ui_live.py:86`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:67` |
| `native-team-evaluations-live.cjs` | 双陪评价提交产生后续评价与通知数据 | `services/backend/orders/tests/test_team_evaluations_workflow.py:69` |
| `native-team-gift-live.cjs` | 双陪礼物提交产生后续订单和资金断言 | `services/backend/orders/tests/test_team_gift_workflow.py:65` |
| `native-timeout-open-live.cjs` | 老板页面与陪玩、后台超时实时流程同步 | `services/backend/orders/tests/test_provider_timeout_live_page.py:89` |
| `native-vip-confirmation-realtime.cjs` | 会员实销确认、结算及页面实时刷新交错 | `services/backend/engagement/test_account_only_vip_native_realtime.py:72` |
| `negotiated-refund-three-role-realtime.cjs` | 三端协商退款、实时状态和资金流程交错 | `services/backend/orders/tests/test_negotiated_three_role_realtime.py:85`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:164`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:195`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:244`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:301`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:377` |
| `provider-chat-mobile-375-live.cjs` | 陪玩聊天发送、图片及失败重试产生后续消息断言 | `services/backend/chat/test_provider_frontend_http.py:122` |
| `provider-double-selected-375-live.cjs` | 陪玩进度展示和老板页面检查混合 | `services/backend/orders/tests/test_checkout_provider_status_live.py:74`<br>`services/backend/orders/tests/test_checkout_provider_status_live.py:98` |
| `provider-negotiated-aftersale-hold-live.cjs` | 协商售后期间老板与陪玩实时状态共同核对 | `services/backend/orders/tests/test_negotiated_three_role_realtime.py:51` |
| `provider-order-chat-context-live.cjs` | 陪玩发送订单咨询，后续依赖实际聊天记录 | `services/backend/chat/test_provider_frontend_http.py:61` |
| `provider-report-time-375.cjs` | 报单截图上传、提交和时间字段驱动审核断言 | `services/backend/orders/tests/test_checkout_provider_status_live.py:165` |
| `provider-vue-reject-regrab-live.cjs` | 接单、拒单、重抢及跨端反馈混合 | `services/backend/orders/tests/test_provider_reject_confirm_browser.py:92` |
| `provider-vue-reject-regrab-realtime.cjs` | 拒单重抢与老板、陪玩实时消息同步 | `services/backend/orders/tests/test_provider_reject_confirm_browser.py:92` |

## 静态检查与边界

只检查文件引用、Python 语法和文档路径；没有运行功能测试或启动服务。原 Vue／原生页面测试步骤不等于当前 React 端覆盖，历史覆盖的删除和未来替换应分别记录。
