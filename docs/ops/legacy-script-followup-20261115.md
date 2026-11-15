# 66 个旧脚本混合调用复核（2026-11-15）

> 本文为当时的核对快照，后续处理见 [52 个旧脚本的混合调用复核](legacy-script-followup-52-20261115.md)。

在[上一轮核对](legacy-script-audit-20261115.md)基础上，逐项核查剩余 66 个脚本和现有调用。仅拆除已退役页面的展示或操作步骤，保留调用方中已有的业务断言和当前端调用；没有开发或运行功能测试。

本轮删除 14 个旧脚本，剩余 52 个。删除的是旧原生或旧 Vue 页面的覆盖，未为这些页面步骤开发新的当前端测试；不能把它们视为已有替代覆盖。保留脚本仍引用已经移除的旧端，相关混合调用链暂不可作为当前验收依据。

## 已删除的 14 个脚本

| 脚本 | 处理及保留范围 |
| --- | --- |
| `admin-chat-order-mobile-live.cjs` | 移除已退役后台页面的可选浏览器步骤；订单和会话接口断言保留。 |
| `admin-double-income-transactions-375-live.cjs` | 移除旧后台流水展示步骤；订单收益和流水查询保留。 |
| `admin-double-progress-375-live.cjs` | 移除旧后台进度页步骤；履约进度接口断言和其他端调用保留。 |
| `admin-income-withdraw-375-live.cjs` | 移除旧后台提现页面分支，沿用原有的接口领取和审核分支。 |
| `admin-wallet-identity-375-live.cjs` | 移除旧后台钱包展示步骤；账户和流水查询断言保留。 |
| `native-banner-destination-check.cjs` | 移除旧页面轮播跳转展示步骤；商品和轮播可用性接口断言保留。 |
| `native-gift-naming-refund.cjs` | 移除旧冠名退款展示步骤；礼物、冠名和退款结果断言保留。 |
| `native-gift-refund-presentation.cjs` | 移除旧礼物退款展示步骤；退款、收益、通知及其他端调用保留。 |
| `native-negotiated-referral-wallet.cjs` | 移除旧钱包推荐收益展示步骤；钱包和佣金流水断言保留。 |
| `native-order-price-breakdown.cjs` | 移除旧订单页面展示步骤；订单详情、钱包与优惠券退款断言保留。 |
| `native-referral-wallet-live.cjs` | 移除旧钱包页面渲染步骤；推荐佣金流水断言保留。 |
| `native-reservation-display-live.cjs` | 移除旧预约页面展示步骤；预约通知与接口状态断言保留。 |
| `native-unpaid-timeout-display.cjs` | 移除旧订单页面展示步骤；取消状态、退款流水与通知断言保留。 |
| `provider-tag-vue-review-live.cjs` | 移除旧 Vue 页面分支，沿用该调用方原有的接口审核分支及状态断言。 |

## 仍需整理的 52 个脚本

下表列出当前可执行文件中的直接调用位置。这些脚本可能承载真实接口动作、会话同步、或当前端与旧端交错的场景；直接删除会使现有调用链失效。后续需要先有可用的当前端替代并单独验证相应覆盖。

| 脚本 | 当前直接调用方 |
| --- | --- |
| `admin-chat-order-lookup-live.cjs` | `services/backend/chat/test_admin_order_lookup_http.py:61`<br>`services/backend/chat/test_provider_frontend_http.py:69` |
| `admin-dashboard-staff-queue-375-live.cjs` | `services/backend/orders/tests/test_dashboard_staff_shortcut_browser.py:64` |
| `admin-dashboard-withdraw-mobile-live.cjs` | `services/backend/console/test_dashboard_withdraw_browser.py:86` |
| `admin-double-report-approve-375-live.cjs` | `services/backend/orders/tests/test_checkout_provider_status_live.py:152` |
| `admin-order-cancel-prompt-live.cjs` | `services/backend/orders/tests/test_admin_cancel_prompt_live.py:64` |
| `admin-provider-chat-image-live.cjs` | `services/backend/chat/test_provider_frontend_http.py:74`<br>`services/backend/chat/test_provider_frontend_http.py:127` |
| `admin-reservation-convert-conflict-live.cjs` | `services/backend/orders/tests/test_reservation_dialog_live_browser.py:93`<br>`services/backend/orders/tests/test_reservation_dialog_live_browser.py:100`<br>`services/backend/orders/tests/test_reservation_dialog_live_browser.py:105`<br>`services/backend/orders/tests/test_reservation_dialog_live_browser.py:110` |
| `admin-reward-identity-375-live.cjs` | `services/backend/orders/tests/test_checkout_provider_status_live.py:417` |
| `admin-timeout-dashboard-live.cjs` | `services/backend/orders/tests/test_provider_timeout_live_page.py:95` |
| `admin-wallet-recharge-375-live.cjs` | `services/backend/orders/tests/test_checkout_provider_status_live.py:392` |
| `native-account-only-evaluation-live.cjs` | `services/backend/orders/tests/test_account_only_native_reservation.py:67`<br>`services/backend/orders/tests/test_account_only_native_reservation.py:115` |
| `native-aftersale-concurrent-live.cjs` | `services/backend/orders/tests/test_account_only_native_reservation.py:57`<br>`services/backend/orders/tests/test_native_aftersale_concurrent_http.py:31` |
| `native-boss-refund-realtime-live.cjs` | `services/backend/orders/tests/test_admin_cancel_prompt_live.py:139`<br>`services/backend/orders/tests/test_admin_cancel_prompt_live.py:162` |
| `native-boss-transfer-realtime-live.cjs` | `services/backend/orders/tests/test_admin_cancel_prompt_live.py:192` |
| `native-browse-gift-live.cjs` | `services/backend/orders/tests/test_team_gift_workflow.py:91` |
| `native-chat-read-scope-live.cjs` | `services/backend/chat/test_boss_read_scope_http.py:54` |
| `native-checkin-order-realtime.cjs` | `services/backend/users/test_native_checkin_order_realtime.py:109` |
| `native-checkout-confirm-team-live.cjs` | `services/backend/orders/tests/test_native_checkout_service_refresh.py:45` |
| `native-checkout-coupon-live.cjs` | `services/backend/orders/tests/test_native_checkout_coupon_refresh.py:42`<br>`services/backend/orders/tests/test_native_checkout_coupon_refresh.py:118`<br>`services/backend/orders/tests/test_native_checkout_coupon_refresh.py:149` |
| `native-checkout-double-status-live.cjs` | `services/backend/orders/tests/test_checkout_provider_status_live.py:48` |
| `native-checkout-package-change-live.cjs` | `services/backend/orders/tests/test_native_checkout_service_refresh.py:131`<br>`services/backend/orders/tests/test_native_checkout_service_refresh.py:161` |
| `native-checkout-provider-status-live.cjs` | `services/backend/orders/tests/test_checkout_provider_status_live.py:454` |
| `native-checkout-requirement-change-live.cjs` | `services/backend/orders/tests/test_native_checkout_service_refresh.py:74` |
| `native-checkout-service-live.cjs` | `services/backend/orders/tests/test_native_checkout_service_refresh.py:192` |
| `native-checkout-zero-breakdown-live.cjs` | `services/backend/orders/tests/test_native_checkout_coupon_refresh.py:84` |
| `native-checkout-zero-price-change-live.cjs` | `services/backend/orders/tests/test_native_checkout_coupon_refresh.py:182` |
| `native-coupon-admin-realtime.cjs` | `services/backend/coupons/test_coupon_availability_workflow.py:1286` |
| `native-coupon-claim-race-live.cjs` | `services/backend/coupons/test_native_claim_race_http.py:38` |
| `native-coupon-live-driver.cjs` | `services/backend/coupons/test_coupon_availability_workflow.py:1409` |
| `native-coupon-return-realtime.cjs` | `services/backend/coupons/test_coupon_availability_workflow.py:1317` |
| `native-coupon-vip-realtime.cjs` | `services/backend/coupons/test_coupon_availability_workflow.py:1165` |
| `native-gameplay-picker-live.cjs` | `services/backend/users/test_gameplay_picker_workflow.py:163`<br>`services/backend/users/test_gameplay_picker_workflow.py:210` |
| `native-membership-empty-real.cjs` | `services/backend/engagement/test_account_only_vip_native_realtime.py:34` |
| `native-provider-tags-realtime.cjs` | `services/backend/engagement/test_provider_tag_boss_realtime.py:69`<br>`services/backend/engagement/test_provider_tag_boss_realtime.py:103`<br>`services/backend/engagement/test_provider_tag_boss_realtime.py:132` |
| `native-quick-double-message-live.cjs` | `services/backend/orders/tests/test_quick_double_message_browser.py:57` |
| `native-reservation-live.cjs` | `services/backend/orders/tests/test_account_only_native_reservation.py:32`<br>`services/backend/orders/tests/test_native_reservation_http.py:50`<br>`services/backend/orders/tests/test_reservation_pagination_live.py:62` |
| `native-reservation-notice-live.cjs` | `services/backend/console/test_message_account_only_live.py:48`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:215`<br>`services/backend/orders/tests/test_reservation_notices_live.py:205`<br>`services/backend/orders/tests/test_reservation_notices_live.py:220` |
| `native-reservation-recharge-live.cjs` | `services/backend/orders/tests/test_native_reservation_recharge_live.py:61` |
| `native-reservation-refresh-live.cjs` | `services/backend/orders/tests/test_reservation_refresh_live.py:83` |
| `native-subsidy-aftersale-real.cjs` | `services/backend/coupons/test_subsidy_ui_live.py:53`<br>`services/backend/coupons/test_subsidy_ui_live.py:86`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:67` |
| `native-team-evaluations-live.cjs` | `services/backend/orders/tests/test_team_evaluations_workflow.py:69` |
| `native-team-gift-live.cjs` | `services/backend/orders/tests/test_team_gift_workflow.py:65` |
| `native-timeout-open-live.cjs` | `services/backend/orders/tests/test_provider_timeout_live_page.py:89` |
| `native-vip-confirmation-realtime.cjs` | `services/backend/engagement/test_account_only_vip_native_realtime.py:72` |
| `negotiated-refund-three-role-realtime.cjs` | `services/backend/orders/tests/test_negotiated_three_role_realtime.py:85`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:164`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:195`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:244`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:301`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py:377` |
| `provider-chat-mobile-375-live.cjs` | `services/backend/chat/test_provider_frontend_http.py:122` |
| `provider-double-selected-375-live.cjs` | `services/backend/orders/tests/test_checkout_provider_status_live.py:74`<br>`services/backend/orders/tests/test_checkout_provider_status_live.py:98` |
| `provider-negotiated-aftersale-hold-live.cjs` | `services/backend/orders/tests/test_negotiated_three_role_realtime.py:51` |
| `provider-order-chat-context-live.cjs` | `services/backend/chat/test_provider_frontend_http.py:61` |
| `provider-report-time-375.cjs` | `services/backend/orders/tests/test_checkout_provider_status_live.py:165` |
| `provider-vue-reject-regrab-live.cjs` | `services/backend/orders/tests/test_provider_reject_confirm_browser.py:92` |
| `provider-vue-reject-regrab-realtime.cjs` | `services/backend/orders/tests/test_provider_reject_confirm_browser.py:92` |

## 验证边界

本轮只进行静态调用、语法结构和文档链接核对，未启动业务测试、开发服务或部署。静态结果不能证明业务流程已经通过。
