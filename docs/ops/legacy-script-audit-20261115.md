# 旧端脚本与调用方核对（2026-11-15）

> 本文是第一轮核对快照；其后对保留脚本的逐项处理见[混合调用复核](legacy-script-followup-20261115.md)。

逐项核对先前列出的 132 个脚本：检查其旧端路径、仓库调用者和测试模块。只删除已失效旧端脚本及完全依赖它们、没有其他仓库调用者的旧端测试模块。未运行或开发业务功能测试。

本轮删除 66 个旧端脚本、45 个仅依赖旧端的测试模块（共 55 个测试方法），以及一个仅调用旧端测试工具的脚本。保留的 66 个脚本仍被混合测试或其他代码引用；不能将这些测试或其中有效的业务断言一并删除。
已删除的旧页面测试不再提供覆盖，当前三端对应场景是否已有替代覆盖未在本次核对。历史文档和 JSON 中的旧命令与结果保留为当时的记录，不是当前可运行入口。

| 脚本 | 处理 | 原调用方 |
| --- | --- | --- |
| `admin-aftersale-history-mobile-live.cjs` | 删除 | `services/backend/orders/tests/test_admin_aftersale_history_browser.py`<br>`services/backend/orders/tests/test_legacy_team_warning_browser.py` |
| `admin-aftersale-queue-mobile-live.cjs` | 删除 | `services/backend/orders/tests/test_admin_aftersale_history_browser.py` |
| `admin-application-realtime-375.cjs` | 删除 | `services/backend/orders/tests/test_admin_application_realtime.py` |
| `admin-banner-status-live.cjs` | 删除 | `services/backend/banners/test_native_live.py` |
| `admin-chat-drafts-mobile-live.cjs` | 删除 | `services/backend/chat/test_staff_drafts_mobile_http.py` |
| `admin-chat-history-scroll-live.cjs` | 删除 | `services/backend/chat/test_admin_history_scroll_http.py` |
| `admin-chat-order-lookup-live.cjs` | 保留：仍被混合调用 | `services/backend/chat/test_admin_order_lookup_http.py`<br>`services/backend/chat/test_order_context_frontend_http.py`<br>`services/backend/chat/test_provider_frontend_http.py` |
| `admin-chat-order-mobile-live.cjs` | 保留：仍被混合调用 | `services/backend/chat/test_admin_order_lookup_http.py` |
| `admin-chat-read-live.cjs` | 删除 | `services/backend/chat/test_chat_read_frontend_http.py` |
| `admin-chat-sound-network-live.cjs` | 删除 | `services/backend/chat/test_admin_chat_sound_network.py` |
| `admin-claim-queue-realtime.cjs` | 删除 | `services/backend/engagement/test_claim_queue_browser_live.py` |
| `admin-dashboard-charts-live.cjs` | 删除 | `services/backend/console/test_dashboard_chart_browser.py` |
| `admin-dashboard-overdue-mobile-live.cjs` | 删除 | `services/backend/console/test_dashboard_overdue_browser.py` |
| `admin-dashboard-queue-realtime-live.cjs` | 删除 | `services/backend/console/test_dashboard_queue_realtime_browser.py` |
| `admin-dashboard-report-block-375-live.cjs` | 删除 | `services/backend/console/test_dashboard_report_block_browser.py` |
| `admin-dashboard-staff-queue-375-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_dashboard_staff_shortcut_browser.py` |
| `admin-dashboard-withdraw-mobile-live.cjs` | 保留：仍被混合调用 | `services/backend/console/test_dashboard_withdraw_browser.py` |
| `admin-double-income-transactions-375-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_checkout_provider_status_live.py` |
| `admin-double-progress-375-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_checkout_provider_status_live.py` |
| `admin-double-report-approve-375-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_checkout_provider_status_live.py` |
| `admin-income-withdraw-375-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_checkout_provider_status_live.py` |
| `admin-order-cancel-prompt-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_admin_cancel_prompt_live.py` |
| `admin-provider-chat-image-live.cjs` | 保留：仍被混合调用 | `services/backend/chat/test_provider_frontend_http.py` |
| `admin-recharge-wallet-live.cjs` | 删除 | `services/backend/orders/tests/test_native_recharge_wallet_realtime.py` |
| `admin-report-mobile-375-live.cjs` | 删除 | `services/backend/orders/tests/test_admin_report_mobile_375_http.py` |
| `admin-report-refund-review-live.cjs` | 删除 | `services/backend/console/test_report_refund_live_browser.py` |
| `admin-report-transfer-375-live.cjs` | 删除 | `services/backend/orders/tests/test_admin_report_mobile_375_http.py` |
| `admin-reservation-convert-conflict-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_reservation_dialog_live_browser.py` |
| `admin-reward-identity-375-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_checkout_provider_status_live.py` |
| `admin-sound-network-live.cjs` | 删除 | `services/backend/orders/tests/test_admin_sound_network.py` |
| `admin-team-progress-realtime.cjs` | 删除 | `services/backend/orders/tests/test_admin_team_progress_realtime.py` |
| `admin-timeout-dashboard-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_provider_timeout_live_page.py` |
| `admin-wallet-identity-375-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_checkout_provider_status_live.py` |
| `admin-wallet-recharge-375-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_checkout_provider_status_live.py` |
| `admin-withdraw-claim-conflict-live.cjs` | 删除 | `services/backend/console/test_withdraw_claim_conflict_browser.py` |
| `admin-withdraw-preflight-live.cjs` | 删除 | `services/backend/console/test_withdraw_preflight_http.py` |
| `admin-withdraw-release-live.cjs` | 删除 | `services/backend/console/test_withdraw_release_frontend_http.py` |
| `check-native.mjs` | 删除 | `docs/BOSS_NATIVE_DESIGN.md`<br>`docs/BOSS_NATIVE_PRODUCT.md`<br>`docs/MINIAPP_MEDIA_BUDGET_20260928.md`<br>`docs/RELEASE_PIPELINE_AUDIT_20260923.md`<br>`docs/RELEASE_READINESS.md`<br>`docs/history/README-before-cleanup-20261111.md`<br>`docs/ops/lottery-gacha-20261020/native-unit-verification.json`<br>`docs/ops/lottery-ten-guarantee-20261020/native-unit-verification.json`<br>`docs/订单派单三端状态与原型验收基线.md`<br>`scripts/test-release-config.mjs` |
| `check-release-config.mjs` | 删除 | `docs/BOSS_NATIVE_PRODUCT.md`<br>`docs/RELEASE_PIPELINE_AUDIT_20260923.md`<br>`docs/RELEASE_READINESS.md`<br>`docs/UPDATE_DEPLOYMENT_20261006.md`<br>`scripts/test-release-config.mjs` |
| `gift-realtime-browser-375.cjs` | 删除 | `services/backend/wallet/test_gift_live_browser_network.py` |
| `native-account-only-coupons-realtime.cjs` | 删除 | `services/backend/coupons/test_account_only_native_realtime.py` |
| `native-account-only-evaluation-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_account_only_native_reservation.py` |
| `native-aftersale-concurrent-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_account_only_native_reservation.py`<br>`services/backend/orders/tests/test_native_aftersale_concurrent_http.py` |
| `native-aftersale-lost-live.cjs` | 删除 | `services/backend/orders/tests/test_native_aftersale_lost_http.py` |
| `native-application-progress-live.cjs` | 删除 | `services/backend/orders/tests/test_native_application_progress_http.py` |
| `native-banner-destination-check.cjs` | 保留：仍被混合调用 | `services/backend/console/test_menu_operations.py` |
| `native-banner-live.cjs` | 删除 | `services/backend/banners/test_native_live.py` |
| `native-boss-messages-live.cjs` | 删除 | `services/backend/site_messages/test_boss_message_frontend_http.py` |
| `native-boss-refund-realtime-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_admin_cancel_prompt_live.py` |
| `native-boss-report-hold-live.cjs` | 删除 | `services/backend/orders/tests/test_boss_report_aftersale_hold_http.py` |
| `native-boss-transfer-realtime-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_admin_cancel_prompt_live.py` |
| `native-browse-gift-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_team_gift_workflow.py` |
| `native-chat-history-live.cjs` | 删除 | `services/backend/chat/test_chat_history_frontend_http.py` |
| `native-chat-order-context-live.cjs` | 删除 | `services/backend/chat/test_order_context_frontend_http.py` |
| `native-chat-read-live.cjs` | 删除 | `services/backend/chat/test_chat_read_frontend_http.py` |
| `native-chat-read-scope-live.cjs` | 保留：仍被混合调用 | `services/backend/chat/test_boss_read_scope_http.py` |
| `native-checkin-order-realtime.cjs` | 保留：仍被混合调用 | `services/backend/users/test_native_checkin_order_realtime.py` |
| `native-checkout-confirm-team-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_native_checkout_service_refresh.py` |
| `native-checkout-coupon-live.cjs` | 保留：仍被混合调用 | `services/backend/coupons/test_vip_checkout_realtime.py`<br>`services/backend/orders/tests/test_native_checkout_coupon_refresh.py` |
| `native-checkout-double-status-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_admin_team_progress_realtime.py`<br>`services/backend/orders/tests/test_checkout_provider_status_live.py` |
| `native-checkout-package-change-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_native_checkout_service_refresh.py` |
| `native-checkout-provider-status-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_checkout_provider_status_live.py` |
| `native-checkout-requirement-change-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_native_checkout_service_refresh.py` |
| `native-checkout-service-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_native_checkout_service_refresh.py` |
| `native-checkout-zero-breakdown-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_native_checkout_coupon_refresh.py` |
| `native-checkout-zero-price-change-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_native_checkout_coupon_refresh.py` |
| `native-claim-progress-live.cjs` | 删除 | `services/backend/engagement/test_native_claim_progress_live.py` |
| `native-claim-tag-expiry-live.cjs` | 删除 | `services/backend/engagement/test_native_claim_progress_live.py` |
| `native-coupon-admin-realtime.cjs` | 保留：仍被混合调用 | `services/backend/coupons/test_coupon_availability_workflow.py` |
| `native-coupon-claim-race-live.cjs` | 保留：仍被混合调用 | `services/backend/coupons/test_native_claim_race_http.py` |
| `native-coupon-live-driver.cjs` | 保留：仍被混合调用 | `services/backend/coupons/test_coupon_availability_workflow.py` |
| `native-coupon-return-realtime.cjs` | 保留：仍被混合调用 | `services/backend/coupons/test_coupon_availability_workflow.py` |
| `native-coupon-vip-realtime.cjs` | 保留：仍被混合调用 | `services/backend/coupons/test_coupon_availability_workflow.py` |
| `native-favorites-race-live.cjs` | 删除 | `services/backend/orders/tests/test_native_favorites_race_http.py` |
| `native-gameplay-picker-live.cjs` | 保留：仍被混合调用 | `services/backend/users/test_gameplay_picker_workflow.py` |
| `native-gift-naming-refund.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_gift_basket_refund_journey.py` |
| `native-gift-order-list-live.cjs` | 删除 | `services/backend/orders/tests/test_native_gift_order_list_http.py` |
| `native-gift-recovery-live.cjs` | 删除 | `services/backend/orders/tests/test_native_gift_recovery_http.py` |
| `native-gift-refund-presentation.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_team_gift_workflow.py` |
| `native-membership-empty-real.cjs` | 保留：仍被混合调用 | `services/backend/engagement/test_account_only_vip_native_realtime.py` |
| `native-negotiated-referral-wallet.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_negotiated_three_role_realtime.py` |
| `native-order-operation-session-live.cjs` | 删除 | `services/backend/orders/tests/test_native_order_operation_session_http.py` |
| `native-order-price-breakdown.cjs` | 保留：仍被混合调用 | `services/backend/coupons/test_coupon_availability_workflow.py` |
| `native-provider-tags-realtime.cjs` | 保留：仍被混合调用 | `services/backend/engagement/test_provider_tag_boss_realtime.py` |
| `native-qualification-cancel-live.cjs` | 删除 | `services/backend/orders/tests/test_qualification_cancel_notice.py` |
| `native-quick-double-message-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_quick_double_message_browser.py` |
| `native-recharge-wallet-realtime.cjs` | 删除 | `services/backend/orders/tests/test_native_recharge_wallet_realtime.py` |
| `native-referral-wallet-live.cjs` | 保留：仍被混合调用 | `services/backend/wallet/test_account_only_recharge.py` |
| `native-reservation-display-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_reservation_notices_live.py` |
| `native-reservation-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_account_only_native_reservation.py`<br>`services/backend/orders/tests/test_native_reservation_http.py`<br>`services/backend/orders/tests/test_reservation_pagination_live.py` |
| `native-reservation-notice-live.cjs` | 保留：仍被混合调用 | `services/backend/console/test_message_account_only_live.py`<br>`services/backend/engagement/test_account_only_claim_notice.py`<br>`services/backend/orders/tests/test_legacy_team_warning_browser.py`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py`<br>`services/backend/orders/tests/test_reservation_notices_live.py`<br>`services/backend/users/test_full_attendance_account_only.py` |
| `native-reservation-recharge-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_native_reservation_recharge_live.py` |
| `native-reservation-refresh-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_reservation_refresh_live.py` |
| `native-selection-lost-live.cjs` | 删除 | `services/backend/orders/tests/test_native_selection_response_lost_http.py` |
| `native-service-favorite-live.cjs` | 删除 | `services/backend/orders/tests/test_native_favorites_race_http.py` |
| `native-subsidy-aftersale-real.cjs` | 保留：仍被混合调用 | `services/backend/coupons/test_subsidy_ui_live.py`<br>`services/backend/orders/tests/test_negotiated_three_role_realtime.py` |
| `native-subsidy-realtime.cjs` | 删除 | `services/backend/coupons/test_subsidy_realtime_network.py` |
| `native-tag-return-live.cjs` | 删除 | `services/backend/engagement/test_native_claim_progress_live.py` |
| `native-team-evaluations-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_team_evaluations_workflow.py` |
| `native-team-gift-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_team_gift_workflow.py` |
| `native-team-progress-realtime.cjs` | 删除 | `services/backend/orders/tests/test_admin_team_progress_realtime.py` |
| `native-timeout-open-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_provider_timeout_live_page.py` |
| `native-unpaid-timeout-display.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_timeout_customer_notice.py` |
| `native-vip-confirmation-realtime.cjs` | 保留：仍被混合调用 | `services/backend/engagement/test_account_only_vip_native_realtime.py` |
| `native-wallet-pagination-realtime.cjs` | 删除 | `services/backend/wallet/test_native_wallet_pagination_realtime.py` |
| `negotiated-refund-three-role-realtime.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_negotiated_three_role_realtime.py` |
| `prepare-cloud-miniapp.py` | 删除 | `docs/MINIAPP_MEDIA_BUDGET_20260928.md`<br>`docs/WECHAT_FORMAL_SETUP_20260930.md` |
| `provider-chat-mobile-375-live.cjs` | 保留：仍被混合调用 | `services/backend/chat/test_provider_frontend_http.py` |
| `provider-double-selected-375-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_checkout_provider_status_live.py` |
| `provider-negotiated-aftersale-hold-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_negotiated_three_role_realtime.py` |
| `provider-order-chat-context-live.cjs` | 保留：仍被混合调用 | `services/backend/chat/test_provider_frontend_http.py` |
| `provider-points-realtime-live.cjs` | 删除 | `services/backend/engagement/test_provider_points_realtime_live.py` |
| `provider-report-refund-live-375.cjs` | 删除 | `services/backend/console/test_report_refund_live_browser.py` |
| `provider-report-time-375.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_admin_report_mobile_375_http.py`<br>`services/backend/orders/tests/test_checkout_provider_status_live.py`<br>`services/backend/orders/tests/test_provider_report_time_browser.py` |
| `provider-reservation-375-live.cjs` | 删除 | `services/backend/orders/tests/test_provider_reservation_375_browser.py` |
| `provider-tag-vue-review-live.cjs` | 保留：仍被混合调用 | `services/backend/engagement/test_provider_tag_journey.py` |
| `provider-vue-reject-regrab-live.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_provider_reject_confirm_browser.py` |
| `provider-vue-reject-regrab-realtime.cjs` | 保留：仍被混合调用 | `services/backend/orders/tests/test_provider_reject_confirm_browser.py` |
| `reservation-fields-http.cjs` | 删除 | `services/backend/orders/tests/test_native_reservation_fields_http.py` |
| `snap-spacing-scale.mjs` | 删除 | `scripts/check-native.mjs` |
| `test-admin-order-commission.cjs` | 删除 | `docs/ops/order-commission-mode-20261020/verification.json` |
| `test-admin-order-income-display.cjs` | 删除 | `docs/ops/order-commission-mode-20261020/verification.json` |
| `test-admin-refund-aftersale-realtime.cjs` | 删除 | `docs/ops/order-commission-mode-20261020/verification.json` |
| `test-auth-sessions.cjs` | 删除 | `docs/AUTH_SESSION_AUDIT_20260923.md`<br>`docs/LIVE_AUTHORIZATION_AUDIT_20260923.md`<br>`scripts/test-admin-silent-request.cjs` |
| `test-native-companion-selection.cjs` | 删除 | `docs/ops/multi-companion-selection-20261021/verification-summary.json`<br>`docs/订单派单三端状态与原型验收基线.md` |
| `test-native-core.cjs` | 删除 | `docs/BOSS_NATIVE_DESIGN.md`<br>`docs/BOSS_NATIVE_PRODUCT.md`<br>`docs/RELEASE_PIPELINE_AUDIT_20260923.md`<br>`docs/ops/checkout-selection-20261020/verification.json`<br>`docs/ops/multi-companion-selection-20261021/verification-summary.json`<br>`docs/订单派单三端状态与原型验收基线.md` |
| `test-native-lottery.cjs` | 删除 | `docs/ops/lottery-gacha-20261020/native-unit-verification.json`<br>`docs/ops/lottery-ten-guarantee-20261020/native-unit-verification.json` |
| `test-native-multiplatform.cjs` | 删除 | `docs/ops/boss-multiplatform-runtime-adapter-20261102.md` |
| `test-realtime-clients.cjs` | 删除 | `docs/LIVE_AUTHORIZATION_AUDIT_20260923.md`<br>`docs/REALTIME_RECOVERY_AUDIT_20260923.md` |
| `test-release-config.mjs` | 删除 | `docs/RELEASE_PIPELINE_AUDIT_20260923.md` |
| `test-withdraw-ui.cjs` | 删除 | `docs/WALLET_REVIEW_AUDIT_20260923.md` |
| `verify-local-capacity.cjs` | 删除 | `docs/LOCAL_CAPACITY_VALIDATION.md` |

## 已移除的旧端测试模块

下列模块的每个测试都依赖已删除的旧端脚本，且没有发现其他仓库代码引用；列出测试方法便于后续盘点覆盖。

- `services/backend/banners/test_native_live.py`：`test_owner_qualification_changes_open_home_without_affecting_other_owner`, `test_staff_disables_service_while_owner_home_remains_open`, `test_staff_disables_content_while_owner_home_remains_open`
- `services/backend/chat/test_admin_chat_sound_network.py`：`test_customer_message_notifies_staff_with_independent_mute`
- `services/backend/chat/test_admin_history_scroll_http.py`：`test_new_message_preserves_history_and_unread_until_viewed`
- `services/backend/chat/test_chat_history_frontend_http.py`：`test_older_history_and_new_message_with_real_chat_api`
- `services/backend/chat/test_chat_read_frontend_http.py`：`test_native_marks_only_history_actually_loaded`, `test_staff_marks_only_history_actually_loaded`
- `services/backend/chat/test_order_context_frontend_http.py`：`test_order_number_reaches_customer_service_and_context_is_account_scoped`
- `services/backend/chat/test_staff_drafts_mobile_http.py`：`test_customer_drafts_remain_separate_when_switching_sessions`
- `services/backend/console/test_dashboard_chart_browser.py`：`test_actual_orders_render_charts_and_support_tooltips_and_theme`
- `services/backend/console/test_dashboard_overdue_browser.py`：`test_overdue_count_and_filter_at_375`
- `services/backend/console/test_dashboard_queue_realtime_browser.py`：`test_new_order_and_withdrawal_update_open_workbench`
- `services/backend/console/test_dashboard_report_block_browser.py`：`test_refunded_report_drilldown_and_rejection_at_375`
- `services/backend/console/test_report_refund_live_browser.py`：`test_open_review_closes_after_refund_and_can_reject`, `test_open_provider_report_tracks_refund_and_rejection`
- `services/backend/console/test_withdraw_claim_conflict_browser.py`：`test_open_payout_confirmation_invalidated_when_claim_is_released`
- `services/backend/console/test_withdraw_preflight_http.py`：`test_second_staff_cannot_see_old_payout_prompt_after_rejection`
- `services/backend/console/test_withdraw_release_frontend_http.py`：`test_admin_releases_only_after_confirmation_and_rechecks_claim_version`
- `services/backend/coupons/test_account_only_native_realtime.py`：`test_native_claim_and_second_device_update`
- `services/backend/coupons/test_subsidy_realtime_network.py`：`test_open_boss_and_provider_pages_refresh_after_platform_topup`
- `services/backend/coupons/test_vip_checkout_realtime.py`：`test_account_only_checkout_requotes_after_staff_changes_discount`, `test_open_checkout_drops_removed_binding_and_requotes`
- `services/backend/engagement/test_account_only_claim_notice.py`：`test_boss_receives_real_cancellation_and_refund_notice`
- `services/backend/engagement/test_claim_queue_browser_live.py`：`test_gift_and_delivery_update_open_staff_workflow`
- `services/backend/engagement/test_native_claim_progress_live.py`：`test_redeem_and_admin_delivery_keep_open_draft`, `test_suspended_tag_expires_while_native_page_is_open`
- `services/backend/engagement/test_provider_points_realtime_live.py`：`test_provider_redeem_and_staff_cancel_refresh_open_page`
- `services/backend/orders/tests/test_admin_aftersale_history_browser.py`：`test_reopened_history_in_mobile_order_drawer`, `test_dashboard_queue_opens_and_resolves_oldest_case`, `test_reopened_case_updates_open_drawer_via_real_socket`
- `services/backend/orders/tests/test_admin_application_realtime.py`：`test_team_application_refreshes_open_drawer`
- `services/backend/orders/tests/test_admin_report_mobile_375_http.py`：`test_staff_distinguishes_transferred_reports`, `test_staff_reviews_and_rejects_real_report`
- `services/backend/orders/tests/test_admin_sound_network.py`：`test_actual_order_notifies_staff_and_sound_can_be_muted`
- `services/backend/orders/tests/test_admin_team_progress_realtime.py`：`test_open_drawer_refreshes_when_both_providers_submit_evidence`
- `services/backend/orders/tests/test_boss_report_aftersale_hold_http.py`：`test_rejected_report_after_service_window_has_clear_owner_next_step`
- `services/backend/orders/tests/test_legacy_team_warning_browser.py`：`test_staff_can_identify_an_inflight_old_workflow`
- `services/backend/orders/tests/test_native_aftersale_lost_http.py`：`test_lost_open_then_staff_resolve_and_boss_reopens`
- `services/backend/orders/tests/test_native_application_progress_http.py`：`test_boss_progress_matches_real_team_readiness_and_staff_order`
- `services/backend/orders/tests/test_native_favorites_race_http.py`：`test_service_detail_and_list_reconcile_real_mutations`, `test_refresh_and_account_switch_after_real_removal`
- `services/backend/orders/tests/test_native_gift_order_list_http.py`：`test_fresh_device_filters_real_gift_orders_and_keeps_status_filter`
- `services/backend/orders/tests/test_native_gift_recovery_http.py`：`test_lost_response_does_not_duplicate_gift_after_page_reload`
- `services/backend/orders/tests/test_native_order_operation_session_http.py`：`test_switch_accounts_while_cancel_confirmation_is_open`
- `services/backend/orders/tests/test_native_recharge_wallet_realtime.py`：`test_open_boss_wallet_updates_recharge_then_order_payment`
- `services/backend/orders/tests/test_native_reservation_fields_http.py`：`test_actual_reservation_fields_are_available_to_each_party`
- `services/backend/orders/tests/test_native_selection_response_lost_http.py`：`test_team_selection_response_lost_refreshes_real_order`
- `services/backend/orders/tests/test_provider_report_time_browser.py`：`test_real_submission_uses_beijing_time`
- `services/backend/orders/tests/test_provider_reservation_375_browser.py`：`test_confirmation_to_order_at_375px`
- `services/backend/orders/tests/test_qualification_cancel_notice.py`：`test_real_cancellation_discloses_frozen_coupon_fact_and_actual_outcome`
- `services/backend/site_messages/test_boss_message_frontend_http.py`：`test_boss_message_history_survives_new_notice_and_return`
- `services/backend/users/test_full_attendance_account_only.py`：`test_last_day_awards_message_and_staff_sees_progress`
- `services/backend/wallet/test_gift_live_browser_network.py`：`test_gift_refreshes_open_wallet_and_inbox`
- `services/backend/wallet/test_native_wallet_pagination_realtime.py`：`test_recharge_preserves_expanded_filtered_history`

## 保留边界

其余脚本的调用方包含当前业务断言、其他脚本或当前端场景；本次不改写、不跳过也不删除这些测试。需要进一步清理时，应先独立梳理并替换相关测试覆盖。
