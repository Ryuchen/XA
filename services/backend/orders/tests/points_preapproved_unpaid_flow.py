"""End-to-end evidence when both reports were approved before any refund."""
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import ConsumptionConfirmation, RefundedPointsConsumptionEvidence
from orders.models import AfterSale, Order, OrderPointsRefundSettlement, PointsNegotiationPosting
from orders.tests.factories import make_console_user
from orders.tests.test_release_flow import screenshot
from stores.db import transaction
from wallet.models import ProviderReport


def complete_and_preapprove(test, *, order, accounts, players):
    for player in players:
        result = player.post(f'/api/orders/orders/{order.pk}/complete/',
                             {'completion_image': screenshot()}, format='multipart')
        test.assertEqual(result.data['code'], 0, result.data)
    order.refresh_from_db()
    test.assertEqual(order.status, 'COMPLETED')
    reports = [ProviderReport.objects.get(order=order, provider_account=account) for account in accounts]
    for player, report in zip(players, reports):
        result = player.post(f'/api/wallet/reports/{report.pk}/images/',
                             {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
        test.assertEqual(result.data['code'], 0, result.data)
    reviewer_user = make_console_user(['report:view', 'report:audit'])
    reviewer_account = get_or_create_account_for_legacy_user(reviewer_user)
    reviewer_user.admin_membership.account = reviewer_account
    reviewer_user.admin_membership.save(update_fields=['account'])
    reviewer = APIClient(HTTP_HOST='admin.esports.club',
        HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer_account)[0])
    test._exercise_preapproved_unpaid[0] += timedelta(seconds=1)
    for player, report in zip(players, reports):
        result = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
        test.assertEqual(result.data['code'], 0, result.data)
        result = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
        test.assertEqual(result.data['code'], 0, result.data)
        report.refresh_from_db()
    test.assertEqual([report.payout_amount for report in reports], [6000, 5250])
    test._exercise_preapproved_unpaid[0] += timedelta(seconds=1)
    return reports


def finish_preapproved_unpaid(test, *, order, case_id, buyer, account, accounts, players, boss,
                              client, execution_url, reports):
    order.refresh_from_db()
    test.assertEqual((order.status, order.payment_status), ('COMPLETED', 'PARTIALLY_REFUNDED'))
    test.assertFalse(order.providers.filter(settled_at__isnull=False).exists())
    # A's scope is closed. A low-star review of B opens B's independent case.
    low = boss.post('/api/orders/evaluate/', {'order_id': order.pk, 'provider_account_id': accounts[1].pk,
        'score': 1, 'skill_score': 5, 'attitude_score': 5, 'communication_score': 5}, format='json')
    test.assertEqual(low.data['code'], 0, low.data)
    case = AfterSale.objects.get(pk=case_id)
    test.assertEqual((case.status, case.affected_provider_account_ids), ('OPEN', [accounts[1].pk]))
    switches = dict(SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_AGREEMENT_TESTING=True,
        SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True, SERVICE_POINTS_FOLLOWUP_POSTING_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_POSTING_TESTING=True, SERVICE_POINTS_FOLLOWUP_REVISION_TESTING=True,
        SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING=True, SERVICE_POINTS_FOLLOWUP_TERMINATION_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_TERMINATION_TESTING=True, SERVICE_POINTS_FOLLOWUP_FUNDING_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_FUNDING_TESTING=True, SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_SETTLEMENT_TESTING=True, SERVICE_POINTS_FOLLOWUP_ENDPOINT_TESTING=True,
        SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True,
        SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
        REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    with override_settings(**switches):
        proposal = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
            'request_id': 'preapproved-unpaid-proposal', 'expected_version': 1, 'refund_units': 1000,
            'provider_terms': [{'account_id': accounts[1].pk, 'retained_units': 4250}],
            'explanation': '审核后由双方确认退款'}, format='json')
        test.assertEqual(proposal.data['code'], 0, proposal.data)
        proposal_id = proposal.data['data']['id']
        for actor in (boss, players[1]):
            accepted = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                  {'proposal_id': proposal_id, 'accepted': True}, format='json')
            test.assertEqual(accepted.data['code'], 0, accepted.data)
        result = client.post(execution_url, {'proposal_id': proposal_id,
            'client_request_id': 'preapproved-unpaid-refund'}, format='json')
        test.assertEqual(result.data['code'], 0, result.data)
        posting = PointsNegotiationPosting.objects.get(proposal_id=proposal_id)
        test.assertIsNone(posting.followup_funding_revision.paid_person_id)
        for report, original in zip(reports, (6000, 5250)):
            report.refresh_from_db()
            test.assertEqual(report.payout_amount, original)
        from orders.points_followup_funding import verify_followup_funding
        from orders.services import settle_order
        with patch('django.utils.timezone.now', return_value=reports[0].submitted_at + timedelta(hours=73)):
            with transaction.atomic():
                test.assertTrue(verify_followup_funding(order.pk).fully_funded)
                test.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)),
                                 [report.provider_id for report in reports])
                settlement = OrderPointsRefundSettlement.objects.get(posting=posting)
                test.assertEqual([settlement.persons.get(report=report).amount for report in reports], [5500, 4250])
                for report, original in zip(reports, (6000, 5250)):
                    report.refresh_from_db()
                    test.assertEqual(report.payout_amount, original)
                from engagement.confirmation import process_order
                test.assertTrue(process_order(order.pk))
                benefit = RefundedPointsConsumptionEvidence.objects.get(settlement=settlement)
                test.assertEqual((benefit.paid_units, benefit.refunded_coin_units, benefit.net_paid_units),
                                 (11000, 1090, 9910))
                test.assertEqual(ConsumptionConfirmation.objects.get(order=order).refunded_amount, 1090)
                test.assertFalse(process_order(order.pk))
