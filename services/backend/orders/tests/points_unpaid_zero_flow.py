"""Real-API edge flow for two unpaid providers after a points refund."""
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import PointLedger
from orders.models import Order, OrderPointsRefundSettlement, OrderProvider, PointsNegotiationPosting
from orders.tests.factories import make_console_user, make_service
from orders.tests.test_release_flow import screenshot
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import ProviderReport, RefundDepositDeficit, Transaction, SystemConfig
from wallet.services import get_wallet


def exercise_unpaid_zero_deposit(test, *, order, case_id, buyer, account, accounts, players,
                                 boss, client, admin, staff, execution_url):
    switches = dict(SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_AGREEMENT_TESTING=True,
        SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True, SERVICE_POINTS_FOLLOWUP_POSTING_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_POSTING_TESTING=True, SERVICE_POINTS_FOLLOWUP_REVISION_TESTING=True,
        SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING=True, SERVICE_POINTS_FOLLOWUP_TERMINATION_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_TERMINATION_TESTING=True, SERVICE_POINTS_FOLLOWUP_FUNDING_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_FUNDING_TESTING=True, SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_SETTLEMENT_TESTING=True, SERVICE_POINTS_FOLLOWUP_ENDPOINT_TESTING=True,
        SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True,
        SERVICE_ORDINARY_FUNDING_TESTING=True,
        SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
        REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    with override_settings(**switches):
        reports = [ProviderReport.objects.get(order=order, provider_account=target) for target in accounts]
        for player, report, kinds in ((players[0], reports[0], ('COMPLETION', 'RESULT')),
                                      (players[1], reports[1], ('RESULT',))):
            for kind in kinds:
                response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                       {'kind': kind, 'image': screenshot()}, format='multipart')
                test.assertEqual(response.data['code'], 0, response.data)
        reviewer_user = make_console_user(['report:view', 'report:audit'])
        reviewer_account = get_or_create_account_for_legacy_user(reviewer_user)
        reviewer_user.admin_membership.account = reviewer_account
        reviewer_user.admin_membership.save(update_fields=['account'])
        reviewer = APIClient(HTTP_HOST='admin.esports.club',
            HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer_account)[0])
        baseline = timezone.now()
        for index in (0, 1):
            with patch('django.utils.timezone.now', return_value=baseline + timedelta(seconds=index + 1)):
                response = players[index].post(f'/api/wallet/reports/{reports[index].pk}/submit/', {}, format='json')
                test.assertEqual(response.data['code'], 0, response.data)
                response = reviewer.post(f'/api/admin/reports/{reports[index].pk}/approve/', {}, format='json')
                test.assertEqual(response.data['code'], 0, response.data)
        for report in reports:
            report.refresh_from_db()
        test.assertEqual([report.payout_amount for report in reports], [5500, 5250])
        assignment = OrderProvider.objects.get(order=order, provider_account=accounts[1])
        wallet = get_wallet(account_id=accounts[1].pk, user_id=assignment.provider_id)
        wallet.balance = 1200
        wallet.save(update_fields=['balance'])  # isolated starting-balance fixture
        profile = EscortProfile.objects.get(account=accounts[1])
        profile.deposit_mode = 'CASH'
        profile.deposit_paid = 0
        profile.deposit_required = 8050
        profile.save(update_fields=['deposit_mode', 'deposit_paid', 'deposit_required'])
        SystemConfig.objects.update_or_create(key='provider_deposit_targets', defaults={
            'value': '{"cash_target":8050,"earnings_target":16100,"version":1}'})
        proposal_time = baseline + timedelta(seconds=3)
        with patch('django.utils.timezone.now', side_effect=lambda: proposal_time):
            topup_url = f'/api/admin/escorts/{profile.pk}/deposit-topups/'
            topup = client.post(topup_url, {'amount': 8000, 'reason': '未结算双陪押金测试',
                'reference': 'UNPAID-ZERO-1', 'client_request_id': 'unpaid-zero-topup-1'}, format='multipart')
            test.assertEqual(topup.data['code'], 0, topup.data)
            proposed = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id': 'unpaid-zero-proposal', 'expected_version': 1, 'refund_units': 14500,
                'provider_terms': [{'account_id': accounts[1].pk, 'retained_units': 0}],
                'explanation': '未结算时退清并由受影响陪玩承担'}, format='json')
            test.assertEqual(proposed.data['code'], 0, proposed.data)
            proposal = proposed.data['data']['id']
            for actor in (boss, players[1]):
                agreed = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                    {'proposal_id': proposal, 'accepted': True}, format='json')
                test.assertEqual(agreed.data['code'], 0, agreed.data)
            proposal_time += timedelta(seconds=1)
            request = {'proposal_id': proposal, 'client_request_id': 'unpaid-zero-refund'}
            buyer_wallet = get_wallet(account_id=account.pk, user_id=buyer.pk)
            # Reload after the real top-up, before checking rollback.
            profile.refresh_from_db()
            before = (buyer_wallet.balance, wallet.balance, profile.deposit_paid,
                      Transaction.objects.count(), PointLedger.objects.count(), order.refund_records.count())
            insufficient = client.post(execution_url, request, format='json')
            test.assertNotEqual(insufficient.data.get('code', 400), 0, insufficient.data)
            buyer_wallet.refresh_from_db(); wallet.refresh_from_db(); profile.refresh_from_db()
            test.assertEqual((buyer_wallet.balance, wallet.balance, profile.deposit_paid,
                              Transaction.objects.count(), PointLedger.objects.count(), order.refund_records.count()), before)
            topup = client.post(topup_url, {'amount': 50, 'reason': '未结算双陪押金补差',
                'reference': 'UNPAID-ZERO-2', 'client_request_id': 'unpaid-zero-topup-2'}, format='multipart')
            test.assertEqual(topup.data['code'], 0, topup.data)
            paid = client.post(execution_url, request, format='json')
            test.assertEqual(paid.data['code'], 0, paid.data)
            posting = PointsNegotiationPosting.objects.get(proposal_id=proposal)
            share = posting.shares.get()
            test.assertEqual((share.pending_deduction, share.wallet_deduction, share.deposit_deduction,
                              share.retained_income), (5250, 1200, 8050, 0))
            test.assertEqual((posting.points_refund.coin_units, posting.points_refund.points_hundredths,
                              posting.points_refund.is_final), (10640, 9667, True))
            wallet.refresh_from_db(); profile.refresh_from_db()
            test.assertEqual((wallet.balance, profile.deposit_paid), (0, 0))
            deficit = RefundDepositDeficit.objects.get(refund=posting.points_refund.refund, profile=profile)
            test.assertEqual((deficit.amount, deficit.remaining), (8050, 8050))
            test.assertFalse(OrderProvider.objects.filter(order=order, settled_at__isnull=False).exists())
            test.assertEqual(client.post(execution_url, request, format='json').data['data']['execution_id'], posting.pk)
        from orders.points_followup_funding import verify_followup_funding
        from orders.services import settle_order
        with patch('django.utils.timezone.now', return_value=baseline + timedelta(hours=73)):
            with transaction.atomic():
                funding = verify_followup_funding(order.pk)
                test.assertTrue(funding.fully_funded)
                test.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)),
                                 [reports[0].provider_id, reports[1].provider_id])
                order.refresh_from_db()
                test.assertIsNotNone(order.settled_at)
                settlement = OrderPointsRefundSettlement.objects.get(posting=posting)
                zero = settlement.persons.get(report=reports[1])
                test.assertEqual(zero.amount, 0)
                test.assertIsNone(zero.income_transaction_id)
                test.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME',
                    wallet__account=accounts[1], remark=f'订单收入：{order.order_no}').exists())
                deficit.refresh_from_db()
                test.assertEqual(deficit.remaining, 8050)
                test.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                from engagement.confirmation import process_order
                from engagement.models import RefundedPointsConsumptionEvidence
                test.assertFalse(process_order(order.pk))
                benefit = RefundedPointsConsumptionEvidence.objects.get(settlement=settlement)
                test.assertEqual((benefit.net_paid_units, benefit.net_principal_units, benefit.awarded_points), (0, 0, 0))
                test.assertFalse(process_order(order.pk))
                # A real next service order is allowed solely to recover the
                # refund-created deposit gap. A second order must wait.
                service = make_service(name='押金补差服务', price=10000)
                options = boss.post('/api/orders/discount-options-preview/', {'service_id': service.pk}, format='json')
                test.assertEqual(options.data['code'], 0, options.data)
                payload = {'service_id': service.pk, 'provider_id': accounts[1].pk,
                           'discount_selection_token': options.data['data']['options'][0]['selection_token'],
                           'client_request_id': 'unpaid-zero-recovery-order'}
                created = boss.post('/api/orders/create/', payload, format='json')
                test.assertEqual(created.data['code'], 0, created.data)
                recovery = Order.objects.get(pk=created.data['data']['id'])
                from wallet.models import RefundDepositRecoveryPermit
                permit = RefundDepositRecoveryPermit.objects.get(order=recovery, account=accounts[1])
                test.assertIsNone(permit.closed_at)
                options = boss.post('/api/orders/discount-options-preview/', {'service_id': service.pk}, format='json')
                test.assertEqual(options.data['code'], 0, options.data)
                again = boss.post('/api/orders/create/', {**payload,
                    'discount_selection_token': options.data['data']['options'][0]['selection_token'],
                    'client_request_id': 'unpaid-zero-recovery-blocked'}, format='json')
                test.assertNotEqual(again.data.get('code', 400), 0, again.data)
                for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                    response = players[1].post(f'/api/orders/orders/{recovery.pk}/{action}/',
                                               {key: screenshot()}, format='multipart')
                    test.assertEqual(response.data['code'], 0, response.data)
                recovery_report = ProviderReport.objects.get(order=recovery, provider_account=accounts[1])
                response = players[1].post(f'/api/wallet/reports/{recovery_report.pk}/images/',
                    {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                test.assertEqual(response.data['code'], 0, response.data)
                response = players[1].post(f'/api/wallet/reports/{recovery_report.pk}/submit/', {}, format='json')
                test.assertEqual(response.data['code'], 0, response.data)
                response = reviewer.post(f'/api/admin/reports/{recovery_report.pk}/approve/', {}, format='json')
                test.assertEqual(response.data['code'], 0, response.data)
                recovery_report.refresh_from_db()
                test.assertFalse(process_order(recovery.pk))
                deficit.refresh_from_db(); permit.refresh_from_db()
                test.assertEqual(deficit.remaining, 8050)
                test.assertIsNone(permit.closed_at)
                with patch('django.utils.timezone.now', return_value=recovery_report.submitted_at + timedelta(hours=73)):
                    test.assertTrue(process_order(recovery.pk))
                    income = Transaction.objects.get(order=recovery, wallet__account=accounts[1], tx_type='INCOME',
                                                     remark=f'订单收入：{recovery.order_no}')
                    test.assertGreater(income.amount, 0)
                    test.assertLess(income.amount, 8050)
                    from wallet.models import RefundDepositIncomeApplication
                    application = RefundDepositIncomeApplication.objects.get(source_transaction=income)
                    test.assertEqual(application.applied_amount, income.amount)
                    deficit.refresh_from_db(); profile.refresh_from_db(); wallet.refresh_from_db(); permit.refresh_from_db()
                    test.assertEqual((deficit.remaining, profile.deposit_paid, wallet.balance),
                                     (8050 - income.amount, income.amount, 0))
                    test.assertEqual(permit.close_reason, 'INCOME_SETTLED')
                    test.assertIsNotNone(permit.closed_at)
                    remaining = deficit.remaining
                    options = boss.post('/api/orders/discount-options-preview/', {'service_id': service.pk}, format='json')
                    test.assertEqual(options.data['code'], 0, options.data)
                    second_created = boss.post('/api/orders/create/', {**payload,
                        'discount_selection_token': options.data['data']['options'][0]['selection_token'],
                        'client_request_id': 'unpaid-zero-recovery-second'}, format='json')
                    test.assertEqual(second_created.data['code'], 0, second_created.data)
                    second_order = Order.objects.get(pk=second_created.data['data']['id'])
                    second_permit = RefundDepositRecoveryPermit.objects.get(order=second_order, account=accounts[1])
                    test.assertIsNone(second_permit.closed_at)
                    for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                        response = players[1].post(f'/api/orders/orders/{second_order.pk}/{action}/',
                                                   {key: screenshot()}, format='multipart')
                        test.assertEqual(response.data['code'], 0, response.data)
                    second_report = ProviderReport.objects.get(order=second_order, provider_account=accounts[1])
                    response = players[1].post(f'/api/wallet/reports/{second_report.pk}/images/',
                        {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                    test.assertEqual(response.data['code'], 0, response.data)
                    response = players[1].post(f'/api/wallet/reports/{second_report.pk}/submit/', {}, format='json')
                    test.assertEqual(response.data['code'], 0, response.data)
                    response = reviewer.post(f'/api/admin/reports/{second_report.pk}/approve/', {}, format='json')
                    test.assertEqual(response.data['code'], 0, response.data)
                    second_report.refresh_from_db()
                    test.assertFalse(process_order(second_order.pk))
                    second_permit.refresh_from_db()
                    test.assertIsNone(second_permit.closed_at)
                    with patch('django.utils.timezone.now', return_value=second_report.submitted_at + timedelta(hours=73)):
                        test.assertTrue(process_order(second_order.pk))
                        second_income = Transaction.objects.get(order=second_order, wallet__account=accounts[1],
                            tx_type='INCOME', remark=f'订单收入：{second_order.order_no}')
                        test.assertGreater(second_income.amount, remaining)
                        second_application = RefundDepositIncomeApplication.objects.get(source_transaction=second_income)
                        test.assertEqual(second_application.applied_amount, remaining)
                        deficit.refresh_from_db(); profile.refresh_from_db(); wallet.refresh_from_db(); second_permit.refresh_from_db()
                        test.assertEqual((deficit.remaining, profile.deposit_paid, wallet.balance),
                                         (0, 8050, second_income.amount - remaining))
                        test.assertEqual(second_permit.close_reason, 'GAP_PAID')
                        test.assertIsNotNone(second_permit.closed_at)
                        test.assertEqual(profile.order_acceptance_error(service, allow_refund_recovery=True), '')
