"""Real scoped aftersale previews for points-paid transferred service."""
from copy import deepcopy
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import transaction as directory_transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import MemberProfile, PointFundingLot, PointLedger
from orders.models import (NegotiationProposal, Order, OrderApplication, OrderOriginalShareTransfer, OrderParticipationSegment, OrderProvider,
                           PointsNegotiationPosting,
                           OrderRefund, OrderSegmentFinancialSnapshot, OrderSegmentPersonSettlement,
                           OrderPointsFundingRelease, OrderPointsTransferFundingRelease, OrderPointsTransferFundingRevision)
from orders.tests import test_points_checkout_integration as fixture
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from stores.context import store_context
from stores.db import transaction
from users.models import EscortLevel, EscortProfile, PassRefundRepaymentReceipt
from wallet.models import Transaction, Wallet
from wallet.services import get_wallet


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
    SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
    SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    SEGMENT_REFUND_PREVIEW_ENABLED=True, SERVICE_POINTS_TRANSFER_CAPTURE_TESTING=True,
    SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=True, SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_PREVIEW_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class PointsTransferRefundPreviewTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = fixture.PointsFundingPlatformRecoveryTests.setUp
    payload = fixture.PointsFundingPlatformRecoveryTests.payload
    quoted_payload = fixture.PointsFundingPlatformRecoveryTests.quoted_payload
    create_assigned_points_order = fixture.PointsFundingPlatformRecoveryTests.create_assigned_points_order
    verify = fixture.PointsFundingPlatformRecoveryTests.verify
    fund = fixture.PointsFundingPlatformRecoveryTests.fund

    @override_settings(ROOM_COMPLETION_REVIEW_ENABLED=True, ROOM_BOSS_CONFIRMATION_ENABLED=True)
    def test_points_transfer_scoped_refund_remaining_player_completes_room(self):
        self._exercise_room_continuation = True
        try:
            self.test_scoped_transfer_termination_keeps_other_teammate_fulfilling()
        finally:
            del self._exercise_room_continuation

    @override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_TESTING=True,
        SERVICE_POINTS_TRANSFER_REFUND_AGREEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
        SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING=True,
        SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING=True,
        SERVICE_POINTS_TRANSFER_REFUND_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
        SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_FUNDING_TESTING=True,
        SERVICE_POINTS_TRANSFER_REFUND_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
        SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_CONSUMPTION_TESTING=True,
        SERVICE_POINTS_TRANSFER_REFUND_ENDPOINT_TESTING=True, SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True,
        SERVICE_POINTS_TRANSFER_FOLLOWUP_PREVIEW_TESTING=True,
        REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
        NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_scoped_transfer_termination_keeps_other_teammate_fulfilling(self):
        from console.points_redemption_config import save_points_terms
        from orders.points_redemption_config import current_rules
        from orders.points_negotiation_termination import verify_termination_locked
        with TemporaryDirectory(prefix='xa-points-transfer-team-refund-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                user = make_user()
                self.account = get_or_create_account_for_legacy_user(user)
                self.account.boss_type = make_boss_type(discount_rate=90); self.account.save(update_fields=['boss_type'])
                self.wallet = get_wallet(user=user, account=self.account)
                self.wallet.balance = 25000; self.wallet.save(update_fields=['balance'])
                for kind, amount in (('PREPAID', 24800), ('GIFT', 100), ('COMPENSATION', 100)):
                    PointFundingLot.objects.create(account=self.account, source_key=f'team-transfer-mixed-{kind}',
                                                   kind=kind, remaining=amount)
                self.profile = MemberProfile.objects.create(account=self.account, points_balance=15000)
                self.service = make_service(price=10000)
                self.service.billing_provider_count = 2; self.service.commission_policy = 'REGULAR'
                if getattr(self, '_exercise_room_continuation', False):
                    self.service.room_enabled = True
                self.service.save(update_fields=['billing_provider_count', 'commission_policy', 'room_enabled'])
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
                self.client = boss
                save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                                  max_ratio_basis_points=5000, version=current_rules()['version'], audit=lambda *_: None)
                payload = {**self.payload(), 'gift_balance_amount': 100, 'compensation_balance_amount': 100}
                quote = boss.post('/api/orders/quote/', payload, format='json')
                self.assertEqual(quote.data['code'], 0, quote.data)
                data = {key: value for key, value in payload.items() if key != 'preview_points_hundredths'}
                data['points_checkout_test_token'] = quote.data['data']['points_preview']['checkout_test_token']
                created = boss.post('/api/orders/create/', data, format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                order = Order.objects.get(pk=created.data['data']['id'])
                Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
                accounts, players = [], []
                for rate in (20, 30):
                    provider = make_provider(deposit_mode='EARNINGS')
                    account = get_or_create_account_for_legacy_user(provider)
                    level = EscortLevel.objects.create(name=f'transfer-refund-team-{rate}', commission_rate=rate)
                    EscortProfile.objects.filter(user=provider).update(account=account, level=level)
                    accounts.append(account)
                    players.append(APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]))
                grabbed = players[0].post(f'/api/orders/orders/{order.pk}/grab/',
                    {'team_size': 2, 'partner_id': accounts[1].pk}, format='json')
                self.assertEqual(grabbed.data['code'], 0, grabbed.data)
                application = OrderApplication.objects.get(order=order, applicant=accounts[0])
                confirmed = players[1].post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                    {'action': 'CONFIRM_TEAM'}, format='json')
                self.assertEqual(confirmed.data['code'], 0, confirmed.data)
                selected = boss.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
                self.assertEqual(selected.data['code'], 0, selected.data)
                for player in players:
                    started = player.post(f'/api/orders/orders/{order.pk}/start/',
                        {'entry_image': screenshot()}, format='multipart')
                    self.assertEqual(started.data['code'], 0, started.data)
                replacement = make_provider(deposit_mode='EARNINGS')
                replacement_account = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = replacement_account
                replacement.escort_profile.save(update_fields=['account'])
                replacement_client = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(replacement_account)[0])
                operator = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'order:settle',
                                              'support:edit', 'report:audit', 'escort:view', 'escort:deposit_topup'])
                staff = get_or_create_account_for_legacy_user(operator)
                operator.admin_membership.account = staff; operator.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                transfer = admin.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方同意', 'transfers': [
                    {'old_provider_id': accounts[0].pk, 'new_provider_id': replacement_account.pk, 'split_type': 'PERCENT',
                     'split_value': 0, 'penalty_amount': 0, 'original_share': {'retained': 5000,
                        'transferred': 5000, 'agreement': '双方同意'}}]}, format='json')
                self.assertEqual(transfer.data['code'], 0, transfer.data)
                segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=replacement_account)
                pending = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                    'reason': '未入队期间先核对队友', 'affected_provider_account_ids': [accounts[1].pk],
                    'client_request_id': 'team-unstarted-check'}, format='json')
                self.assertEqual(pending.data['code'], 0, pending.data)
                pending_id = pending.data['data']['id']
                pending_preview = admin.get(f'/api/admin/aftersales/{pending_id}/negotiation-preview/?refund_units=1000')
                self.assertEqual(pending_preview.data['code'], 0, pending_preview.data)
                self.assertFalse(pending_preview.data['data']['agreement_available'])
                self.assertFalse(pending_preview.data['data']['execution_available'])
                pending_term = pending_preview.data['data']['provider_terms'][0]
                denied = admin.post(f'/api/admin/aftersales/{pending_id}/negotiation-proposal/',
                    {'request_id': 'unstarted-unsafe-proposal', 'expected_version': 0, 'refund_units': 1000,
                     'provider_terms': [{'account_id': accounts[1].pk, 'retained_units': pending_term['retained_units']}],
                     'explanation': '先确认队友方案'}, format='json')
                self.assertGreaterEqual(denied.status_code, 400)
                resolved = admin.post(f'/api/admin/aftersales/{pending_id}/resolve/', {'resolution': '继续等待入队'}, format='json')
                self.assertEqual(resolved.data['code'], 0, resolved.data)
                entered = replacement_client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': segment.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(entered.data['code'], 0, entered.data)
                opened = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                    'reason': '只终止其中一位陪玩', 'affected_provider_account_ids': [replacement_account.pk],
                    'client_request_id': 'team-transfer-close'}, format='json')
                self.assertEqual(opened.data['code'], 0, opened.data)
                case_id = opened.data['data']['id']
                preview = admin.post(f'/api/admin/orders/{order.pk}/segment-refund-preview/',
                    {'refund_amount': 1000}, format='json')
                self.assertEqual(preview.data['code'], 0, preview.data)
                term = preview.data['data']['provider_terms'][0]
                proposed = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',
                    {'request_id': 'team-transfer-proposal', 'expected_version': 0, 'refund_units': 1000,
                     'provider_terms': [{'account_id': replacement_account.pk, 'retained_units': term['retained_units']}],
                     'explanation': '双方确认该陪玩退款'}, format='json')
                self.assertEqual(proposed.data['code'], 0, proposed.data)
                proposal_id = proposed.data['data']['id']
                status = admin.get(f'/api/admin/aftersales/{case_id}/negotiation-execution/')
                self.assertTrue(status.data['data']['execution_enabled'])
                summary = admin.get(f'/api/admin/aftersales/{case_id}/negotiation-proposal/')
                self.assertTrue(summary.data['data']['execution_available'])
                for actor in (boss, replacement_client):
                    accepted = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                        {'proposal_id': proposal_id, 'accepted': True}, format='json')
                    self.assertEqual(accepted.data['code'], 0, accepted.data)
                with directory_transaction.atomic(using='default'), transaction.atomic():
                    execution_url = f'/api/admin/aftersales/{case_id}/negotiation-execution/'
                    execution_body = {'proposal_id': proposal_id, 'client_request_id': 'team-transfer-refund'}
                    for switch in ('SERVICE_POINTS_TRANSFER_REFUND_ENDPOINT_TESTING',
                                   'SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING',
                                   'SERVICE_POINTS_TRANSFER_REFUND_FUNDING_TESTING',
                                   'SERVICE_POINTS_TRANSFER_REFUND_REPORT_TESTING',
                                   'SERVICE_POINTS_TRANSFER_REFUND_SETTLEMENT_TESTING',
                                   'SERVICE_POINTS_TRANSFER_REFUND_CONSUMPTION_TESTING',
                                   'SERVICE_POINTS_REFUND_DEPOSIT_TESTING', 'REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED'):
                        with self.subTest(switch=switch), override_settings(**{switch: False}):
                            self.assertFalse(admin.get(execution_url).data['data']['execution_enabled'])
                            denied = admin.post(execution_url, execution_body, format='json')
                            self.assertGreaterEqual(denied.status_code, 400)
                            self.assertFalse(OrderRefund.objects.filter(order=order).exists())
                    support_user = make_console_user(['order:view', 'support:edit'])
                    support = get_or_create_account_for_legacy_user(support_user)
                    support_user.admin_membership.account = support; support_user.admin_membership.save(update_fields=['account'])
                    support_client = APIClient(HTTP_HOST='admin.esports.club',
                        HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(support)[0])
                    self.assertEqual(support_client.post(execution_url, execution_body, format='json').status_code, 403)
                    support_preview = support_client.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/?refund_units=1000')
                    self.assertFalse(support_preview.data['data']['execution_available'])
                    from orders.points_negotiation_termination import transfer_endpoint_enabled
                    from engagement.models import TransferRefundPointsConsumptionEvidence
                    from stores.db import connection
                    with connection.cursor() as cursor:
                        tables = connection.introspection.table_names(cursor)
                    incomplete = [table for table in tables if table != TransferRefundPointsConsumptionEvidence._meta.db_table]
                    with patch.object(connection.introspection, 'table_names', return_value=incomplete):
                        self.assertFalse(transfer_endpoint_enabled())
                    with patch.dict('os.environ', {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
                        self.assertFalse(transfer_endpoint_enabled())
                    from django.db import connections
                    with patch.dict(connections['default'].settings_dict, {'NAME': 'non-disposable.sqlite3'}):
                        self.assertFalse(transfer_endpoint_enabled())
                    with store_context(self.first):
                        foreign_user = make_console_user(['order:view', 'order:refund'])
                        foreign_account = get_or_create_account_for_legacy_user(foreign_user)
                        foreign_user.admin_membership.account = foreign_account
                        foreign_user.admin_membership.save(update_fields=['account'])
                        foreign = APIClient(HTTP_HOST='admin.esports.club',
                            HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(foreign_account)[0])
                        denied = foreign.post(execution_url, execution_body, format='json')
                        self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                    executed = admin.post(execution_url, execution_body, format='json')
                    self.assertEqual(executed.data['code'], 0, executed.data)
                    posting = PointsNegotiationPosting.objects.get(pk=executed.data['data']['execution_id'])
                    replay = admin.post(execution_url, execution_body, format='json')
                    self.assertEqual(replay.data['data']['execution_id'], posting.pk)
                    conflict = admin.post(execution_url, {**execution_body, 'client_request_id': 'different-request'}, format='json')
                    self.assertGreaterEqual(conflict.status_code, 400)
                    with override_settings(SERVICE_POINTS_TRANSFER_REFUND_ENDPOINT_TESTING=False):
                        readback = admin.get(execution_url, execution_body)
                        self.assertEqual(readback.data['data']['execution_id'], posting.pk)
                    current = Order.objects.get(pk=order.pk)
                    self.assertEqual((current.status, current.payment_status), ('IN_SERVICE', 'PARTIALLY_REFUNDED'))
                    self.assertIsNone(OrderProvider.objects.get(order=order, provider_account=accounts[1]).released_at)
                    self.assertEqual(OrderParticipationSegment.objects.get(pk=segment.pk).released_at, posting.accepted_at)
                    self.assertEqual(verify_termination_locked(posting).pk, posting.pk)
                    with transaction.atomic():
                        denied = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                            'reason': '不能重开已确认部分', 'affected_provider_account_ids': [replacement_account.pk],
                            'client_request_id': 'closed-party-again'}, format='json')
                        self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                        reopened = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                            'reason': '另一名陪玩需要处理', 'affected_provider_account_ids': [accounts[1].pk],
                            'client_request_id': 'other-party-new'}, format='json')
                        self.assertEqual(reopened.data['code'], 0, reopened.data)
                        transaction.set_rollback(True)
                    evidence = replacement_client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                        {'segment_id': segment.pk, 'completion_image': screenshot()}, format='multipart')
                    self.assertEqual(evidence.data['code'], 0, evidence.data)
                    for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                        report = replacement_client.post(f'/api/orders/orders/{order.pk}/segment-report/',
                            {'segment_id': segment.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                            format='multipart' if action == 'upload' else 'json')
                        self.assertEqual(report.data['code'], 0, report.data)
                    reviewed = admin.post(f'/api/admin/orders/{order.pk}/segments/{segment.pk}/report-review/',
                        {'action': 'approve', 'version': 1}, format='json')
                    self.assertEqual(reviewed.data['code'], 0, reviewed.data)
                    from console.models import AdminAuditLog
                    with transaction.atomic():
                        AdminAuditLog.objects.filter(path=f'/api/admin/aftersales/{case_id}/negotiation-execution/',
                            object_id=str(case_id)).delete()
                        denied = players[1].post(f'/api/orders/orders/{order.pk}/complete/',
                            {'completion_image': screenshot()}, format='multipart')
                        self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                        self.assertEqual(Order.objects.get(pk=order.pk).status, 'IN_SERVICE')
                        transaction.set_rollback(True)
                    completed = players[1].post(f'/api/orders/orders/{order.pk}/complete/',
                        {'completion_image': screenshot()}, format='multipart')
                    self.assertEqual(completed.data['code'], 0, completed.data)
                    if getattr(self, '_exercise_room_continuation', False):
                        from wallet.models import SystemConfig
                        SystemConfig.objects.update_or_create(key='room_confirmation_deadline',
                            defaults={'value': '{"minutes":30,"version":1}'})
                        self.assertEqual(Order.objects.get(pk=order.pk).status, 'IN_SERVICE')
                        room_url = f'/api/orders/orders/{order.pk}/room/completion-requests/'
                        submitted = players[1].post(room_url, {'description':'队友继续完成房间服务',
                            'client_request_id':'points-transfer-room'}, format='json')
                        self.assertEqual(submitted.status_code, 200, submitted.data)
                        self.assertEqual(submitted.data['data']['participants'], [accounts[1].pk])
                        request_id = submitted.data['data']['id']
                        reviewed = admin.post(f'/api/admin/room-completion-requests/{request_id}/review/',
                            {'decision':'VERIFIED', 'note':'核实队友完成', 'client_request_id':'points-transfer-review'}, format='json')
                        self.assertEqual(reviewed.status_code, 200, reviewed.data)
                        done = boss.post(f'{room_url}{request_id}/confirm/',
                            {'client_request_id':'points-transfer-confirm'}, format='json')
                        self.assertEqual(done.status_code, 200, done.data)
                    self.assertEqual(Order.objects.get(pk=order.pk).status, 'COMPLETED')
                    self.assertEqual(verify_termination_locked(posting).pk, posting.pk)
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                    with transaction.atomic():
                        reopened = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {
                            'reason': '另一位陪玩申请售后', 'affected_provider_account_ids': [accounts[1].pk],
                            'client_request_id': 'transfer-followup-unpaid'}, format='json')
                        self.assertEqual(reopened.data['code'], 0, reopened.data)
                        followup_id = reopened.data['data']['id']
                        before = (OrderRefund.objects.filter(order=order).count(),
                                  Transaction.objects.filter(order=order).count(),
                                  PointLedger.objects.filter(order=order).count())
                        followup = admin.get(f'/api/admin/aftersales/{followup_id}/negotiation-preview/?refund_units=1000')
                        self.assertEqual(followup.data['code'], 0, followup.data)
                        self.assertEqual(followup.data['data']['provider_terms'][0]['account_id'], accounts[1].pk)
                        self.assertEqual((followup.data['data']['read_only'], followup.data['data']['agreement_available'],
                                          followup.data['data']['execution_available']), (True, False, False))
                        funding_preview = admin.get(f'/api/admin/aftersales/{followup_id}/negotiation-funding/?refund_units=1000')
                        self.assertEqual(funding_preview.data['code'], 0, funding_preview.data)
                        self.assertFalse(funding_preview.data['data']['executable'])
                        self.assertEqual(support_client.get(
                            f'/api/admin/aftersales/{followup_id}/negotiation-funding/?refund_units=1000').status_code, 403)
                        with store_context(self.first):
                            denied = foreign.get(f'/api/admin/aftersales/{followup_id}/negotiation-preview/?refund_units=1000')
                            self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                        from orders.points_transfer_followup_preview import preview_locked as transfer_followup_preview
                        detail = transfer_followup_preview(followup_id, refund_equivalent_units=10)
                        self.assertEqual(detail['settled_other_provider_units'], 0)
                        self.assertEqual(detail['conditional_funding']['net_coin_units'],
                            order.amount - posting.points_refund.coin_units - detail['coin_units'])
                        too_large = transfer_followup_preview(followup_id, refund_equivalent_units=7000)
                        self.assertGreater(too_large['provider_recovery_preview']['shortfall_units'], 0)
                        self.assertEqual(sum(too_large['provider_recovery_preview'].values()),
                                         too_large['provider_terms'][0]['burden_units'])
                        self.assertFalse(too_large['estimated_sufficient'])
                        self.assertFalse(too_large['execution_available'])
                        with transaction.atomic(), override_settings(
                                SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_AGREEMENT_TESTING=True,
                                SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_EXECUTION_TESTING=True,
                                SERVICE_POINTS_TRANSFER_FOLLOWUP_POSTING_TESTING=True,
                                SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_TESTING=True,
                                SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True):
                            target_profile = EscortProfile.objects.get(account=accounts[1])
                            shortfall = too_large['provider_recovery_preview']['shortfall_units']
                            self.assertGreater(shortfall, 10)
                            wallet_amount = min(200, shortfall - 10)
                            deposit_amount = ((shortfall - wallet_amount + 9) // 10) * 10 + 100
                            target_profile.deposit_mode = 'CASH'; target_profile.deposit_paid = 0
                            target_profile.deposit_required = deposit_amount
                            target_profile.save(update_fields=['deposit_mode', 'deposit_paid', 'deposit_required'])
                            Wallet.objects.filter(user_id=target_profile.user_id).update(balance=wallet_amount)
                            topup = admin.post(f'/api/admin/escorts/{target_profile.pk}/deposit-topups/',
                                {'amount': deposit_amount, 'reason': '隔离测试线下押金', 'reference': 'FOLLOWUP-CASH-DEPOSIT',
                                 'client_request_id': 'followup-cash-deposit'}, format='multipart')
                            self.assertEqual(topup.data['code'], 0, topup.data)
                            backed = transfer_followup_preview(followup_id, refund_equivalent_units=7000)
                            self.assertTrue(backed['estimated_sufficient'])
                            self.assertGreater(backed['provider_recovery_preview']['wallet_units'], 0)
                            self.assertGreater(backed['provider_recovery_preview']['deposit_units'], 0)
                            version = (NegotiationProposal.objects.filter(case_id=followup_id)
                                       .order_by('-version').values_list('version', flat=True).first() or 0)
                            proposed = support_client.post(f'/api/admin/aftersales/{followup_id}/negotiation-proposal/',
                                {'request_id': 'transfer-followup-cash-consent', 'expected_version': version,
                                 'refund_units': 7000, 'provider_terms': [{'account_id': accounts[1].pk,
                                    'retained_units': backed['provider_terms'][0]['retained_units']}],
                                 'explanation': '双方确认钱包和押金承担'}, format='json')
                            self.assertEqual(proposed.data['code'], 0, proposed.data)
                            large_proposal_id = proposed.data['data']['id']
                            for actor in (boss, players[1]):
                                agreed = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                    {'proposal_id': large_proposal_id, 'accepted': True}, format='json')
                                self.assertEqual(agreed.data['code'], 0, agreed.data)
                            from orders.points_transfer_followup_execution import post_locked as post_transfer_followup
                            from orders.points_followup_execution import prepare_followup_locked
                            with transaction.atomic():
                                EscortProfile.objects.filter(pk=target_profile.pk).update(deposit_paid=0)
                                with self.assertRaises(ValidationError):
                                    prepare_followup_locked(followup_id, large_proposal_id, operator=staff)
                                self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)
                                transaction.set_rollback(True)
                            second = post_transfer_followup(followup_id, large_proposal_id, operator=staff,
                                legacy_operator=operator, request_id='transfer-followup-cash-post')
                            share = second.shares.get(account=accounts[1])
                            self.assertEqual((share.wallet_deduction, share.deposit_deduction),
                                (backed['provider_recovery_preview']['wallet_units'],
                                 backed['provider_recovery_preview']['deposit_units']))
                            self.assertEqual(Wallet.objects.get(user_id=target_profile.user_id).balance,
                                             wallet_amount - share.wallet_deduction)
                            target_profile.refresh_from_db()
                            self.assertEqual(target_profile.deposit_paid, deposit_amount - share.deposit_deduction)
                            self.assertEqual(share.deposit_deficit.amount, share.deposit_deduction)
                            transaction.set_rollback(True)
                        with transaction.atomic(), override_settings(
                                SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_AGREEMENT_TESTING=True,
                                SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_EXECUTION_TESTING=True,
                                SERVICE_POINTS_TRANSFER_FOLLOWUP_POSTING_TESTING=True,
                                SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_TESTING=True,
                                SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_TESTING=True,
                                SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True):
                            original_receipt = posting.points_refund.redemption
                            remaining_refund = original_receipt.deal_units - posting.points_refund.equivalent_units
                            target_user = OrderProvider.objects.get(order=order, provider_account=accounts[1]).provider_id
                            Wallet.objects.filter(user_id=target_user).update(balance=remaining_refund)
                            final_preview = transfer_followup_preview(followup_id, refund_equivalent_units=remaining_refund)
                            self.assertTrue(final_preview['estimated_sufficient'])
                            self.assertTrue(final_preview['is_final'])
                            version = (NegotiationProposal.objects.filter(case_id=followup_id)
                                       .order_by('-version').values_list('version', flat=True).first() or 0)
                            proposed = support_client.post(f'/api/admin/aftersales/{followup_id}/negotiation-proposal/',
                                {'request_id': 'transfer-followup-final-consent', 'expected_version': version,
                                 'refund_units': remaining_refund, 'provider_terms': [{'account_id': accounts[1].pk,
                                    'retained_units': final_preview['provider_terms'][0]['retained_units']}],
                                 'explanation': '双方确认全额退款'}, format='json')
                            self.assertEqual(proposed.data['code'], 0, proposed.data)
                            final_proposal = proposed.data['data']['id']
                            for actor in (boss, players[1]):
                                agreed = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                    {'proposal_id': final_proposal, 'accepted': True}, format='json')
                                self.assertEqual(agreed.data['code'], 0, agreed.data)
                            from orders.points_transfer_followup_termination import execute_locked as close_followup
                            from orders.points_transfer_followup_termination import verify_locked as verify_followup_close
                            from orders.models import AfterSale
                            final_posting = close_followup(followup_id, final_proposal, operator=staff,
                                legacy_operator=operator, request_id='transfer-followup-final-close')
                            self.assertTrue(final_posting.points_refund.is_final)
                            self.assertEqual(Order.objects.get(pk=order.pk).payment_status, 'REFUNDED')
                            self.assertEqual(AfterSale.objects.get(pk=followup_id).status, 'REFUNDED')
                            self.assertEqual(posting.points_refund.coin_units + final_posting.points_refund.coin_units,
                                             original_receipt.paid_units)
                            self.assertEqual(posting.points_refund.points_hundredths + final_posting.points_refund.points_hundredths,
                                             original_receipt.points_hundredths)
                            self.assertIn('restored_coupon_count', final_posting.points_refund.refund.benefit_effects)
                            self.assertEqual(verify_followup_close(final_posting).pk, final_posting.pk)
                            from orders.points_negotiation_termination import _verify_transfer_termination_facts_locked
                            from orders.points_transfer_followup_termination import _verify_followup_closure_facts_locked
                            with self.assertRaises(ValidationError):
                                _verify_transfer_termination_facts_locked(posting, allow_verified_later_final=True)
                            with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING=True,
                                                   SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING=True):
                                self.assertEqual(_verify_transfer_termination_facts_locked(posting,
                                    allow_verified_later_final=True).pk, posting.pk)
                                self.assertEqual(_verify_followup_closure_facts_locked(final_posting).pk, final_posting.pk)
                            self.assertEqual(close_followup(followup_id, final_proposal, operator=staff,
                                legacy_operator=operator, request_id='transfer-followup-final-close').pk, final_posting.pk)
                            with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING=True):
                                from orders.points_transfer_followup_settlement_plan import build_locked as followup_plan
                                plan, funding = followup_plan(order.pk)
                                self.assertEqual(sum(person.income for person in plan.persons), funding.provider_income_units)
                            with directory_transaction.atomic(using='default'), transaction.atomic(), override_settings(
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_FUNDING_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PREFLIGHT_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_CONSUMPTION_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING=True):
                                from orders.points_funding_evidence import verify_order_funding
                                needed = verify_order_funding(order.pk)
                                if needed.remaining:
                                    from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
                                    old_client = self.client
                                    self.client = self.platform_client
                                    registered = PlatformPassRepaymentTests.post(self, 'xinan', {**self.data,
                                        'request_id': str(uuid4()), 'external_reference': 'FINAL-TRANSFER-FUNDING',
                                        'amount': needed.remaining})
                                    self.assertEqual(registered.status_code, 200, registered.data)
                                    claim = registered.data['data']['claim_id']
                                    verified = PlatformPassRepaymentTests.action(self, claim, 'verify', {'request_id': str(uuid4())})
                                    self.assertEqual(verified.status_code, 200, verified.data)
                                    allocated = self.client.post(
                                        f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation',
                                        {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': needed.remaining},
                                        format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
                                    self.assertEqual(allocated.status_code, 200, allocated.data)
                                    self.client = old_client
                                self.assertTrue(verify_order_funding(order.pk).fully_funded)
                                first_part = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=accounts[0])
                                second_part = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=accounts[1])
                                response = players[0].post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                                    {'segment_id': first_part.pk, 'completion_image': screenshot()}, format='multipart')
                                self.assertEqual(response.data['code'], 0, response.data)
                                for actor, part in ((players[0], first_part), (players[1], second_part)):
                                    for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                                        response = actor.post(f'/api/orders/orders/{order.pk}/segment-report/',
                                            {'segment_id': part.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                                            format='multipart' if action == 'upload' else 'json')
                                        self.assertEqual(response.data['code'], 0, response.data)
                                    response = admin.post(f'/api/admin/orders/{order.pk}/segments/{part.pk}/report-review/',
                                        {'action': 'approve', 'version': 1}, format='json')
                                    self.assertEqual(response.data['code'], 0, response.data)
                                from orders.services import settle_order
                                from engagement.confirmation import process_order
                                from engagement.models import ConsumptionConfirmation, TransferRefundPointsConsumptionEvidence
                                from engagement.transfer_refunded_points_confirmation import verify_evidence_locked
                                with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
                                    settle_order(Order.objects.select_for_update().get(pk=order.pk))
                                    settled = Order.objects.select_for_update().get(pk=order.pk)
                                    self.assertIsNotNone(settled.settled_at)
                                    self.assertFalse(process_order(order.pk))
                                    member = TransferRefundPointsConsumptionEvidence.objects.get(manifest__order=order)
                                    self.assertEqual((member.net_paid_units, member.net_principal_units, member.awarded_points), (0, 0, 0))
                                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                                    self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                                    self.assertEqual(verify_evidence_locked(member).pk, member.pk)
                                    self.assertFalse(process_order(order.pk))
                                directory_transaction.set_rollback(True, using='default')
                                transaction.set_rollback(True)
                            transaction.set_rollback(True)
                        version = (NegotiationProposal.objects.filter(case_id=followup_id)
                                   .order_by('-version').values_list('version', flat=True).first() or 0)
                        blocked = support_client.post(f'/api/admin/aftersales/{followup_id}/negotiation-proposal/',
                            {'request_id': 'transfer-followup-blocked', 'expected_version': version, 'refund_units': 10,
                             'provider_terms': [{'account_id': accounts[1].pk,
                                'retained_units': detail['provider_terms'][0]['retained_units']}],
                             'explanation': '追加退款尚不可执行'}, format='json')
                        self.assertGreaterEqual(blocked.status_code, 400)
                        with transaction.atomic(), override_settings(
                                SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_AGREEMENT_TESTING=True):
                            from orders.points_transfer_followup_preview import internal_agreement_enabled
                            self.assertTrue(internal_agreement_enabled())
                            from django.db import connections
                            with patch.dict(connections['default'].settings_dict, {'NAME': 'non-disposable.sqlite3'}):
                                self.assertFalse(internal_agreement_enabled())
                            with patch.dict('os.environ', {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
                                self.assertFalse(internal_agreement_enabled())
                            version = (NegotiationProposal.objects.filter(case_id=followup_id)
                                       .order_by('-version').values_list('version', flat=True).first() or 0)
                            term = detail['provider_terms'][0]
                            proposed = support_client.post(f'/api/admin/aftersales/{followup_id}/negotiation-proposal/',
                                {'request_id': 'transfer-followup-consent', 'expected_version': version,
                                 'refund_units': 10, 'provider_terms': [{'account_id': accounts[1].pk,
                                    'retained_units': term['retained_units']}], 'explanation': '双方确认追加退款'}, format='json')
                            self.assertEqual(proposed.data['code'], 0, proposed.data)
                            proposal_id = proposed.data['data']['id']
                            self.assertFalse(proposed.data['data']['points_execution_available'])
                            from orders.points_negotiation_preview import verified_asset_terms
                            proposal = NegotiationProposal.objects.get(pk=proposal_id)
                            self.assertEqual(verified_asset_terms(proposal)['prior_refund_id'], posting.points_refund_id)
                            user_url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
                            self.assertEqual(replacement_client.get(user_url).status_code, 404)
                            self.assertEqual(players[0].get(user_url).status_code, 404)
                            for actor in (boss, players[1]):
                                agreed = actor.post(user_url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
                                self.assertEqual(agreed.data['code'], 0, agreed.data)
                            self.assertEqual(agreed.data['data']['status'], 'ACCEPTED')
                            self.assertFalse(agreed.data['data']['points_execution_available'])
                            retry = players[1].post(user_url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
                            self.assertEqual(retry.data['code'], 0, retry.data)
                            from orders.points_followup_execution import prepare_followup_locked, post_followup_locked
                            with override_settings(SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_EXECUTION_TESTING=True,
                                    SERVICE_POINTS_FOLLOWUP_POSTING_TESTING=True,
                                    SERVICE_POINTS_FOLLOWUP_REVISION_TESTING=True,
                                    SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING=True):
                                prepared = prepare_followup_locked(followup_id, proposal_id, operator=staff)
                                expected = detail['provider_recovery_preview']
                                self.assertEqual((prepared['pending_units'], prepared['wallet_units'], prepared['deposit_units']),
                                    (expected['pending_units'], expected['wallet_units'], expected['deposit_units']))
                                with self.assertRaises(ValidationError):
                                    prepare_followup_locked(followup_id, proposal_id, operator=support)
                                with self.assertRaises(ValidationError):
                                    post_followup_locked(followup_id, proposal_id, operator=staff,
                                        legacy_operator=operator, request_id='transfer-followup-internal-post')
                                from orders.points_transfer_followup_execution import post_locked as post_transfer_followup
                                from orders.models import OrderPointsTransferFollowupFundingRevision
                                from orders.points_transfer_followup_revision import verify_locked as verify_followup_revision
                                with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_POSTING_TESTING=True,
                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_TESTING=True):
                                    with patch.dict(connections['default'].settings_dict, {'NAME': 'non-disposable.sqlite3'}):
                                        with self.assertRaises(ValidationError):
                                            post_transfer_followup(followup_id, proposal_id, operator=staff,
                                                legacy_operator=operator, request_id='transfer-followup-post')
                                    with patch('orders.points_transfer_followup_revision.OrderPointsTransferFollowupFundingRevision.objects.create',
                                               side_effect=RuntimeError('followup revision failed')):
                                        with self.assertRaisesMessage(RuntimeError, 'followup revision failed'):
                                            post_transfer_followup(followup_id, proposal_id, operator=staff,
                                                legacy_operator=operator, request_id='transfer-followup-post')
                                    self.assertEqual(before, (OrderRefund.objects.filter(order=order).count(),
                                        Transaction.objects.filter(order=order).count(), PointLedger.objects.filter(order=order).count()))
                                    with transaction.atomic():
                                        posted = post_transfer_followup(followup_id, proposal_id, operator=staff,
                                            legacy_operator=operator, request_id='transfer-followup-post')
                                        self.assertEqual((posted.points_refund.coin_units, posted.points_refund.points_hundredths),
                                                         (detail['coin_units'], detail['points_hundredths']))
                                        revision = OrderPointsTransferFollowupFundingRevision.objects.get(posting=posted)
                                        actual, funding_revision = verify_followup_revision(revision)
                                        self.assertEqual(actual.net_coin_units,
                                            order.amount - posting.points_refund.coin_units - posted.points_refund.coin_units)
                                        self.assertEqual(funding_revision.paid_provider_units, 0)
                                        self.assertEqual(post_transfer_followup(followup_id, proposal_id, operator=staff,
                                            legacy_operator=operator, request_id='transfer-followup-post').pk, posted.pk)
                                        with self.assertRaises(ValidationError):
                                            post_transfer_followup(followup_id, proposal_id, operator=staff,
                                                legacy_operator=operator, request_id='different-followup-post')
                                        self.assertEqual(OrderRefund.objects.filter(order=order).count(), 2)
                                        transaction.set_rollback(True)
                                    from orders.points_transfer_followup_termination import execute_locked as close_followup
                                    from orders.points_transfer_followup_termination import verify_locked as verify_followup_close
                                    with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_TESTING=True):
                                        with patch('console.models.AdminAuditLog.objects.create',
                                                   side_effect=RuntimeError('followup audit failed')):
                                            with self.assertRaisesMessage(RuntimeError, 'followup audit failed'):
                                                close_followup(followup_id, proposal_id, operator=staff,
                                                    legacy_operator=operator, request_id='transfer-followup-close')
                                        self.assertEqual(before, (OrderRefund.objects.filter(order=order).count(),
                                            Transaction.objects.filter(order=order).count(),
                                            PointLedger.objects.filter(order=order).count()))
                                        with directory_transaction.atomic(using='default'), transaction.atomic():
                                            endpoint_flags = {key: True for key in (
                                                'SERVICE_POINTS_TRANSFER_REFUND_PRIOR_SETTLEMENT_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_ENDPOINT_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_EXECUTION_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_POSTING_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_FUNDING_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PREFLIGHT_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_CONSUMPTION_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING',
                                                'SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING',
                                                'SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING')}
                                            endpoint = f'/api/admin/aftersales/{followup_id}/negotiation-execution/'
                                            execution = {'proposal_id': proposal_id, 'client_request_id': 'transfer-followup-close'}
                                            with override_settings(**endpoint_flags):
                                                self.assertTrue(admin.get(endpoint).data['data']['execution_enabled'])
                                                preview = admin.get(f'/api/admin/aftersales/{followup_id}/negotiation-preview/?refund_units=10')
                                                self.assertTrue(preview.data['data']['agreement_available'])
                                                self.assertTrue(preview.data['data']['execution_available'])
                                                self.assertFalse(support_client.get(
                                                    f'/api/admin/aftersales/{followup_id}/negotiation-preview/?refund_units=10'
                                                    ).data['data']['execution_available'])
                                                self.assertTrue(admin.get(f'/api/admin/aftersales/{followup_id}/negotiation-proposal/')
                                                                .data['data']['execution_available'])
                                                self.assertEqual(support_client.post(endpoint, execution, format='json').status_code, 403)
                                                for switch in ('SERVICE_POINTS_TRANSFER_FOLLOWUP_ENDPOINT_TESTING',
                                                        'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING',
                                                        'SERVICE_POINTS_TRANSFER_FOLLOWUP_CONSUMPTION_TESTING',
                                                        'SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING'):
                                                    with self.subTest(disabled=switch), override_settings(**{switch: False}):
                                                        self.assertFalse(admin.get(endpoint).data['data']['execution_enabled'])
                                                        self.assertGreaterEqual(admin.post(endpoint, execution, format='json').status_code, 400)
                                                        self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)
                                                with store_context(self.first):
                                                    denied = foreign.post(endpoint, execution, format='json')
                                                    self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                                                executed = admin.post(endpoint, execution, format='json')
                                                self.assertEqual(executed.data['code'], 0, executed.data)
                                                closed = PointsNegotiationPosting.objects.get(pk=executed.data['data']['execution_id'])
                                                self.assertEqual(admin.post(endpoint, execution, format='json').data['data']['execution_id'], closed.pk)
                                                self.assertEqual(admin.get(endpoint, execution).data['data']['execution_id'], closed.pk)
                                                with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_ENDPOINT_TESTING=False):
                                                    self.assertEqual(admin.get(endpoint, execution).data['data']['execution_id'], closed.pk)
                                                    self.assertGreaterEqual(admin.post(endpoint, execution, format='json').status_code, 400)
                                            self.assertEqual(verify_followup_close(closed).pk, closed.pk)
                                            from orders.points_transfer_followup_revision import (
                                                _verify_followup_revision_facts_locked as verify_revision_facts)
                                            with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING=True,
                                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING=True,
                                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING=True,
                                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING=True):
                                                revision = OrderPointsTransferFollowupFundingRevision.objects.get(posting=closed)
                                                self.assertEqual(verify_revision_facts(revision), verify_followup_revision(revision))
                                            with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING=True,
                                                                   SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING=True):
                                                from orders.points_negotiation_termination import _verify_transfer_termination_facts_locked
                                                from orders.points_transfer_followup_termination import _verify_followup_closure_facts_locked
                                                self.assertEqual(_verify_transfer_termination_facts_locked(posting).pk, posting.pk)
                                                self.assertEqual(_verify_followup_closure_facts_locked(closed).pk, closed.pk)
                                            from orders.points_negotiation_execution import _verify_transfer_posting_facts_locked
                                            for candidate in (posting, closed):
                                                checked_posting, checked_facts, checked_history = _verify_transfer_posting_facts_locked(candidate)
                                                self.assertEqual(checked_posting.pk, candidate.pk)
                                                self.assertEqual(checked_facts, posting.participants['facts'])
                                                self.assertTrue(checked_history.segment_shares)
                                            with transaction.atomic():
                                                original_refund_tx = posting.points_refund.coin_transaction
                                                self.assertIsNotNone(original_refund_tx)
                                                Transaction.objects.filter(pk=original_refund_tx.pk).update(
                                                    amount=original_refund_tx.amount + 10)
                                                with self.assertRaises(ValidationError):
                                                    _verify_transfer_posting_facts_locked(posting)
                                                transaction.set_rollback(True)
                                            from orders.models import AfterSale
                                            self.assertEqual(AfterSale.objects.get(pk=followup_id).status, 'PARTIALLY_REFUNDED')
                                            self.assertEqual(Order.objects.get(pk=order.pk).payment_status, 'PARTIALLY_REFUNDED')
                                            from orders.negotiation_aftersale_scope import closed_provider_accounts, assert_scope_not_closed
                                            current = Order.objects.select_for_update().get(pk=order.pk)
                                            self.assertEqual(closed_provider_accounts(current),
                                                             {replacement_account.pk, accounts[1].pk})
                                            for account in (replacement_account, accounts[1]):
                                                with self.assertRaises(ValidationError):
                                                    assert_scope_not_closed(current, [account.pk])
                                            self.assertNotIn(accounts[0].pk, closed_provider_accounts(current))
                                            self.assertEqual(close_followup(followup_id, proposal_id, operator=staff,
                                                legacy_operator=operator, request_id='transfer-followup-close').pk, closed.pk)
                                            with self.assertRaises(ValidationError):
                                                close_followup(followup_id, proposal_id, operator=staff,
                                                    legacy_operator=operator, request_id='wrong-followup-close')
                                            with transaction.atomic():
                                                from console.models import AdminAuditLog
                                                AdminAuditLog.objects.filter(
                                                    path=f'/api/admin/aftersales/{followup_id}/negotiation-execution/',
                                                    request_body__refund_id=closed.points_refund.refund_id).delete()
                                                with self.assertRaises(ValidationError):
                                                    verify_followup_close(closed)
                                                with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING=True):
                                                    with self.assertRaises(ValidationError):
                                                        _verify_followup_closure_facts_locked(closed)
                                                transaction.set_rollback(True)
                                            with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_FUNDING_TESTING=True):
                                                from orders.points_funding_evidence import verify_order_funding
                                                unpaid = verify_order_funding(order.pk)
                                                self.assertEqual(unpaid.required,
                                                    detail['conditional_funding']['required_units'])
                                                self.assertGreater(unpaid.remaining, 0)
                                                from orders.points_transfer_followup_settlement_plan import build_locked as followup_plan
                                                from orders.settlement import SettlementError
                                                with self.assertRaises(SettlementError):
                                                    followup_plan(order.pk)
                                                with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING=True):
                                                    before_plan = (Transaction.objects.filter(order=order).count(),
                                                        PointLedger.objects.filter(order=order).count())
                                                    plan, plan_funding = followup_plan(order.pk)
                                                    persons = {item.account_id: item for item in plan.persons}
                                                    self.assertEqual(persons[replacement_account.pk].income,
                                                        posting.shares.get(account_id=replacement_account.pk).retained_income)
                                                    self.assertEqual(persons[accounts[1].pk].income,
                                                        closed.shares.get(account_id=accounts[1].pk).retained_income)
                                                    self.assertEqual(sum(item.income for item in plan.persons),
                                                                     plan_funding.provider_income_units)
                                                    self.assertTrue(all(item.blockers for item in plan.persons))
                                                    self.assertEqual(before_plan, (Transaction.objects.filter(order=order).count(),
                                                        PointLedger.objects.filter(order=order).count()))
                                                from engagement.confirmation import process_order as confirm_followup_order
                                                from engagement.models import ConsumptionConfirmation
                                                def assert_followup_not_confirmed():
                                                    with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
                                                        try:
                                                            with transaction.atomic():
                                                                result = confirm_followup_order(order.pk)
                                                        except SettlementError:
                                                            pass
                                                        else:
                                                            self.assertFalse(result)
                                                    self.assertIsNone(Order.objects.get(pk=order.pk).settled_at)
                                                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                                                    self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                                                assert_followup_not_confirmed()
                                                from orders.points_transfer_followup_settlement import prepare_locked as prepare_settlement
                                                with self.assertRaises(SettlementError):
                                                    prepare_settlement(order.pk)
                                                with override_settings(
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PREFLIGHT_TESTING=True):
                                                    with self.assertRaisesMessage(SettlementError, '门店补资尚未足额'):
                                                        prepare_settlement(order.pk)
                                                with override_settings(
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PREFLIGHT_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING=True):
                                                    from orders.services import settle_order
                                                    with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
                                                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                                                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                                                from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
                                                old_client = self.client
                                                self.client = self.platform_client
                                                registered = PlatformPassRepaymentTests.post(self, 'xinan', {**self.data,
                                                    'request_id': str(uuid4()), 'external_reference': 'SECOND-TRANSFER-FUNDING',
                                                    'amount': unpaid.remaining})
                                                self.assertEqual(registered.status_code, 200, registered.data)
                                                claim = registered.data['data']['claim_id']
                                                verified = PlatformPassRepaymentTests.action(self, claim, 'verify',
                                                    {'request_id': str(uuid4())})
                                                self.assertEqual(verified.status_code, 200, verified.data)
                                                body = {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': unpaid.remaining}
                                                url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
                                                funded = self.client.post(url, body, format='json',
                                                    HTTP_AUTHORIZATION='Bearer ' + self.token)
                                                self.assertEqual(funded.status_code, 200, funded.data)
                                                replay = self.client.post(url, body, format='json',
                                                    HTTP_AUTHORIZATION='Bearer ' + self.token)
                                                self.assertEqual(replay.data, funded.data)
                                                self.assertTrue(verify_order_funding(order.pk).fully_funded)
                                                with override_settings(
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PREFLIGHT_TESTING=True):
                                                    ready_plan, ready_terms, prior = prepare_settlement(order.pk)
                                                    self.assertIsNone(prior)
                                                    self.assertEqual(ready_terms['points_funding']['followup_posting_id'], closed.pk)
                                                    self.assertEqual(sum(person.income for person in ready_plan.persons),
                                                        detail['conditional_funding']['provider_income_units'])
                                                    with transaction.atomic():
                                                        from orders.models import OrderPointsFundingAllocation
                                                        allocated = OrderPointsFundingAllocation.objects.get(
                                                            redemption__order_id=order.pk)
                                                        OrderPointsFundingAllocation.objects.filter(pk=allocated.pk).update(
                                                            amount=allocated.amount + 10)
                                                        with self.assertRaises(SettlementError):
                                                            prepare_settlement(order.pk)
                                                        transaction.set_rollback(True)
                                                assert_followup_not_confirmed()
                                                with transaction.atomic(), override_settings(
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PREFLIGHT_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING=True,
                                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING=True):
                                                    from orders.models import OrderSegmentSettlementManifest
                                                    from orders.segment_settlement import verify_finalized
                                                    first_part = OrderParticipationSegment.objects.get(
                                                        attribution__order=order, provider_account=accounts[0])
                                                    second_part = OrderParticipationSegment.objects.get(
                                                        attribution__order=order, provider_account=accounts[1])
                                                    response = players[0].post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                                                        {'segment_id': first_part.pk, 'completion_image': screenshot()}, format='multipart')
                                                    self.assertEqual(response.data['code'], 0, response.data)
                                                    report_base = timezone.now()
                                                    for actor, part, delay in ((players[0], first_part, 0), (players[1], second_part, 48)):
                                                        with patch('django.utils.timezone.now', return_value=report_base + timedelta(hours=delay)):
                                                            for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                                                                response = actor.post(f'/api/orders/orders/{order.pk}/segment-report/',
                                                                    {'segment_id': part.pk, 'action': action,
                                                                     'client_request_id': str(uuid4()), **extra},
                                                                    format='multipart' if action == 'upload' else 'json')
                                                                self.assertEqual(response.data['code'], 0, response.data)
                                                            response = admin.post(f'/api/admin/orders/{order.pk}/segments/{part.pk}/report-review/',
                                                                {'action': 'approve', 'version': 1}, format='json')
                                                            self.assertEqual(response.data['code'], 0, response.data)
                                                    future_settlement = report_base + timedelta(hours=73)
                                                    with patch('django.utils.timezone.now', return_value=future_settlement):
                                                        before_income = Transaction.objects.filter(order=order, tx_type='INCOME').count()
                                                        with patch('orders.segment_settlement.OrderSegmentPersonSettlement.objects.create',
                                                                   side_effect=RuntimeError('segment write failed')):
                                                            with self.assertRaisesMessage(RuntimeError, 'segment write failed'):
                                                                with transaction.atomic():
                                                                    settle_order(Order.objects.select_for_update().get(pk=order.pk))
                                                        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), before_income)
                                                        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
                                                        released = settle_order(Order.objects.select_for_update().get(pk=order.pk))
                                                        self.assertEqual(set(released), {OrderProvider.objects.get(order=order,
                                                            provider_account=account).provider_id for account in (accounts[0], replacement_account)})
                                                        settled = Order.objects.select_for_update().get(pk=order.pk)
                                                        self.assertIsNone(settled.settled_at)
                                                        self.assertEqual(settle_order(settled), [])
                                                        self.assertEqual(close_followup(followup_id, proposal_id, operator=staff,
                                                            legacy_operator=operator, request_id='transfer-followup-close').pk, closed.pk)
                                                        self.assertEqual(closed_provider_accounts(settled),
                                                                         {replacement_account.pk, accounts[1].pk})
                                                        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                                                        with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_CONSUMPTION_TESTING=True):
                                                            self.assertFalse(confirm_followup_order(order.pk))
                                                            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                                                    with patch('django.utils.timezone.now', return_value=report_base + timedelta(hours=121)):
                                                        released = settle_order(Order.objects.select_for_update().get(pk=order.pk))
                                                        self.assertEqual(released, [OrderProvider.objects.get(order=order,
                                                            provider_account=accounts[1]).provider_id])
                                                        settled = Order.objects.select_for_update().get(pk=order.pk)
                                                        self.assertIsNotNone(settled.settled_at)
                                                        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
                                                        verify_finalized(settled, manifest)
                                                        self.assertEqual(settle_order(settled), [])
                                                        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                                                        with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_CONSUMPTION_TESTING=True):
                                                            from engagement.models import ClubOperationConfig, TransferRefundPointsConsumptionEvidence
                                                            from engagement.transfer_refunded_points_confirmation import verify_evidence_locked
                                                            with transaction.atomic():
                                                                operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
                                                                operation.vip_enabled = False
                                                                operation.save(update_fields=['vip_enabled'])
                                                                self.assertFalse(confirm_followup_order(order.pk))
                                                                disabled = TransferRefundPointsConsumptionEvidence.objects.get(manifest=manifest)
                                                                self.assertFalse(disabled.vip_eligible)
                                                                self.assertEqual(disabled.awarded_points, 0)
                                                                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                                                                transaction.set_rollback(True)
                                                            with transaction.atomic():
                                                                operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
                                                                operation.points_enabled = False
                                                                operation.save(update_fields=['points_enabled'])
                                                                self.assertTrue(confirm_followup_order(order.pk))
                                                                disabled = TransferRefundPointsConsumptionEvidence.objects.get(manifest=manifest)
                                                                self.assertTrue(disabled.vip_eligible)
                                                                self.assertFalse(disabled.points_eligible)
                                                                self.assertEqual(disabled.awarded_points, 0)
                                                                self.assertTrue(ConsumptionConfirmation.objects.filter(order=order).exists())
                                                                self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                                                                transaction.set_rollback(True)
                                                            points_before = MemberProfile.objects.get(account=self.account).points_balance
                                                            with patch('engagement.transfer_refunded_points_confirmation.TransferRefundPointsConsumptionEvidence.save',
                                                                       side_effect=RuntimeError('membership proof write failed')):
                                                                with self.assertRaisesMessage(RuntimeError, 'membership proof write failed'):
                                                                    with transaction.atomic():
                                                                        confirm_followup_order(order.pk)
                                                            self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, points_before)
                                                            self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                                                            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                                                            self.assertTrue(confirm_followup_order(order.pk))
                                                            self.assertFalse(confirm_followup_order(order.pk))
                                                            member = TransferRefundPointsConsumptionEvidence.objects.get(manifest=manifest)
                                                            first_proof, second_proof = posting.points_refund, closed.points_refund
                                                            self.assertEqual(member.posting_id, closed.pk)
                                                            self.assertEqual(member.refunded_coin_units,
                                                                first_proof.coin_units + second_proof.coin_units)
                                                            self.assertEqual(member.refunded_principal_units,
                                                                first_proof.prepaid_units + second_proof.prepaid_units)
                                                            self.assertEqual(member.net_paid_units,
                                                                order.amount - first_proof.coin_units - second_proof.coin_units)
                                                            self.assertEqual(member.awarded_points,
                                                                member.net_principal_units * member.points_rate * member.birthday_multiplier // 100)
                                                            self.assertEqual(verify_evidence_locked(member).pk, member.pk)
                                                            with transaction.atomic():
                                                                refund_tx = first_proof.coin_transaction
                                                                Transaction.objects.filter(pk=refund_tx.pk).update(amount=refund_tx.amount + 10)
                                                                with self.assertRaises(ValidationError):
                                                                    verify_evidence_locked(member)
                                                                transaction.set_rollback(True)
                                                            self.assertEqual(close_followup(followup_id, proposal_id, operator=staff,
                                                                legacy_operator=operator, request_id='transfer-followup-close').pk, closed.pk)
                                                            with override_settings(**endpoint_flags):
                                                                self.assertEqual(admin.post(endpoint, execution, format='json').data['data']['execution_id'], closed.pk)
                                                                self.assertEqual(admin.get(endpoint, execution).data['data']['execution_id'], closed.pk)
                                                                with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_ENDPOINT_TESTING=False):
                                                                    self.assertEqual(admin.get(endpoint, execution).data['data']['execution_id'], closed.pk)
                                                                    self.assertGreaterEqual(admin.post(endpoint, execution, format='json').status_code, 400)
                                                            self.assertEqual(OrderRefund.objects.filter(order=order).count(), 2)
                                                            self.assertEqual(PointLedger.objects.filter(order=order, kind='CONSUMPTION').count(),
                                                                             int(member.awarded_points > 0))
                                                    transaction.set_rollback(True)
                                                self.client = old_client
                                            directory_transaction.set_rollback(True, using='default')
                                            transaction.set_rollback(True)
                            with patch('orders.points_followup_termination.endpoint_available', return_value=True):
                                status = admin.get(f'/api/admin/aftersales/{followup_id}/negotiation-execution/')
                                self.assertFalse(status.data['data']['execution_enabled'])
                                summary = admin.get(f'/api/admin/aftersales/{followup_id}/negotiation-proposal/')
                                self.assertFalse(summary.data['data']['execution_available'])
                                denied = admin.post(f'/api/admin/aftersales/{followup_id}/negotiation-execution/',
                                    {'proposal_id': proposal_id, 'client_request_id': 'transfer-followup-execute'}, format='json')
                                self.assertGreaterEqual(denied.status_code, 400)
                            self.assertEqual(before, (OrderRefund.objects.filter(order=order).count(),
                                Transaction.objects.filter(order=order).count(), PointLedger.objects.filter(order=order).count()))
                            transaction.set_rollback(True)
                        with transaction.atomic():
                            refund_tx = posting.points_refund.coin_transaction
                            Transaction.objects.filter(pk=refund_tx.pk).update(amount=refund_tx.amount + 10)
                            with self.assertRaises(ValidationError):
                                transfer_followup_preview(followup_id, refund_equivalent_units=1000)
                            transaction.set_rollback(True)
                        with transaction.atomic():
                            from orders.models import AfterSale
                            AfterSale.objects.filter(pk=followup_id).update(
                                affected_provider_account_ids=[replacement_account.pk])
                            with self.assertRaises(ValidationError):
                                transfer_followup_preview(followup_id, refund_equivalent_units=1000)
                            transaction.set_rollback(True)
                        self.assertEqual(before, (OrderRefund.objects.filter(order=order).count(),
                            Transaction.objects.filter(order=order).count(), PointLedger.objects.filter(order=order).count()))
                        with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_PREVIEW_TESTING=False):
                            denied = admin.get(f'/api/admin/aftersales/{followup_id}/negotiation-preview/?refund_units=1000')
                            self.assertNotEqual(denied.data.get('code', 400), 0)
                        transaction.set_rollback(True)
                    first_segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=accounts[0])
                    second_segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=accounts[1])
                    original_evidence = players[0].post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                        {'segment_id': first_segment.pk, 'completion_image': screenshot()}, format='multipart')
                    self.assertEqual(original_evidence.data['code'], 0, original_evidence.data)
                    for actor, part in ((players[0], first_segment), (players[1], second_segment)):
                        for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                            report = actor.post(f'/api/orders/orders/{order.pk}/segment-report/',
                                {'segment_id': part.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                                format='multipart' if action == 'upload' else 'json')
                            self.assertEqual(report.data['code'], 0, report.data)
                        reviewed = admin.post(f'/api/admin/orders/{order.pk}/segments/{part.pk}/report-review/',
                            {'action': 'approve', 'version': 1}, format='json')
                        self.assertEqual(reviewed.data['code'], 0, reviewed.data)
                    from engagement.confirmation import process_order
                    from engagement.models import ConsumptionConfirmation, TransferRefundPointsConsumptionEvidence
                    from orders.points_funding_evidence import verify_order_funding
                    result = verify_order_funding(order.pk)
                    self.assertGreater(result.remaining, 0)
                    future = timezone.now() + timedelta(hours=73)
                    with patch('django.utils.timezone.now', return_value=future):
                        self.assertFalse(process_order(order.pk))
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                    from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
                    self.client = self.platform_client
                    registered = PlatformPassRepaymentTests.post(self, 'xinan', {**self.data,
                        'request_id': str(uuid4()), 'external_reference': 'TX-MIXED-TRANSFER', 'amount': result.remaining})
                    self.assertEqual(registered.status_code, 200, registered.data)
                    claim = registered.data['data']['claim_id']
                    verified = PlatformPassRepaymentTests.action(self, claim, 'verify', {'request_id': str(uuid4())})
                    self.assertEqual(verified.status_code, 200, verified.data)
                    funded = self.client.post(f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation',
                        {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': result.remaining}, format='json',
                        HTTP_AUTHORIZATION='Bearer ' + self.token)
                    self.assertEqual(funded.status_code, 200, funded.data)
                    with transaction.atomic():
                        reopened = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {
                            'reason': '另一位陪玩申请售后', 'affected_provider_account_ids': [accounts[1].pk],
                            'client_request_id': 'transfer-followup-paid'}, format='json')
                        self.assertEqual(reopened.data['code'], 0, reopened.data)
                        followup_id = reopened.data['data']['id']
                        with patch('django.utils.timezone.now', return_value=future):
                            self.assertFalse(process_order(order.pk))
                            paid = list(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).order_by('pk'))
                            self.assertTrue(paid)
                            self.assertNotIn(accounts[1].pk, [row.account_id for row in paid])
                            with transaction.atomic(), override_settings(
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING=True):
                                from orders.points_transfer_refund_evidence import _prior_settlement
                                from orders.points_transfer_followup_revision import _verify_paid_manifest
                                from orders.points_transfer_refund_evidence import (
                                    _verify_facts_locked, _reconstruct_from_verified_facts,
                                    _verify_revisions_from_verified_facts, reconstruct_funding_locked,
                                    verify_transfer_revisions_after_refund_locked, digest as transfer_facts_digest)
                                completed_order = Order.objects.select_for_update().get(pk=order.pk)
                                before_final = _prior_settlement(completed_order)
                                _, facts, _ = _verify_facts_locked(order.pk, posting.participants)
                                boundary = facts['transfers'][-1]['id']
                                original_funding = reconstruct_funding_locked(order.pk, posting.participants,
                                    refund_id=posting.points_refund.refund_id, through_transfer_id=boundary)
                                original_releases = verify_transfer_revisions_after_refund_locked(order.pk,
                                    posting.participants, refund_id=posting.points_refund.refund_id)
                                resolved = admin.post(f'/api/admin/aftersales/{followup_id}/resolve/',
                                    {'resolution': '已处理，不影响继续履约'}, format='json')
                                self.assertEqual(resolved.data['code'], 0, resolved.data)
                                self.assertTrue(process_order(order.pk))
                                all_paid = list(OrderSegmentPersonSettlement.objects.filter(manifest__order=order))
                                self.assertGreater(len(all_paid), len(paid))
                                from orders.points_negotiation_execution import _verify_transfer_posting_facts_locked
                                checked_posting, checked_facts, checked_history = _verify_transfer_posting_facts_locked(posting)
                                self.assertEqual(checked_posting.pk, posting.pk)
                                self.assertEqual(checked_facts, facts)
                                self.assertEqual(checked_history, original_funding)
                                _, facts_after, _ = _verify_facts_locked(order.pk, posting.participants)
                                self.assertEqual(_reconstruct_from_verified_facts(order.pk, facts_after,
                                    refund_id=posting.points_refund.refund_id, through_transfer_id=boundary), original_funding)
                                self.assertEqual(_verify_revisions_from_verified_facts(order.pk, facts_after,
                                    refund_id=posting.points_refund.refund_id), original_releases)
                                forged_facts = deepcopy(posting.participants)
                                forged_facts['facts']['segments'][0]['incoming_basis'] += 10
                                forged_facts['digest'] = transfer_facts_digest(forged_facts['facts'])
                                with self.assertRaises(ValidationError):
                                    _verify_facts_locked(order.pk, forged_facts)
                                with self.assertRaises(ValidationError):
                                    _verify_paid_manifest(completed_order, before_final, cutoff=future)
                                _verify_paid_manifest(completed_order, before_final, cutoff=future, allow_later=True)
                                with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING=False):
                                    with self.assertRaises(ValidationError):
                                        _verify_paid_manifest(completed_order, before_final, cutoff=future, allow_later=True)
                                with transaction.atomic():
                                    OrderSegmentPersonSettlement.objects.filter(pk=paid[0].pk).update(amount=paid[0].amount + 10)
                                    with self.assertRaises(ValidationError):
                                        _verify_paid_manifest(completed_order, before_final, cutoff=future, allow_later=True)
                                    transaction.set_rollback(True)
                                with transaction.atomic():
                                    tampered = deepcopy(before_final)
                                    tampered['people'][0]['income_transaction_id'] = None
                                    with self.assertRaises(ValidationError):
                                        _verify_paid_manifest(completed_order, tampered, cutoff=future, allow_later=True)
                                    transaction.set_rollback(True)
                                tampered = deepcopy(before_final)
                                tampered['plan']['people'][0]['income'] += 10
                                with self.assertRaises(ValidationError):
                                    _verify_paid_manifest(completed_order, tampered, cutoff=future, allow_later=True)
                                transaction.set_rollback(True)
                            detail = transfer_followup_preview(followup_id, refund_equivalent_units=1000)
                            self.assertEqual(detail['settled_other_provider_units'], sum(row.amount for row in paid))
                            self.assertEqual(detail['conditional_funding_revision']['paid_provider_units'],
                                             sum(row.amount for row in paid))
                            self.assertFalse(detail['execution_available'])
                            with transaction.atomic(), override_settings(
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_AGREEMENT_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_EXECUTION_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_POSTING_TESTING=True,
                                    SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_TESTING=True,
                                    SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True):
                                version = (NegotiationProposal.objects.filter(case_id=followup_id)
                                           .order_by('-version').values_list('version', flat=True).first() or 0)
                                term = detail['provider_terms'][0]
                                proposed = support_client.post(f'/api/admin/aftersales/{followup_id}/negotiation-proposal/',
                                    {'request_id': 'transfer-followup-paid-consent', 'expected_version': version,
                                     'refund_units': 1000, 'provider_terms': [{'account_id': accounts[1].pk,
                                        'retained_units': term['retained_units']}], 'explanation': '双方确认后续退款'}, format='json')
                                self.assertEqual(proposed.data['code'], 0, proposed.data)
                                proposal_id = proposed.data['data']['id']
                                for actor in (boss, players[1]):
                                    agreed = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                        {'proposal_id': proposal_id, 'accepted': True}, format='json')
                                    self.assertEqual(agreed.data['code'], 0, agreed.data)
                                prior_people = [(row.pk, row.amount, row.income_transaction_id) for row in paid]
                                from orders.points_transfer_followup_execution import post_locked as post_transfer_followup
                                from orders.models import OrderPointsFundingAllocation, OrderPointsTransferFollowupFundingRevision
                                from orders.points_transfer_followup_revision import verify_locked as verify_followup_revision
                                with transaction.atomic(), override_settings(
                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_TESTING=True,
                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING=True,
                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_FUNDING_TESTING=True,
                                        SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PREFLIGHT_TESTING=True):
                                    from orders.points_transfer_followup_termination import execute_locked as close_followup
                                    from orders.points_transfer_followup_settlement_plan import build_locked as followup_plan
                                    closed = close_followup(followup_id, proposal_id, operator=staff,
                                        legacy_operator=operator, request_id='transfer-followup-paid-close')
                                    plan, funding = followup_plan(order.pk)
                                    persons = {person.account_id: person for person in plan.persons}
                                    for paid_row in paid:
                                        self.assertEqual(persons[paid_row.account_id].income, paid_row.amount)
                                        self.assertEqual(list(persons[paid_row.account_id].segment_ids), paid_row.segment_ids)
                                    self.assertEqual(persons[accounts[1].pk].income,
                                        closed.shares.get(account_id=accounts[1].pk).retained_income)
                                    self.assertEqual(sum(person.income for person in plan.persons), funding.provider_income_units)
                                    from orders.points_transfer_followup_settlement import prepare_locked as prepare_settlement
                                    checked, terms, prior = prepare_settlement(order.pk)
                                    self.assertEqual(terms['points_funding']['followup_posting_id'], closed.pk)
                                    self.assertEqual([row['id'] for row in prior['people']], [row.pk for row in paid])
                                    self.assertEqual([(person.account_id, person.income) for person in checked.persons],
                                                     [(person.account_id, person.income) for person in plan.persons])
                                    with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING=True,
                                            SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING=True,
                                            SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING=True,
                                            SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING=True):
                                        from orders.points_transfer_followup_revision import _verify_followup_revision_facts_locked
                                        revision = OrderPointsTransferFollowupFundingRevision.objects.get(posting=closed)
                                        self.assertEqual(_verify_followup_revision_facts_locked(revision),
                                                         verify_followup_revision(revision))
                                        from orders.points_funding_release import receipt_points_used_locked
                                        allocation = OrderPointsFundingAllocation.objects.get(redemption__order=order)
                                        receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=allocation.receipt_id)
                                        expected_used = receipt_points_used_locked(receipt)
                                        with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING=True):
                                            self.assertEqual(receipt_points_used_locked(receipt), expected_used)
                                            with directory_transaction.atomic(using='default'), transaction.atomic():
                                                from stores.test_platform_pass_refunds import PlatformPassRefundTests
                                                released_units = sum(item[2] for item in revision.releases)
                                                self.assertGreater(released_units, 0)
                                                pass_refund = PlatformPassRefundTests.create_shortfall(self, released_units + 1)
                                                rejected = PlatformPassRepaymentTests.action(self, claim, 'allocate',
                                                    {'request_id': str(uuid4()), 'refund_id': pass_refund.pk,
                                                     'amount': released_units + 1})
                                                self.assertNotEqual(rejected.status_code, 200, rejected.data)
                                                reuse = {'request_id': str(uuid4()), 'refund_id': pass_refund.pk,
                                                         'amount': released_units}
                                                allocated = PlatformPassRepaymentTests.action(self, claim, 'allocate', reuse)
                                                self.assertEqual(allocated.status_code, 200, allocated.data)
                                                self.assertEqual(PlatformPassRepaymentTests.action(self, claim, 'allocate', reuse).data,
                                                                 allocated.data)
                                                self.assertEqual(receipt_points_used_locked(receipt), expected_used)
                                                directory_transaction.set_rollback(True, using='default')
                                                transaction.set_rollback(True)
                                        with transaction.atomic():
                                            from orders.points_transfer_followup_revision import _digest as followup_digest
                                            revision.prior_manifest = None
                                            revision.evidence_hash = followup_digest(closed, revision.first_posting_id,
                                                None, revision.allocations, revision.releases, revision.funding,
                                                revision.revision, revision.prior_required_units)
                                            revision.save(update_fields=['prior_manifest', 'evidence_hash'])
                                            with self.assertRaises(ValidationError):
                                                _verify_followup_revision_facts_locked(revision)
                                            with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING=True):
                                                with self.assertRaises(ValidationError):
                                                    receipt_points_used_locked(receipt)
                                            transaction.set_rollback(True)
                                        revision.refresh_from_db()
                                    with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING=True,
                                            SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING=False):
                                        with self.assertRaises(ValidationError):
                                            receipt_points_used_locked(receipt)
                                    with transaction.atomic():
                                        OrderSegmentPersonSettlement.objects.filter(pk=paid[0].pk).update(amount=paid[0].amount + 10)
                                        with self.assertRaises(SettlementError):
                                            prepare_settlement(order.pk)
                                        transaction.set_rollback(True)
                                    self.assertEqual(prior_people, list(OrderSegmentPersonSettlement.objects.filter(
                                        pk__in=[row.pk for row in paid]).order_by('pk')
                                        .values_list('pk', 'amount', 'income_transaction_id')))
                                    with transaction.atomic(), override_settings(
                                            SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING=True,
                                            SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING=True,
                                            SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING=True,
                                            SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING=True,
                                            SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING=True,
                                            SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING=True):
                                        from orders.services import settle_order
                                        from orders.segment_settlement import verify_finalized
                                        from orders.models import OrderSegmentSettlementManifest, OrderSegmentSettlementRevision
                                        with patch('django.utils.timezone.now', return_value=future + timedelta(seconds=1)):
                                            previous_manifest = OrderSegmentSettlementManifest.objects.get(order=order)
                                            original_plan = deepcopy(previous_manifest.plan)
                                            original_income_count = Transaction.objects.filter(order=order, tx_type='INCOME').count()
                                            with patch('orders.models.OrderSegmentSettlementRevision.objects.create',
                                                       side_effect=RuntimeError('settlement version write failed')):
                                                with self.assertRaisesMessage(RuntimeError, 'settlement version write failed'):
                                                    with transaction.atomic():
                                                        settle_order(Order.objects.select_for_update().get(pk=order.pk))
                                            self.assertEqual(OrderSegmentSettlementManifest.objects.get(order=order).plan, original_plan)
                                            self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(),
                                                             original_income_count)
                                            released = settle_order(Order.objects.select_for_update().get(pk=order.pk))
                                            self.assertEqual(released, [OrderProvider.objects.get(
                                                order=order, provider_account=accounts[1]).provider_id])
                                            settled = Order.objects.select_for_update().get(pk=order.pk)
                                            self.assertIsNotNone(settled.settled_at)
                                            manifest = OrderSegmentSettlementManifest.objects.get(order=order)
                                            self.assertEqual(OrderSegmentSettlementRevision.objects.filter(
                                                manifest=manifest, posting=closed).count(), 1)
                                            verify_finalized(settled, manifest)
                                            with transaction.atomic():
                                                version_row = OrderSegmentSettlementRevision.objects.get(manifest=manifest, posting=closed)
                                                version_row.before_plan_hash = '0' * 64
                                                version_row.save(update_fields=['before_plan_hash'])
                                                with self.assertRaises(SettlementError):
                                                    verify_finalized(settled, manifest)
                                                transaction.set_rollback(True)
                                            with transaction.atomic():
                                                from orders.segment_settlement import _hash as settlement_hash
                                                changed_plan = deepcopy(manifest.plan)
                                                next(item for item in changed_plan['people']
                                                     if item['account_id'] == accounts[1].pk)['income'] += 10
                                                manifest.plan = changed_plan
                                                manifest.plan_hash = settlement_hash(changed_plan)
                                                manifest.save(update_fields=['plan', 'plan_hash'])
                                                version_row = OrderSegmentSettlementRevision.objects.get(manifest=manifest, posting=closed)
                                                version_row.after_plan = changed_plan
                                                version_row.after_plan_hash = manifest.plan_hash
                                                version_row.save(update_fields=['after_plan', 'after_plan_hash'])
                                                with self.assertRaises(SettlementError):
                                                    verify_finalized(settled, manifest)
                                                transaction.set_rollback(True)
                                            manifest.refresh_from_db()
                                            with transaction.atomic():
                                                OrderSegmentSettlementRevision.objects.filter(manifest=manifest, posting=closed).delete()
                                                with self.assertRaises(SettlementError):
                                                    verify_finalized(settled, manifest)
                                                transaction.set_rollback(True)
                                            self.assertEqual(settle_order(settled), [])
                                            self.assertEqual(prior_people, list(OrderSegmentPersonSettlement.objects.filter(
                                                pk__in=[row.pk for row in paid]).order_by('pk')
                                                .values_list('pk', 'amount', 'income_transaction_id')))
                                            with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_CONSUMPTION_TESTING=True):
                                                self.assertTrue(process_order(order.pk))
                                                self.assertFalse(process_order(order.pk))
                                                member = TransferRefundPointsConsumptionEvidence.objects.get(manifest=manifest)
                                                self.assertEqual(member.posting_id, closed.pk)
                                                self.assertEqual(member.refunded_coin_units,
                                                    posting.points_refund.coin_units + closed.points_refund.coin_units)
                                                self.assertEqual(member.refunded_principal_units,
                                                    posting.points_refund.prepaid_units + closed.points_refund.prepaid_units)
                                                self.assertEqual(close_followup(followup_id, proposal_id, operator=staff,
                                                    legacy_operator=operator, request_id='transfer-followup-paid-close').pk, closed.pk)
                                                self.assertEqual(OrderRefund.objects.filter(order=order).count(), 2)
                                        transaction.set_rollback(True)
                                    transaction.set_rollback(True)
                                with patch('orders.points_transfer_followup_revision.OrderPointsFundingRelease.objects.bulk_create',
                                           side_effect=RuntimeError('followup release failed')):
                                    with self.assertRaisesMessage(RuntimeError, 'followup release failed'):
                                        post_transfer_followup(followup_id, proposal_id, operator=staff,
                                            legacy_operator=operator, request_id='transfer-followup-paid-post')
                                self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)
                                self.assertFalse(OrderPointsTransferFollowupFundingRevision.objects.filter(
                                    posting__proposal_id=proposal_id).exists())
                                self.assertEqual(prior_people, list(OrderSegmentPersonSettlement.objects.filter(
                                    pk__in=[row.pk for row in paid]).order_by('pk').values_list('pk', 'amount', 'income_transaction_id')))
                                second = post_transfer_followup(followup_id, proposal_id, operator=staff,
                                    legacy_operator=operator, request_id='transfer-followup-paid-post')
                                revision = OrderPointsTransferFollowupFundingRevision.objects.get(posting=second)
                                projected, reserved = verify_followup_revision(revision)
                                self.assertEqual(reserved.paid_provider_units, sum(row.amount for row in paid))
                                self.assertEqual(prior_people, list(OrderSegmentPersonSettlement.objects.filter(
                                    pk__in=[row.pk for row in paid]).order_by('pk').values_list('pk', 'amount', 'income_transaction_id')))
                                self.assertEqual(projected.net_coin_units,
                                    order.amount - posting.points_refund.coin_units - second.points_refund.coin_units)
                                with transaction.atomic():
                                    from orders.points_transfer_followup_revision import _digest as followup_digest
                                    forged = deepcopy(revision.prior_manifest)
                                    late = second.points_refund.refund.created_at + timedelta(seconds=1)
                                    forged['people'][0]['settled_at'] = late.isoformat()
                                    OrderSegmentPersonSettlement.objects.filter(pk=forged['people'][0]['id']).update(settled_at=late)
                                    revision.prior_manifest = forged
                                    revision.evidence_hash = followup_digest(second, revision.first_posting_id, forged,
                                        revision.allocations, revision.releases, revision.funding, revision.revision,
                                        revision.prior_required_units)
                                    revision.save(update_fields=['prior_manifest', 'evidence_hash'])
                                    with patch('django.utils.timezone.now', return_value=late + timedelta(seconds=1)):
                                        with self.assertRaises(ValidationError):
                                            verify_followup_revision(revision)
                                    transaction.set_rollback(True)
                                with transaction.atomic():
                                    from orders.segment_settlement import _hash
                                    from orders.points_transfer_followup_revision import _digest as followup_digest
                                    revision.refresh_from_db()
                                    forged = deepcopy(revision.prior_manifest)
                                    next(item for item in forged['plan']['people']
                                         if item['account_id'] == accounts[1].pk)['income'] += 10
                                    forged['plan_hash'] = _hash(forged['plan'])
                                    from orders.models import OrderSegmentSettlementManifest
                                    OrderSegmentSettlementManifest.objects.filter(pk=forged['manifest_id']).update(
                                        plan=forged['plan'], plan_hash=forged['plan_hash'])
                                    revision.prior_manifest = forged
                                    revision.evidence_hash = followup_digest(second, revision.first_posting_id, forged,
                                        revision.allocations, revision.releases, revision.funding, revision.revision,
                                        revision.prior_required_units)
                                    revision.save(update_fields=['prior_manifest', 'evidence_hash'])
                                    with self.assertRaises(ValidationError):
                                        verify_followup_revision(revision)
                                    transaction.set_rollback(True)
                                from orders.points_funding_release import receipt_points_used_locked
                                allocation = OrderPointsFundingAllocation.objects.get(redemption__order=order)
                                receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=allocation.receipt_id)
                                self.assertEqual(receipt_points_used_locked(receipt),
                                                 reserved.allocated_units - reserved.release_units)
                                with transaction.atomic():
                                    from orders.models import OrderPointsFundingReleaseEvidence
                                    OrderPointsFundingReleaseEvidence.objects.create(posting=second, allocations=[],
                                        original_provider_income=0, expected_release=0, evidence_hash='0' * 64)
                                    with self.assertRaises(ValidationError):
                                        receipt_points_used_locked(receipt)
                                    transaction.set_rollback(True)
                                with transaction.atomic():
                                    from orders.points_transfer_followup_revision import _digest as followup_digest
                                    revision.refresh_from_db()
                                    changed = deepcopy(revision.funding)
                                    changed['required_units'] += 10
                                    revision.funding = changed
                                    revision.evidence_hash = followup_digest(second, revision.first_posting_id,
                                        revision.prior_manifest, revision.allocations, revision.releases,
                                        changed, revision.revision, revision.prior_required_units)
                                    revision.save(update_fields=['funding', 'evidence_hash'])
                                    with self.assertRaises(ValidationError):
                                        receipt_points_used_locked(receipt)
                                    transaction.set_rollback(True)
                                self.assertGreater(reserved.release_units, 0)
                                from stores.test_platform_pass_refunds import PlatformPassRefundTests
                                pass_refund = PlatformPassRefundTests.create_shortfall(self, reserved.release_units + 1)
                                denied = PlatformPassRepaymentTests.action(self, claim, 'allocate',
                                    {'request_id': str(uuid4()), 'refund_id': pass_refund.pk,
                                     'amount': reserved.release_units + 1})
                                self.assertNotEqual(denied.status_code, 200, denied.data)
                                reused = PlatformPassRepaymentTests.action(self, claim, 'allocate',
                                    {'request_id': str(uuid4()), 'refund_id': pass_refund.pk,
                                     'amount': reserved.release_units})
                                self.assertEqual(reused.status_code, 200, reused.data)
                                with transaction.atomic():
                                    revision.evidence_hash = '0' * 64
                                    revision.save(update_fields=['evidence_hash'])
                                    with self.assertRaises(ValidationError):
                                        receipt_points_used_locked(receipt)
                                    transaction.set_rollback(True)
                                transaction.set_rollback(True)
                        transaction.set_rollback(True)
                    with patch('django.utils.timezone.now', return_value=future):
                        self.assertTrue(process_order(order.pk))
                        self.assertFalse(process_order(order.pk))
                    member = TransferRefundPointsConsumptionEvidence.objects.get(manifest__order=order)
                    proof = posting.points_refund
                    self.assertEqual(member.net_paid_units, order.amount - proof.coin_units)
                    self.assertEqual(member.net_principal_units, order.amount - 200 - proof.prepaid_units)
                    self.assertEqual(member.net_paid_units - member.net_principal_units, 200)
                    self.assertEqual(member.awarded_points, member.net_principal_units * member.points_rate * member.birthday_multiplier // 100)
                    self.assertEqual(ConsumptionConfirmation.objects.get(order=order).refunded_amount, proof.coin_units)
                    directory_transaction.set_rollback(True, using='default')
                    transaction.set_rollback(True)

    def test_isolated_transfer_posting_returns_assets_and_debits_provider_atomically(self):
        from orders.points_negotiation_execution import post_locked, verify_posting_locked
        with TemporaryDirectory(prefix='xa-points-transfer-post-') as media, override_settings(
                MEDIA_ROOT=media, SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                SERVICE_POINTS_NEGOTIATION_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_AGREEMENT_TESTING=True,
                SERVICE_POINTS_TRANSFER_REFUND_INTERNAL_AGREEMENT_TESTING=True,
                SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING=True, SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True,
                REFUND_DEPOSIT_DEFICIT_ENABLED=True):
            self.assertEqual(self.verify().status_code, 200)
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                original = OrderProvider.objects.get(order=order, released_at__isnull=True).provider_account
                original_client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(original)[0])
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
                self.assertEqual(original_client.post(f'/api/orders/orders/{order.pk}/start/',
                    {'entry_image': screenshot()}, format='multipart').data['code'], 0)
                replacement = make_provider(deposit_mode='EARNINGS')
                new = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = new; replacement.escort_profile.save(update_fields=['account'])
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(new)[0])
                staff_user = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'support:edit',
                                                'escort:view', 'escort:deposit_topup', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                self.service.commission_override = True; self.service.commission_rate = 30
                self.service.save(update_fields=['commission_override', 'commission_rate'])
                transfer = admin.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方同意',
                    'transfers': [{'old_provider_id': original.pk, 'new_provider_id': new.pk, 'split_type': 'PERCENT',
                        'split_value': 0, 'penalty_amount': 0, 'original_share': {'retained': 4000,
                            'transferred': 6000, 'agreement': '双方同意'}}]}, format='json')
                self.assertEqual(transfer.data['code'], 0, transfer.data)
                segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=new)
                start = player.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': segment.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(start.data['code'], 0, start.data)
                opened = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                    'reason': '双方协商退款', 'affected_provider_account_ids': [new.pk],
                    'client_request_id': 'transfer-post-case'}, format='json')
                self.assertEqual(opened.data['code'], 0, opened.data)
                case_id = opened.data['data']['id']
                # A fixture balance and a real, isolated cash-deposit top-up
                # exercise both wallet and backed deposit recovery.
                Wallet.objects.filter(user=replacement).update(balance=200)
                profile = replacement.escort_profile
                profile.deposit_mode = 'CASH'; profile.deposit_paid = 0; profile.deposit_required = 600
                profile.save(update_fields=['deposit_mode', 'deposit_paid', 'deposit_required'])
                topup = admin.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/',
                    {'amount': 600, 'reason': '隔离测试线下押金', 'reference': 'TRANSFER-POST-DEPOSIT',
                     'client_request_id': 'transfer-post-deposit'}, format='multipart')
                self.assertEqual(topup.data['code'], 0, topup.data)
                with override_settings(SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING=True,
                        SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_FUNDING_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_REPORT_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_SETTLEMENT_TESTING=True,
                        SERVICE_POINTS_CONSUMPTION_TESTING=True,
                        SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_CONSUMPTION_TESTING=True,
                        NEGOTIATION_REFUND_EXECUTION_ENABLED=True), directory_transaction.atomic(using='default'), transaction.atomic():
                    type(profile).objects.filter(pk=profile.pk).update(deposit_mode='EARNINGS')
                    proposal = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',
                        {'request_id': 'transfer-funding-proposal', 'expected_version': 0, 'refund_units': 1000,
                         'provider_terms': [{'account_id': new.pk, 'retained_units': 1520}],
                         'explanation': '双方确认退款和后续补资'}, format='json')
                    self.assertEqual(proposal.data['code'], 0, proposal.data)
                    proposal_id = proposal.data['data']['id']
                    for actor in (boss, player):
                        response = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                            {'proposal_id': proposal_id, 'accepted': True}, format='json')
                        self.assertEqual(response.data['code'], 0, response.data)
                    from orders.points_negotiation_termination import execute_and_terminate
                    from orders.points_funding_evidence import verify_order_funding
                    funded_posting = execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                        request_id='transfer-funding-close', _transfer_testing=True)
                    result = verify_order_funding(order.pk)
                    self.assertEqual((result.required, result.allocated, result.remaining), (100, 0, 100))
                    from orders.services import settle_order
                    from engagement.models import ConsumptionConfirmation
                    from engagement.confirmation import process_order
                    base_time = timezone.now()
                    for actor, part, delay in ((original_client, OrderParticipationSegment.objects.get(
                            attribution__order=order, provider_account=original), 0), (player, segment, 48)):
                        with patch('django.utils.timezone.now', return_value=base_time + timedelta(hours=delay)):
                            evidence = actor.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                                {'segment_id': part.pk, 'completion_image': screenshot()}, format='multipart')
                            self.assertEqual(evidence.data['code'], 0, evidence.data)
                            for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                                report = actor.post(f'/api/orders/orders/{order.pk}/segment-report/',
                                    {'segment_id': part.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                                    format='multipart' if action == 'upload' else 'json')
                                self.assertEqual(report.data['code'], 0, report.data)
                            reviewed = admin.post(f'/api/admin/orders/{order.pk}/segments/{part.pk}/report-review/',
                                {'action': 'approve', 'version': 1}, format='json')
                            self.assertEqual(reviewed.data['code'], 0, reviewed.data)
                    future = base_time + timedelta(hours=73)
                    with patch('django.utils.timezone.now', return_value=future):
                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                        with override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                               SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                            self.assertFalse(process_order(order.pk))
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                    funding = {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 100}
                    added = self.fund(funding)
                    self.assertEqual(added.status_code, 200, added.data)
                    self.assertEqual(self.fund(funding).data, added.data)
                    result = verify_order_funding(order.pk)
                    self.assertEqual((result.required, result.allocated, result.remaining), (100, 100, 0))
                    excessive = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 1})
                    self.assertNotEqual(excessive.status_code, 200, excessive.data)
                    with patch('django.utils.timezone.now', return_value=base_time + timedelta(hours=71)):
                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                    with patch('django.utils.timezone.now', return_value=future):
                        paid = settle_order(Order.objects.select_for_update().get(pk=order.pk))
                        self.assertEqual(paid, [order.providers.get(provider_account=original).provider_id])
                        self.assertFalse(Order.objects.get(pk=order.pk).settled_at)
                        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                        self.assertFalse(process_order(order.pk))
                    with patch('django.utils.timezone.now', return_value=base_time + timedelta(hours=121)), transaction.atomic():
                        original_credit = OrderSegmentPersonSettlement.objects.get(manifest__order=order, account=original)
                        type(original_credit).objects.filter(pk=original_credit.pk).update(settled_at=base_time + timedelta(hours=1))
                        from orders.settlement import SettlementError
                        with self.assertRaises(SettlementError):
                            settle_order(Order.objects.select_for_update().get(pk=order.pk))
                        self.assertFalse(OrderSegmentPersonSettlement.objects.filter(manifest__order=order, account=new).exists())
                        transaction.set_rollback(True)
                    with patch('django.utils.timezone.now', return_value=base_time + timedelta(hours=121)):
                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [replacement.pk])
                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                        settled = Order.objects.get(pk=order.pk)
                        from orders.models import OrderSegmentSettlementManifest
                        from orders.segment_settlement import verify_finalized
                        from orders.settlement import SettlementError
                        manifest = OrderSegmentSettlementManifest.objects.get(order=settled)
                        verify_finalized(settled, manifest)
                        from orders.points_transfer_refund_release import (
                            verify_release_locked as verify_transfer_release,
                            _verify_release_facts_locked as verify_release_facts)
                        marker = funded_posting.funding_release_evidence
                        with self.assertRaises(ValidationError):
                            verify_release_facts(marker)
                        with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING=True):
                            self.assertEqual(verify_release_facts(marker), verify_transfer_release(marker))
                            from orders.points_negotiation_termination import _verify_transfer_termination_facts_locked
                            with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING=True):
                                self.assertEqual(_verify_transfer_termination_facts_locked(funded_posting).pk,
                                                 funded_posting.pk)
                                with transaction.atomic():
                                    from console.models import AdminAuditLog
                                    AdminAuditLog.objects.filter(
                                        path=f'/api/admin/aftersales/{case_id}/negotiation-execution/',
                                        object_id=str(case_id)).delete()
                                    with self.assertRaises(ValidationError):
                                        _verify_transfer_termination_facts_locked(funded_posting)
                                    transaction.set_rollback(True)
                            with transaction.atomic():
                                marker.expected_release += 1; marker.save(update_fields=['expected_release'])
                                with self.assertRaises(ValidationError):
                                    verify_release_facts(marker)
                                transaction.set_rollback(True)
                            marker.refresh_from_db()
                        with transaction.atomic():
                            marker = funded_posting.funding_release_evidence
                            marker.expected_release += 1; marker.save(update_fields=['expected_release'])
                            with self.assertRaises(SettlementError):
                                verify_finalized(settled, manifest)
                            transaction.set_rollback(True)
                        from engagement.models import ClubOperationConfig, TransferRefundPointsConsumptionEvidence
                        from engagement.transfer_refunded_points_confirmation import verify_evidence_locked
                        with transaction.atomic():
                            operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
                            operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
                            self.assertFalse(process_order(order.pk))
                            self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                            operation.vip_enabled = True; operation.save(update_fields=['vip_enabled'])
                            self.assertFalse(process_order(order.pk))
                            transaction.set_rollback(True)
                        with transaction.atomic():
                            operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
                            operation.points_enabled = False; operation.save(update_fields=['points_enabled'])
                            self.assertTrue(process_order(order.pk))
                            self.assertTrue(ConsumptionConfirmation.objects.filter(order=order).exists())
                            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                            operation.points_enabled = True; operation.save(update_fields=['points_enabled'])
                            self.assertFalse(process_order(order.pk))
                            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                            transaction.set_rollback(True)
                        from engagement.models import LicenseConsumptionPointsDecision
                        with transaction.atomic(), override_settings(LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True):
                            self.assertTrue(process_order(order.pk))
                            evidence = TransferRefundPointsConsumptionEvidence.objects.get(manifest=manifest)
                            self.assertTrue(evidence.vip_eligible)
                            self.assertFalse(evidence.points_eligible)
                            self.assertEqual(evidence.awarded_points, 0)
                            self.assertEqual(LicenseConsumptionPointsDecision.objects.get(order=order).outcome, 'DENY')
                            self.assertEqual(verify_evidence_locked(evidence).pk, evidence.pk)
                            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                            transaction.set_rollback(True)
                        before_points = MemberProfile.objects.get(account=order.customer_account).points_balance
                        with patch('coupons.benefits.grant_periodic', side_effect=RuntimeError('vip grant failure')):
                            with self.assertRaisesMessage(RuntimeError, 'vip grant failure'):
                                process_order(order.pk)
                        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                        self.assertFalse(TransferRefundPointsConsumptionEvidence.objects.filter(manifest=manifest).exists())
                        self.assertEqual(MemberProfile.objects.get(account=order.customer_account).points_balance, before_points)
                        with patch('engagement.transfer_refunded_points_confirmation.TransferRefundPointsConsumptionEvidence.save',
                                   side_effect=RuntimeError('consumption evidence failure')):
                            with self.assertRaisesMessage(RuntimeError, 'consumption evidence failure'):
                                process_order(order.pk)
                        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                        self.assertEqual(MemberProfile.objects.get(account=order.customer_account).points_balance, before_points)
                        self.assertTrue(process_order(order.pk))
                        self.assertFalse(process_order(order.pk))
                        confirmation = ConsumptionConfirmation.objects.get(order=order)
                        self.assertEqual((confirmation.amount, confirmation.refunded_amount), (4000, 660))
                        member_evidence = TransferRefundPointsConsumptionEvidence.objects.get(manifest=manifest)
                        self.assertEqual((member_evidence.net_paid_units, member_evidence.net_principal_units,
                                          member_evidence.refunded_principal_units), (3340, 3340, 660))
                        expected_points = 3340 * order.funding_terms['points_rate'] * order.funding_terms['birthday_multiplier'] // 100
                        self.assertEqual(member_evidence.awarded_points, expected_points)
                        self.assertEqual(MemberProfile.objects.get(account=order.customer_account).points_balance,
                                         before_points + expected_points)
                        self.assertEqual(verify_evidence_locked(member_evidence).pk, member_evidence.pk)
                        with transaction.atomic():
                            member_evidence.points_rate += 1; member_evidence.save(update_fields=['points_rate'])
                            with self.assertRaises(ValidationError):
                                process_order(order.pk)
                            transaction.set_rollback(True)
                    self.assertEqual((settled.provider_income, settled.settlement_store_funding), (3440, 100))
                    self.assertTrue(settled.settled_at)
                    directory_transaction.set_rollback(True, using='default')
                    transaction.set_rollback(True)
                review = admin.post(f'/api/admin/orders/{order.pk}/segment-refund-preview/',
                    {'refund_amount': 3000}, format='json')
                self.assertEqual(review.data['code'], 0, review.data)
                term = review.data['data']['provider_terms'][0]
                self.assertEqual((term['original_income_units'], term['burden_units'], term['retained_units']), (2520, 3000, 0))
                created = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',
                    {'request_id': 'transfer-post-proposal', 'expected_version': 0, 'refund_units': 3000,
                     'provider_terms': [{'account_id': new.pk, 'retained_units': 0}],
                     'explanation': '双方确认退款'}, format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                proposal_id = created.data['data']['id']
                for actor in (boss, player):
                    accepted = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                        {'proposal_id': proposal_id, 'accepted': True}, format='json')
                    self.assertEqual(accepted.data['code'], 0, accepted.data)
                self.assertEqual(accepted.data['data']['status'], 'ACCEPTED')
                with override_settings(SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                                       NEGOTIATION_REFUND_EXECUTION_ENABLED=True):
                    denied = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/',
                        {'proposal_id': proposal_id, 'client_request_id': 'public-transfer-post'}, format='json')
                    self.assertGreaterEqual(denied.status_code, 400)
                    self.assertFalse(OrderRefund.objects.filter(order=order).exists())
                with transaction.atomic():
                    before = (Wallet.objects.get(user=order.customer).balance,
                              Wallet.objects.get(user=replacement).balance,
                              PointLedger.objects.filter(order=order).count(),
                              type(profile).objects.get(pk=profile.pk).deposit_paid)
                    with transaction.atomic():
                        type(profile).objects.filter(pk=profile.pk).update(deposit_paid=0)
                        with self.assertRaises(ValidationError):
                            post_locked(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                                        request_id='insufficient-transfer-post')
                        self.assertFalse(OrderRefund.objects.filter(order=order).exists())
                        self.assertEqual(Wallet.objects.get(user=order.customer).balance, before[0])
                        transaction.set_rollback(True)
                    with patch('orders.negotiation_models.PointsNegotiationPostingShare.objects.create',
                               side_effect=RuntimeError('injected posting failure')):
                        with self.assertRaisesMessage(RuntimeError, 'injected posting failure'):
                            post_locked(case_id, proposal_id, operator=staff, legacy_operator=staff_user, request_id='transfer-post')
                    self.assertEqual(before, (Wallet.objects.get(user=order.customer).balance,
                        Wallet.objects.get(user=replacement).balance, PointLedger.objects.filter(order=order).count(),
                        type(profile).objects.get(pk=profile.pk).deposit_paid))
                    self.assertFalse(OrderRefund.objects.filter(order=order).exists())
                    receipt = post_locked(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                                          request_id='transfer-post')
                    share = receipt.shares.get(account=new)
                    self.assertEqual((receipt.points_refund.coin_units, receipt.points_refund.points_hundredths,
                                      share.pending_deduction, share.wallet_deduction, share.deposit_deduction),
                                     (2000, 5000, 2520, 200, 280))
                    self.assertEqual(share.deposit_deficit.amount, 280)
                    self.assertEqual(type(profile).objects.get(pk=profile.pk).deposit_paid, 320)
                    self.assertEqual(Wallet.objects.get(user=replacement).balance, 0)
                    self.assertEqual(Wallet.objects.get(user=order.customer).balance, before[0] + 2000)
                    self.assertEqual(verify_posting_locked(receipt).pk, receipt.pk)
                    replay = post_locked(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                                         request_id='transfer-post')
                    self.assertEqual(replay.pk, receipt.pk)
                    self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)
                    transaction.set_rollback(True)
                from orders.points_negotiation_termination import execute_and_terminate, verify_termination_locked
                with override_settings(SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_REPORT_TESTING=True,
                        NEGOTIATION_REFUND_EXECUTION_ENABLED=True), transaction.atomic():
                    before = (Wallet.objects.get(user=order.customer).balance,
                              Wallet.objects.get(user=replacement).balance,
                              type(profile).objects.get(pk=profile.pk).deposit_paid)
                    with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('audit failure')):
                        with self.assertRaisesMessage(RuntimeError, 'audit failure'):
                            execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                                request_id='transfer-close', _transfer_testing=True)
                    self.assertEqual(before, (Wallet.objects.get(user=order.customer).balance,
                        Wallet.objects.get(user=replacement).balance, type(profile).objects.get(pk=profile.pk).deposit_paid))
                    self.assertFalse(OrderRefund.objects.filter(order=order).exists())
                    self.assertEqual(Order.objects.get(pk=order.pk).status, 'IN_SERVICE')
                    posting = execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                        request_id='transfer-close', _transfer_testing=True)
                    finished = Order.objects.get(pk=order.pk)
                    self.assertEqual((finished.status, finished.payment_status, finished.completed_at),
                                     ('COMPLETED', 'PARTIALLY_REFUNDED', posting.accepted_at))
                    self.assertEqual(OrderParticipationSegment.objects.get(pk=segment.pk).released_at, posting.accepted_at)
                    self.assertEqual(finished.aftersales.get().status, 'PARTIALLY_REFUNDED')
                    self.assertEqual(verify_termination_locked(posting).pk, posting.pk)
                    evidence = player.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                        {'segment_id': segment.pk, 'completion_image': screenshot()}, format='multipart')
                    self.assertEqual(evidence.data['code'], 0, evidence.data)
                    for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                        response = player.post(f'/api/orders/orders/{order.pk}/segment-report/',
                            {'segment_id': segment.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                            format='multipart' if action == 'upload' else 'json')
                        self.assertEqual(response.data['code'], 0, response.data)
                    reviewed = admin.post(f'/api/admin/orders/{order.pk}/segments/{segment.pk}/report-review/',
                        {'action': 'approve', 'version': 1}, format='json')
                    self.assertEqual(reviewed.data['code'], 0, reviewed.data)
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                    self.assertEqual(execute_and_terminate(case_id, proposal_id, operator=staff,
                        legacy_operator=staff_user, request_id='transfer-close', _transfer_testing=True).pk, posting.pk)
                    self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)
                    transaction.set_rollback(True)

    def test_returned_provider_preview_aggregates_segments_and_preserves_settled_other(self):
        from orders.services import settle_order
        with TemporaryDirectory(prefix='xa-points-transfer-refund-') as media, override_settings(MEDIA_ROOT=media):
            self.assertEqual(self.verify().status_code, 200)
            response = self.fund({'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 500})
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                old = OrderProvider.objects.get(order=order, released_at__isnull=True).provider_account
                old_client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(old)[0])
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
                response = old_client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                replacement = make_provider(deposit_mode='EARNINGS')
                new = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = new; replacement.escort_profile.save(update_fields=['account'])
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(new)[0])
                staff_user = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'order:settle',
                                                'support:edit', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                self.service.commission_override = True; self.service.commission_rate = 30
                self.service.save(update_fields=['commission_override', 'commission_rate'])
                def transfer(source, target, retained, transferred):
                    response = admin.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方同意',
                        'transfers': [{'old_provider_id': source.pk, 'new_provider_id': target.pk, 'split_type': 'PERCENT',
                            'split_value': 0, 'penalty_amount': 0, 'original_share': {'retained': retained,
                                'transferred': transferred, 'agreement': '双方同意'}}]}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                transfer(old, new, 4000, 6000)
                middle = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=new)
                response = player.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': middle.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                transfer(new, old, 2000, 4000)
                returned = OrderParticipationSegment.objects.get(attribution__order=order,
                    provider_account=old, released_at__isnull=True)
                pending_case = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                    'reason': '核对尚未开始的转回份额', 'affected_provider_account_ids': [old.pk],
                    'client_request_id': 'unstarted-return-case'}, format='json')
                self.assertEqual(pending_case.data['code'], 0, pending_case.data)
                pending_preview = admin.post(f'/api/admin/orders/{order.pk}/segment-refund-preview/',
                    {'refund_amount': 1000}, format='json')
                self.assertEqual(pending_preview.status_code, 409)
                self.assertIn('未开始履约', str(pending_preview.data))
                resolved = admin.post(f"/api/admin/aftersales/{pending_case.data['data']['id']}/resolve/",
                    {'resolution': '继续履约'}, format='json')
                self.assertEqual(resolved.data['code'], 0, resolved.data)
                response = old_client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': returned.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                self.assertEqual(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).count(), 2)
                case = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                    'reason': '请求协商退款', 'affected_provider_account_ids': [old.pk], 'client_request_id': 'segment-case-1'}, format='json')
                self.assertEqual(case.data['code'], 0, case.data)
                case_id = case.data['data']['id']
                url = f'/api/admin/orders/{order.pk}/segment-refund-preview/'
                support_user = make_console_user(['order:view', 'support:edit'])
                support = get_or_create_account_for_legacy_user(support_user)
                support_user.admin_membership.account = support; support_user.admin_membership.save(update_fields=['account'])
                support_client = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(support)[0])
                before = (Transaction.objects.filter(order=order).count(), PointLedger.objects.filter(order=order).count(),
                          list(Wallet.objects.order_by('pk').values_list('pk', 'balance')))
                result = admin.post(url, {'refund_amount': 1000}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                data = result.data['data']
                self.assertEqual((data['coin_units'], data['points_hundredths']), (660, 1666))
                self.assertEqual(data['provider_terms'], [{'account_id': old.pk, 'original_income_units': 3600,
                    'burden_units': 1000, 'retained_units': 2600, 'additional_units': 0}])
                self.assertEqual((data['conditional_funding_if_recovered']['required_units'],
                    data['conditional_funding_if_recovered']['release_units']), (100, 340))
                self.assertEqual([part['amount'] for part in data['conditional_release_parts']], [340])
                self.assertTrue(data['estimated_sufficient']); self.assertFalse(data['executable'])
                # The capture is preparation for a future atomic posting, not
                # permission to refund. Exercise the real asset-return primitive
                # only in a rolled-back transaction on isolated test databases.
                from orders.points_transfer_refund_evidence import (capture_locked, digest, reconstruct_funding_locked,
                    verify_locked, verify_transfer_revisions_after_refund_locked)
                from orders.points_service_refund import refund_assets_locked
                from orders.points_transfer_history import plan_historic_points_transfer
                from orders.points_transfer_revision import verify_revisions_locked
                from rest_framework.exceptions import ValidationError
                with transaction.atomic():
                    evidence = capture_locked(case_id, refund_equivalent_units=1000)
                    self.assertEqual(len(evidence['facts']['segments']), 3)
                    self.assertEqual(len(evidence['facts']['transfers']), 2)
                    self.assertEqual(sum(item['account_id'] == old.pk for item in evidence['facts']['segments']), 2)
                    versions = [0] + [row['id'] for row in evidence['facts']['transfers']]
                    before_funding = {version: plan_historic_points_transfer(order.pk,
                        through_transfer_id=version, allow_recorded=True) for version in versions}
                    before_released, _ = verify_revisions_locked(order)
                    refund = OrderRefund.objects.create(order=order, amount=660, reason='隔离资金凭证测试',
                        operator=staff_user, operator_account=staff, client_request_id='isolated-proof')
                    asset = refund_assets_locked(order_id=order.pk, refund_id=refund.pk, equivalent_units=1000)
                    self.assertEqual((asset.coin_units, asset.points_hundredths), (660, 1666))
                    self.assertEqual(verify_locked(order.pk, evidence), evidence['facts'])
                    for version in versions:
                        self.assertEqual(reconstruct_funding_locked(order.pk, evidence, refund_id=refund.pk,
                            through_transfer_id=version), before_funding[version])
                    self.assertEqual(verify_transfer_revisions_after_refund_locked(order.pk, evidence,
                        refund_id=refund.pk), before_released)
                    release = OrderPointsTransferFundingRelease.objects.filter(revision__order=order).first()
                    self.assertIsNotNone(release)
                    original_release = release.amount
                    release.amount += 1; release.save(update_fields=['amount'])
                    with self.assertRaises(ValidationError):
                        verify_transfer_revisions_after_refund_locked(order.pk, evidence, refund_id=refund.pk)
                    release.amount = original_release; release.save(update_fields=['amount'])
                    PointLedger.objects.filter(pk=asset.points_ledger_id).update(amount=asset.points_hundredths + 1)
                    with self.assertRaises(ValidationError):
                        reconstruct_funding_locked(order.pk, evidence, refund_id=refund.pk,
                                                   through_transfer_id=versions[-1])
                    PointLedger.objects.filter(pk=asset.points_ledger_id).update(amount=asset.points_hundredths)
                    forged = deepcopy(evidence)
                    forged['facts']['segments'][0]['original_share'] += 10
                    forged['digest'] = digest(forged['facts'])
                    with self.assertRaises(ValidationError):
                        verify_locked(order.pk, forged)
                    Order.objects.filter(pk=order.pk).update(original_amount=order.original_amount + 10)
                    with self.assertRaises(ValidationError):
                        verify_locked(order.pk, evidence)
                    Order.objects.filter(pk=order.pk).update(original_amount=order.original_amount)
                    # Terminating the current service may set its release
                    # time; the agreed end time is validated separately.
                    OrderParticipationSegment.objects.filter(pk=returned.pk).update(released_at=timezone.now())
                    self.assertEqual(verify_locked(order.pk, evidence), evidence['facts'])
                    snap = OrderSegmentFinancialSnapshot.objects.get(segment_id=returned.pk)
                    snap.commission_source = 'tampered'; snap.save(update_fields=['commission_source'])
                    with self.assertRaises(ValidationError):
                        verify_locked(order.pk, evidence)
                    transaction.set_rollback(True)
                shortfall = admin.post(url, {'refund_amount': 5000}, format='json')
                self.assertEqual(shortfall.data['code'], 0, shortfall.data)
                self.assertFalse(shortfall.data['data']['estimated_sufficient'])
                self.assertEqual(shortfall.data['data']['provider_recovery_preview'][0]['shortfall_units'], 1400)
                self.assertFalse(shortfall.data['data']['executable'])
                terms = support_client.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/?refund_units=1000')
                self.assertEqual(terms.data['code'], 0, terms.data)
                self.assertEqual(terms.data['data']['provider_terms'], data['provider_terms'])
                self.assertEqual((terms.data['data']['read_only'], terms.data['data']['agreement_available'],
                                  terms.data['data']['execution_available']), (True, False, False))
                forbidden = support_client.get(f'/api/admin/aftersales/{case_id}/negotiation-funding/?refund_units=1000')
                self.assertEqual(forbidden.status_code, 403)
                recovery = admin.get(f'/api/admin/aftersales/{case_id}/negotiation-funding/?refund_units=1000')
                self.assertEqual(recovery.data['code'], 0, recovery.data)
                self.assertEqual(recovery.data['data']['providers'][0]['pending_income_units'], 1000)
                self.assertFalse(recovery.data['data']['executable'])
                with override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                        SERVICE_POINTS_NEGOTIATION_TESTING=True, SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
                        SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True, SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                        SERVICE_POINTS_FUNDING_RELEASE_TESTING=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True):
                    status = admin.get(f'/api/admin/aftersales/{case_id}/negotiation-execution/')
                    self.assertEqual(status.data['code'], 0, status.data)
                    self.assertFalse(status.data['data']['execution_enabled'])
                    proposal = support_client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',
                        {'request_id': 'transfer-unsupported', 'expected_version': 0, 'refund_units': 1000,
                         'provider_terms': [{'account_id': old.pk, 'retained_units': 2600}],
                         'explanation': '请求协商退款'}, format='json')
                    self.assertGreaterEqual(proposal.status_code, 400)
                    self.assertFalse(NegotiationProposal.objects.filter(case_id=case_id).exists())
                with override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                        SERVICE_POINTS_NEGOTIATION_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_AGREEMENT_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_INTERNAL_AGREEMENT_TESTING=True,
                        SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING=True,
                        SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING=True,
                        NEGOTIATION_REFUND_EXECUTION_ENABLED=True), directory_transaction.atomic(using='default'), transaction.atomic():
                    proposal = support_client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',
                        {'request_id': 'transfer-consent-test', 'expected_version': 0, 'refund_units': 1000,
                         'provider_terms': [{'account_id': old.pk, 'retained_units': 2600}],
                         'explanation': '双方确认退款方案'}, format='json')
                    self.assertEqual(proposal.data['code'], 0, proposal.data)
                    proposal_id = proposal.data['data']['id']
                    user_url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
                    self.assertEqual(player.get(user_url).status_code, 404)
                    for actor in (boss, old_client):
                        agreed = actor.post(user_url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
                        self.assertEqual(agreed.data['code'], 0, agreed.data)
                    self.assertEqual(agreed.data['data']['status'], 'ACCEPTED')
                    self.assertFalse(agreed.data['data']['points_execution_available'])
                    self.assertFalse(OrderRefund.objects.filter(order=order).exists())
                    from orders.points_negotiation_termination import execute_and_terminate, verify_termination_locked
                    with transaction.atomic():
                        closed = execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                            request_id='returned-provider-close', _transfer_testing=True)
                        self.assertEqual(Order.objects.get(pk=order.pk).status, 'COMPLETED')
                        self.assertEqual(OrderParticipationSegment.objects.get(pk=returned.pk).released_at, closed.accepted_at)
                        self.assertEqual(verify_termination_locked(closed).pk, closed.pk)
                        transaction.set_rollback(True)
                    from orders.points_negotiation_execution import post_locked
                    from orders.points_funding_release import receipt_points_used_locked
                    with patch('orders.points_transfer_refund_release.OrderPointsFundingRelease.objects.bulk_create',
                               side_effect=RuntimeError('injected transfer refund release failure')):
                        with self.assertRaisesMessage(RuntimeError, 'injected transfer refund release failure'):
                            post_locked(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                                        request_id='funded-transfer-refund')
                    self.assertFalse(OrderRefund.objects.filter(order=order).exists())
                    self.assertFalse(OrderPointsFundingRelease.objects.filter(allocation__redemption__order=order).exists())
                    posting = post_locked(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                                          request_id='funded-transfer-refund')
                    self.assertEqual(list(posting.funding_releases.values_list('amount', flat=True)), [340])
                    from orders.points_transfer_refund_release import (
                        verify_release_locked as verify_transfer_release,
                        _verify_release_facts_locked as verify_release_facts)
                    with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING=True):
                        self.assertEqual(verify_release_facts(posting.funding_release_evidence),
                                         verify_transfer_release(posting.funding_release_evidence))
                        with transaction.atomic():
                            released = posting.funding_releases.get()
                            released.amount += 1; released.save(update_fields=['amount'])
                            with self.assertRaises(ValidationError):
                                verify_release_facts(posting.funding_release_evidence)
                            transaction.set_rollback(True)
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    self.assertEqual(receipt_points_used_locked(receipt), 100)
                    with override_settings(SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING=True,
                                           SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING=False):
                        self.assertEqual(receipt_points_used_locked(receipt), 100)
                    from orders.points_funding_release import _digest
                    with transaction.atomic():
                        evidence = posting.funding_release_evidence
                        evidence.allocations = []
                        evidence.evidence_hash = _digest(posting, [], evidence.original_provider_income,
                                                         None, evidence.expected_release)
                        evidence.save(update_fields=['allocations', 'evidence_hash'])
                        with self.assertRaises(ValidationError):
                            receipt_points_used_locked(receipt)
                        transaction.set_rollback(True)
                    with transaction.atomic():
                        release = posting.funding_releases.get()
                        release.amount += 1; release.save(update_fields=['amount'])
                        with self.assertRaises(ValidationError):
                            receipt_points_used_locked(receipt)
                        transaction.set_rollback(True)
                    with transaction.atomic():
                        release = OrderPointsTransferFundingRelease.objects.filter(revision__order=order).first()
                        type(release).objects.filter(pk=release.pk).update(
                            created_at=posting.points_refund.refund.created_at + timedelta(seconds=1))
                        with self.assertRaises(ValidationError):
                            receipt_points_used_locked(receipt)
                        transaction.set_rollback(True)
                    with transaction.atomic():
                        release = posting.funding_releases.get()
                        type(release).objects.filter(pk=release.pk).update(
                            created_at=posting.funding_release_evidence.created_at - timedelta(seconds=1))
                        with self.assertRaises(ValidationError):
                            receipt_points_used_locked(receipt)
                        transaction.set_rollback(True)
                    from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
                    first_refund, second_refund = PlatformPassRepaymentTests.setup_refunds(self)
                    allocated = PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
                        {'request_id': str(uuid4()), 'refund_id': first_refund, 'amount': 300})
                    self.assertEqual(allocated.status_code, 200, allocated.data)
                    too_much = PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
                        {'request_id': str(uuid4()), 'refund_id': second_refund, 'amount': 101})
                    self.assertNotEqual(too_much.status_code, 200, too_much.data)
                    allocated = PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
                        {'request_id': str(uuid4()), 'refund_id': second_refund, 'amount': 100})
                    self.assertEqual(allocated.status_code, 200, allocated.data)
                    self.assertEqual(sum(receipt.allocations.values_list('amount', flat=True)), 400)
                    replay = post_locked(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                                         request_id='funded-transfer-refund')
                    self.assertEqual(replay.pk, posting.pk)
                    directory_transaction.set_rollback(True, using='default')
                    transaction.set_rollback(True)
                self.assertEqual(before, (Transaction.objects.filter(order=order).count(), PointLedger.objects.filter(order=order).count(),
                                          list(Wallet.objects.order_by('pk').values_list('pk', 'balance'))))
                denied = player.post(url, {'refund_amount': 1000}, format='json')
                self.assertEqual(denied.status_code, 403)
                mismatch = admin.post(url, {'refund_amount': 1000, 'affected_provider_account_ids': [new.pk]}, format='json')
                self.assertEqual(mismatch.status_code, 409)
                with override_settings(SERVICE_POINTS_TRANSFER_REFUND_PREVIEW_TESTING=False):
                    denied = admin.post(url, {'refund_amount': 1000}, format='json')
                    self.assertEqual(denied.status_code, 409)
                with transaction.atomic():
                    provider_user_id = OrderProvider.objects.get(order=order, provider_account=old).provider_id
                    Wallet.objects.filter(account=support).update(account=None)
                    self.assertEqual(Wallet.objects.filter(user_id=provider_user_id).update(account=support), 1)
                    denied = admin.post(url, {'refund_amount': 1000}, format='json')
                    self.assertEqual(denied.status_code, 409)
                    self.assertIn('钱包归属', str(denied.data))
                    transaction.set_rollback(True)
                with transaction.atomic():
                    revision = OrderPointsTransferFundingRevision.objects.filter(order=order).order_by('pk').first()
                    revision.after_funding = {}; revision.save(update_fields=['after_funding'])
                    denied = admin.post(url, {'refund_amount': 1000}, format='json')
                    self.assertEqual(denied.status_code, 409)
                    transaction.set_rollback(True)
                resolved = admin.post(f'/api/admin/aftersales/{case_id}/resolve/', {'resolution': '继续履约'}, format='json')
                self.assertEqual(resolved.data['code'], 0, resolved.data)
                initial = OrderParticipationSegment.objects.filter(attribution__order=order,
                    provider_account=old, released_at__isnull=False).order_by('pk').first()
                for client, segment in ((old_client, initial), (player, middle)):
                    response = client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                        {'segment_id': segment.pk, 'completion_image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                response = old_client.post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                opened = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {'reason': '两位陪玩共同售后',
                    'client_request_id': 'segment-case-both'}, format='json')
                self.assertEqual(opened.data['code'], 0, opened.data)
                mismatch = admin.post(url, {'refund_amount': 510, 'affected_provider_account_ids': [old.pk]}, format='json')
                self.assertEqual(mismatch.status_code, 409)
                both = admin.post(url, {'refund_amount': 510}, format='json')
                self.assertEqual(both.data['code'], 0, both.data)
                terms = {item['account_id']: item for item in both.data['data']['provider_terms']}
                self.assertEqual((terms[old.pk]['burden_units'], terms[new.pk]['burden_units'],
                                  both.data['data']['rounding_to_store_units']), (410, 110, 10))
                self.assertEqual((both.data['data']['coin_units'], both.data['data']['points_hundredths']), (340, 850))
                self.assertEqual((both.data['data']['conditional_funding_if_recovered']['required_units'],
                                  both.data['data']['conditional_funding_if_recovered']['release_units']), (260, 180))
                with override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                        SERVICE_POINTS_NEGOTIATION_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_AGREEMENT_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_INTERNAL_AGREEMENT_TESTING=True,
                        SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING=True,
                        SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING=True,
                        NEGOTIATION_REFUND_EXECUTION_ENABLED=True), transaction.atomic():
                    proposal = support_client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',
                        {'request_id': 'transfer-whole-consent', 'expected_version': 0, 'refund_units': 510,
                         'provider_terms': [{'account_id': item['account_id'], 'retained_units': item['retained_units']}
                                            for item in both.data['data']['provider_terms']],
                         'explanation': '双方确认整单退款方案'}, format='json')
                    self.assertEqual(proposal.data['code'], 0, proposal.data)
                    proposal_id = proposal.data['data']['id']
                    for actor in (boss, old_client, player):
                        agreed = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                            {'proposal_id': proposal_id, 'accepted': True}, format='json')
                        self.assertEqual(agreed.data['code'], 0, agreed.data)
                    self.assertEqual(agreed.data['data']['status'], 'ACCEPTED')
                    self.assertFalse(OrderRefund.objects.filter(order=order).exists())
                    from orders.points_negotiation_termination import execute_and_terminate, verify_termination_locked
                    previous_completed_at = Order.objects.get(pk=order.pk).completed_at
                    posting = execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                        request_id='completed-transfer-refund', _transfer_testing=True)
                    self.assertFalse(posting.termination_in_service)
                    self.assertEqual(Order.objects.get(pk=order.pk).completed_at, previous_completed_at)
                    self.assertEqual(posting.points_refund.coin_units, 340)
                    self.assertEqual(sum(posting.funding_releases.values_list('amount', flat=True)), 180)
                    self.assertEqual(verify_termination_locked(posting).pk, posting.pk)
                    transaction.set_rollback(True)
                resolved = admin.post(f'/api/admin/aftersales/{case_id}/resolve/', {'resolution': '继续核对'}, format='json')
                self.assertEqual(resolved.data['code'], 0, resolved.data)
                opened = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {'reason': '对该陪玩申请售后',
                    'affected_provider_account_ids': [old.pk], 'client_request_id': 'segment-case-2'}, format='json')
                self.assertEqual(opened.data['code'], 0, opened.data)
                for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                    response = player.post(f'/api/orders/orders/{order.pk}/segment-report/',
                        {'segment_id': middle.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                        format='multipart' if action == 'upload' else 'json')
                    self.assertEqual(response.data['code'], 0, response.data)
                response = admin.post(f'/api/admin/orders/{order.pk}/segments/{middle.pk}/report-review/',
                    {'action': 'approve', 'version': 1}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                future = timezone.now() + timedelta(hours=100)
                with patch('django.utils.timezone.now', return_value=future), transaction.atomic():
                    paid = settle_order(Order.objects.select_for_update().get(pk=order.pk))
                self.assertEqual(paid, [replacement.pk])
                self.assertEqual(OrderSegmentPersonSettlement.objects.get(manifest__order=order).amount, 840)
                with patch('django.utils.timezone.now', return_value=future):
                    result = admin.post(url, {'refund_amount': 1000}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                self.assertEqual(result.data['data']['provider_terms'][0]['retained_units'], 2600)
                self.assertEqual(OrderSegmentPersonSettlement.objects.get(manifest__order=order).amount, 840)
                with patch('django.utils.timezone.now', return_value=future), transaction.atomic():
                    unrelated = OrderSegmentPersonSettlement.objects.get(manifest__order=order)
                    wallet_before = Wallet.objects.get(user=replacement).balance
                    after_other_paid = capture_locked(case_id, refund_equivalent_units=1000)
                    prior = after_other_paid['facts']['prior_settlement']
                    self.assertEqual(prior['manifest_id'], unrelated.manifest_id)
                    self.assertEqual([(row['account_id'], row['amount']) for row in prior['people']],
                                     [(new.pk, 840)])
                    refund = OrderRefund.objects.create(order=order, amount=660, reason='隔离已结算人员测试',
                        operator=staff_user, operator_account=staff, client_request_id='isolated-other-paid')
                    refund_assets_locked(order_id=order.pk, refund_id=refund.pk, equivalent_units=1000)
                    verify_transfer_revisions_after_refund_locked(order.pk, after_other_paid, refund_id=refund.pk)
                    self.assertEqual(OrderSegmentPersonSettlement.objects.get(pk=unrelated.pk).amount, 840)
                    self.assertEqual(Wallet.objects.get(user=replacement).balance, wallet_before)
                    forged_prior = deepcopy(after_other_paid)
                    forged_prior['facts']['prior_settlement']['people'][0]['amount'] += 1
                    forged_prior['digest'] = digest(forged_prior['facts'])
                    with self.assertRaises(ValidationError):
                        verify_locked(order.pk, forged_prior)
                    with transaction.atomic():
                        OrderSegmentPersonSettlement.objects.filter(pk=unrelated.pk).update(amount=841)
                        with self.assertRaises(ValidationError):
                            verify_locked(order.pk, after_other_paid)
                        transaction.set_rollback(True)
                    with transaction.atomic():
                        Transaction.objects.filter(pk=unrelated.income_transaction_id).update(balance_after=0)
                        with self.assertRaises(ValidationError):
                            verify_locked(order.pk, after_other_paid)
                        transaction.set_rollback(True)
                    with transaction.atomic():
                        type(unrelated.manifest).objects.filter(pk=unrelated.manifest_id).update(plan_hash='0' * 64)
                        with self.assertRaises(ValidationError):
                            verify_locked(order.pk, after_other_paid)
                        transaction.set_rollback(True)
                    with transaction.atomic():
                        from orders.segment_settlement import _hash
                        manifest = unrelated.manifest
                        altered_plan = deepcopy(manifest.plan)
                        altered_plan['people'][0]['income'] += 1
                        type(manifest).objects.filter(pk=manifest.pk).update(
                            plan=altered_plan, plan_hash=_hash(altered_plan))
                        with self.assertRaises(ValidationError):
                            verify_locked(order.pk, after_other_paid)
                        transaction.set_rollback(True)
                    transaction.set_rollback(True)
                # Exercise the real accepted refund with a different person
                # already paid, through the isolated-only public API.
                with override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                        SERVICE_POINTS_NEGOTIATION_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_AGREEMENT_TESTING=True,
                        SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_PRIOR_SETTLEMENT_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_ENDPOINT_TESTING=True,
                        SERVICE_POINTS_FUNDING_RELEASE_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_FUNDING_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_REPORT_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_SETTLEMENT_TESTING=True,
                        SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_CONSUMPTION_TESTING=True,
                        SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                        REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
                        SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                        SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING=True,
                        NEGOTIATION_REFUND_EXECUTION_ENABLED=True), patch('django.utils.timezone.now', return_value=future), \
                        directory_transaction.atomic(using='default'), transaction.atomic():
                    execution_url = f'/api/admin/aftersales/{case_id}/negotiation-execution/'
                    with override_settings(SERVICE_POINTS_TRANSFER_REFUND_PRIOR_SETTLEMENT_TESTING=False):
                        self.assertFalse(admin.get(execution_url).data['data']['execution_enabled'])
                    self.assertTrue(admin.get(execution_url).data['data']['execution_enabled'])
                    proposal = support_client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',
                        {'request_id': 'prior-paid-consent', 'expected_version': 0, 'refund_units': 1000,
                         'provider_terms': [{'account_id': old.pk, 'retained_units': 2600}],
                         'explanation': '双方确认退款方案'}, format='json')
                    self.assertEqual(proposal.data['code'], 0, proposal.data)
                    proposal_id = proposal.data['data']['id']
                    for actor in (boss, old_client):
                        agreed = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                            {'proposal_id': proposal_id, 'accepted': True}, format='json')
                        self.assertEqual(agreed.data['code'], 0, agreed.data)
                    from orders.points_negotiation_termination import verify_termination_locked
                    paid_before = OrderSegmentPersonSettlement.objects.get(manifest__order=order)
                    wallet_before = Wallet.objects.get(user=replacement).balance
                    execution_body = {'proposal_id': proposal_id, 'client_request_id': 'prior-paid-refund'}
                    denied = support_client.post(execution_url, execution_body, format='json')
                    self.assertEqual(denied.status_code, 403)
                    self.assertFalse(OrderRefund.objects.filter(order=order).exists())
                    response = admin.post(execution_url, execution_body, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                    posting = PointsNegotiationPosting.objects.get(proposal_id=proposal_id)
                    self.assertEqual(posting.participants['facts']['prior_settlement']['people'][0]['id'], paid_before.pk)
                    self.assertEqual((posting.points_refund.coin_units, posting.points_refund.points_hundredths), (660, 1666))
                    self.assertEqual(list(posting.funding_releases.values_list('amount', flat=True)), [340])
                    self.assertEqual(OrderSegmentPersonSettlement.objects.get(pk=paid_before.pk).amount, 840)
                    self.assertEqual(Wallet.objects.get(user=replacement).balance, wallet_before)
                    self.assertEqual(verify_termination_locked(posting).pk, posting.pk)
                    self.assertEqual(admin.post(execution_url, execution_body, format='json').data['code'], 0)
                    self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)
                    # The first case was opened within the window but resolved
                    # after another participant's income became payable. A new
                    # case cannot be created after the order's deadline, even
                    # though the first case was allowed to finish later.
                    from engagement.confirmation import aftersale_deadline
                    current_order = Order.objects.get(pk=order.pk)
                    cutoff = aftersale_deadline(current_order)
                    self.assertIsNotNone(cutoff)
                    self.assertGreaterEqual(paid_before.settled_at, cutoff)
                    self.assertGreaterEqual(timezone.now(), cutoff)
                    case_before = list(current_order.aftersales.values_list('pk', 'status', 'history'))
                    late = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {
                        'reason': '申请另一位陪玩的售后', 'affected_provider_account_ids': [new.pk],
                        'client_request_id': 'prior-paid-late-followup'}, format='json')
                    self.assertEqual(late.status_code, 400, late.data)
                    self.assertIn('订单已结束售后期或不符合售后条件', str(late.data))
                    staff_late = admin.post('/api/admin/aftersales/open-negotiation/', {
                        'order_id': order.pk, 'reason': '客服代为登记另一位陪玩的售后',
                        'affected_provider_account_ids': [new.pk],
                        'client_request_id': 'staff-prior-paid-late-followup'}, format='json')
                    self.assertEqual(staff_late.status_code, 400, staff_late.data)
                    self.assertIn('仅能为服务中且已付款的服务订单登记协商售后', str(staff_late.data))
                    self.assertEqual(list(current_order.aftersales.values_list('pk', 'status', 'history')), case_before)
                    self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)
                    from orders.models import OrderSegmentSettlementRevision
                    from orders.points_funding_evidence import verify_order_funding
                    from engagement.models import ConsumptionConfirmation
                    from engagement.confirmation import process_order
                    funding = verify_order_funding(order.pk)
                    self.assertEqual((funding.required, funding.allocated, funding.remaining), (100, 100, 0))
                    for part in (initial, returned):
                        for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                            report = old_client.post(f'/api/orders/orders/{order.pk}/segment-report/',
                                {'segment_id': part.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                                format='multipart' if action == 'upload' else 'json')
                            self.assertEqual(report.data['code'], 0, report.data)
                        reviewed = admin.post(f'/api/admin/orders/{order.pk}/segments/{part.pk}/report-review/',
                            {'action': 'approve', 'version': 1}, format='json')
                        self.assertEqual(reviewed.data['code'], 0, reviewed.data)
                    with patch('orders.points_transfer_settlement_revision.OrderSegmentSettlementRevision.objects.create',
                               side_effect=RuntimeError('revision write failed')):
                        with self.assertRaisesMessage(RuntimeError, 'revision write failed'), transaction.atomic():
                            process_order(order.pk)
                    self.assertFalse(OrderSegmentSettlementRevision.objects.filter(manifest__order=order).exists())
                    self.assertEqual(Wallet.objects.get(user=replacement).balance, wallet_before)
                    with patch('orders.models.OrderSegmentSettlementManifest.save',
                               side_effect=RuntimeError('manifest write failed')):
                        with self.assertRaisesMessage(RuntimeError, 'manifest write failed'):
                            process_order(order.pk)
                    self.assertFalse(OrderSegmentSettlementRevision.objects.filter(manifest__order=order).exists())
                    paid_before.manifest.refresh_from_db()
                    self.assertEqual(paid_before.manifest.plan,
                                     posting.participants['facts']['prior_settlement']['plan'])
                    self.assertFalse(process_order(order.pk))
                    self.assertTrue(OrderSegmentSettlementRevision.objects.filter(manifest__order=order).exists())
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                    with patch('django.utils.timezone.now', return_value=future + timedelta(hours=73)):
                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)),
                                         [order.providers.get(provider_account=old).provider_id])
                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                        revision = OrderSegmentSettlementRevision.objects.get(manifest__order=order)
                        self.assertEqual(revision.posting_id, posting.pk)
                        self.assertEqual(OrderSegmentPersonSettlement.objects.get(pk=paid_before.pk).amount, 840)
                        self.assertEqual(Wallet.objects.get(user=replacement).balance, wallet_before)
                        self.assertTrue(Order.objects.get(pk=order.pk).settled_at)
                        self.assertTrue(process_order(order.pk))
                        self.assertFalse(process_order(order.pk))
                        confirmation = ConsumptionConfirmation.objects.get(order=order)
                        self.assertEqual((confirmation.amount, confirmation.refunded_amount), (4000, 660))
                        missing_prior = deepcopy(posting.participants)
                        del missing_prior['facts']['prior_settlement']
                        missing_prior['digest'] = digest(missing_prior['facts'])
                        with self.assertRaises(ValidationError):
                            verify_locked(order.pk, missing_prior)
                        with transaction.atomic():
                            revision.after_plan_hash = '0' * 64
                            revision.save(update_fields=['after_plan_hash'])
                            from orders.settlement import SettlementError
                            from orders.segment_settlement import verify_finalized
                            with self.assertRaises(SettlementError):
                                verify_finalized(Order.objects.get(pk=order.pk), revision.manifest)
                            transaction.set_rollback(True)
                        with transaction.atomic():
                            from orders.segment_settlement import _hash
                            altered = deepcopy(revision.after_plan)
                            next(item for item in altered['people'] if item['account_id'] == new.pk)['income'] += 1
                            type(revision).objects.filter(pk=revision.pk).update(after_plan=altered, after_plan_hash=_hash(altered))
                            type(revision.manifest).objects.filter(pk=revision.manifest_id).update(
                                plan=altered, plan_hash=_hash(altered))
                            with self.assertRaises(SettlementError):
                                verify_finalized(Order.objects.get(pk=order.pk),
                                                 type(revision.manifest).objects.get(pk=revision.manifest_id))
                            transaction.set_rollback(True)
                    directory_transaction.set_rollback(True, using='default')
                    transaction.set_rollback(True)
            with store_context(self.first):
                self.assertFalse(OrderOriginalShareTransfer.objects.exists())
                with transaction.atomic(), self.assertRaises(ValidationError):
                    verify_locked(order.pk, evidence)
                outsider_user = make_console_user(['order:refund'])
                outsider = get_or_create_account_for_legacy_user(outsider_user)
                outsider_user.admin_membership.account = outsider; outsider_user.admin_membership.save(update_fields=['account'])
                foreign = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(outsider)[0])
                response = foreign.post(url, {'refund_amount': 1000}, format='json')
                self.assertNotEqual(response.data.get('code', 400), 0, response.data)
