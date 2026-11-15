"""Two-player API scenario: B's add-on refund, then A's service refund."""
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation
from orders.models import (GameCategory, OrderAddonConsumption, OrderAddonSettlement, OrderPointsRefundSettlement,
                           ServiceAddonConfiguration, ServiceAddonPriceOption)
from orders.tests.factories import make_console_user, make_service
from orders.tests.test_release_flow import screenshot
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from rest_framework.test import APIClient
from wallet.models import ProviderReport, Transaction


def exercise(test, *, order, service, buyer, account, boss, accounts, players, admin, small=False, preapproved=False,
             main_first=False, preallocate=False, partially_settled=False, gift=False, split=False,
             two_addons=False, late_purchase=False, keep_addons=False, late_gift=False, only_late=False):
    from uuid import uuid4
    price = 300 if small else 10000
    addon_income, addon_main_deduction = price * 70 // 100, price * 30 // 100 * (2 if two_addons else 1)
    b_income = 5250 if keep_addons else 5250 - addon_main_deduction
    game = GameCategory.objects.create(name='双陪加购与主单退款')
    service.game_category = game
    service.save(update_fields=['game_category'])
    addon = make_service(name='双陪指定加购', game_category=game, is_addon=True)
    config = ServiceAddonConfiguration.objects.create(service=service, addon=addon, enabled=True,
        condition_mode='NONE', conditions='', version=1)
    ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=price)
    if gift:
        from engagement.models import PointFundingLot
        from wallet.services import get_wallet
        wallet = get_wallet(account=account, user=buyer)
        wallet.balance += price
        wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=account, kind='GIFT', remaining=price, source_key='double-addon-gift')
    path = f'/api/orders/orders/{order.pk}/addons/intents/'
    payment_ids = []
    payment_id = None
    if not only_late:
        intent = boss.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
            'configuration_version': 1, 'provider_account_id': accounts[1].pk, 'request_id': 'double-addon-b',
            'expected_amount': price, 'gift_balance_amount': price if gift else 0, 'compensation_balance_amount': 0}, format='json')
        test.assertEqual(intent.status_code, 200, intent.data)
        paid = boss.post(path + f"{intent.data['data']['id']}/pay/", {}, format='json')
        test.assertEqual(paid.status_code, 200, paid.data)
        payment_id = paid.data['data']['payment_id']
        payment_ids.append(payment_id)
    def buy_second():
        if late_gift:
            from engagement.models import PointFundingLot
            from wallet.services import get_wallet
            wallet = get_wallet(account=account, user=buyer)
            wallet.balance += price
            wallet.save(update_fields=['balance'])
            PointFundingLot.objects.create(account=account, kind='GIFT', remaining=price,
                                           source_key='late-double-addon-gift')
        second_intent = boss.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
            'configuration_version': 1, 'provider_account_id': accounts[1].pk, 'request_id': 'double-addon-b-second',
            'expected_amount': price, 'gift_balance_amount': price if late_gift else 0,
            'compensation_balance_amount': 0}, format='json')
        test.assertEqual(second_intent.status_code, 200, second_intent.data)
        pay_url = path + f"{second_intent.data['data']['id']}/pay/"
        if late_purchase:
            from orders.models import OrderAddonFundingReleaseRun, OrderAddonPayment
            from wallet.services import get_wallet
            buyer_wallet = get_wallet(account=account, user=buyer)
            balance = buyer_wallet.balance
            count = OrderAddonPayment.objects.filter(intent__order=order).count()
            with patch('orders.addon_funding_release.OrderAddonFundingReleaseRun.objects.create',
                       side_effect=RuntimeError('payment funding failed')):
                with test.assertRaisesMessage(RuntimeError, 'payment funding failed'):
                    boss.post(pay_url, {}, format='json')
            buyer_wallet.refresh_from_db()
            test.assertEqual(buyer_wallet.balance, balance)
            test.assertEqual(OrderAddonPayment.objects.filter(intent__order=order).count(), count)
        second_payment = boss.post(pay_url, {}, format='json')
        test.assertEqual(second_payment.status_code, 200, second_payment.data)
        payment_ids.append(second_payment.data['data']['payment_id'])
        if late_purchase:
            from rest_framework.exceptions import ValidationError
            from stores.db import transaction
            from orders.addon_funding_release import verify_run
            from wallet.services import get_wallet
            run = OrderAddonFundingReleaseRun.objects.get(payment_id=payment_ids[-1])
            test.assertEqual((run.allocated_before, run.required_amount, run.released_amount),
                             (110, 20, 90) if only_late else (20, 230, 0) if late_gift else (20, 0, 20))
            wallet = get_wallet(account=account, user=buyer)
            balance = wallet.balance
            replay = boss.post(pay_url, {}, format='json')
            test.assertEqual(replay.status_code, 200, replay.data)
            test.assertEqual(replay.data['data']['payment_id'], payment_ids[-1])
            wallet.refresh_from_db()
            test.assertEqual(wallet.balance, balance)
            with transaction.atomic():
                OrderAddonFundingReleaseRun.objects.filter(pk=run.pk).update(refunded_main_hash='0' * 64)
                run.refresh_from_db()
                with test.assertRaises(ValidationError):
                    verify_run(run)
                transaction.set_rollback(True)
    if two_addons and not late_purchase:
        buy_second()
    if preallocate:
        test.assertEqual(test.verify().status_code, 200)
        allocation = test.fund({'request_id': str(uuid4()), 'order_id': order.pk,
                                'amount': 250 if only_late else 460 if gift else 70 if two_addons and not late_purchase else 160})
        test.assertEqual(allocation.status_code, 200, allocation.data)
    def refund_addon(existing_case_id=None):
        if existing_case_id is None:
            case = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                'reason': '乙的加购未履行', 'affected_provider_account_ids': [accounts[1].pk],
                'client_request_id': 'double-addon-b-case'}, format='json')
            test.assertEqual(case.status_code, 200, case.data)
            case_id = case.data['data']['id']
        else:
            case_id = existing_case_id
        amounts = [price, price] if two_addons else [price // 3, price - price // 3] if split and small else [3000, 7000] if split else [price]
        remaining_addon = addon_income * len(payment_ids)
        for index, amount in enumerate(amounts):
            refund_payment_id = payment_ids[index] if two_addons else payment_id
            staff_url = f'/api/admin/orders/{order.pk}/addons/payments/{refund_payment_id}/refund-proposals/'
            proposal = admin.post(staff_url, {'request_id': f'double-addon-b-refund-{index}', 'case_id': case_id,
                'amount_raw': amount, 'explanation': '乙的加购退款'}, format='json')
            test.assertEqual(proposal.status_code, 200, proposal.data)
            proposal_id = proposal.data['data']['id']
            user_url = f'/api/orders/orders/{order.pk}/addons/payments/{refund_payment_id}/refund-proposals/{proposal_id}/'
            test.assertEqual(players[0].get(user_url).status_code, 404)
            for actor in (boss, players[1]):
                test.assertEqual(actor.post(user_url, {'accepted': True}, format='json').status_code, 200)
            if (split or two_addons) and index == 1:
                from orders.models import OrderAddonFundingReleaseRun, OrderAddonRefund
                from wallet.services import get_wallet
                buyer_wallet = get_wallet(account=account, user=buyer)
                provider_wallet = get_wallet(account=accounts[1])
                balance = buyer_wallet.balance
                provider_balance = provider_wallet.balance
                transactions = list(Transaction.objects.filter(order=order).order_by('pk').values_list(
                    'pk', 'wallet_id', 'amount', 'tx_type'))
                refund_count = OrderAddonRefund.objects.filter(payment_id__in=payment_ids).count()
                run_count = OrderAddonFundingReleaseRun.objects.filter(refund__payment_id__in=payment_ids).count()
                with patch('orders.addon_funding_release.OrderAddonFundingReleaseRun.objects.create',
                           side_effect=RuntimeError('funding release failed')):
                    with test.assertRaisesMessage(RuntimeError, 'funding release failed'):
                        admin.post(staff_url + f'{proposal_id}/execute/', {}, format='json')
                buyer_wallet.refresh_from_db()
                provider_wallet.refresh_from_db()
                test.assertEqual(buyer_wallet.balance, balance)
                test.assertEqual(provider_wallet.balance, provider_balance)
                test.assertEqual(list(Transaction.objects.filter(order=order).order_by('pk').values_list(
                    'pk', 'wallet_id', 'amount', 'tx_type')), transactions)
                test.assertEqual(OrderAddonRefund.objects.filter(payment_id__in=payment_ids).count(), refund_count)
                test.assertEqual(OrderAddonFundingReleaseRun.objects.filter(refund__payment_id__in=payment_ids).count(), run_count)
            refund = admin.post(staff_url + f'{proposal_id}/execute/', {}, format='json')
            test.assertEqual(refund.status_code, 200, refund.data)
            own = refund.data['data']['participants'][0]
            addon_deduction = min(amount, remaining_addon)
            remaining_addon -= addon_deduction
            test.assertEqual((own['account_id'], own['addon_pending_deduction_raw'], own['main_pending_deduction_raw']),
                             (accounts[1].pk, addon_deduction, amount - addon_deduction))
            if two_addons:
                from orders.models import OrderAddonRefundEarningDeduction
                rows = OrderAddonRefundEarningDeduction.objects.filter(
                    refund__proposal_id=proposal_id).order_by('payment_id').values_list('payment_id', 'amount')
                expected_deductions = ([(payment_ids[0], 210), (payment_ids[1], 90)] if index == 0
                                       else [(payment_ids[1], 120)])
                test.assertEqual(list(rows), expected_deductions)
        resolved = admin.post(f'/api/admin/aftersales/{case_id}/resolve/', {'resolution': '乙加购退款完成'}, format='json')
        test.assertEqual(resolved.status_code, 200, resolved.data)

    if not main_first:
        refund_addon()

    def approve_reports():
        reviewer_user = make_console_user(['report:view', 'report:audit'])
        reviewer = get_or_create_account_for_legacy_user(reviewer_user)
        reviewer_user.admin_membership.account = reviewer
        reviewer_user.admin_membership.save(update_fields=['account'])
        review = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer)[0])
        reports = []
        for player, provider in zip(players, accounts):
            report = ProviderReport.objects.get(order=order, provider_account=provider)
            for kind in ('COMPLETION', 'RESULT'):
                response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                    {'kind': kind, 'image': screenshot()}, format='multipart')
                test.assertEqual(response.data['code'], 0, response.data)
            response = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            test.assertEqual(response.data['code'], 0, response.data)
            response = review.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            test.assertEqual(response.data['code'], 0, response.data)
            report.refresh_from_db()
            reports.append(report)
        return reports

    reports = None
    if preapproved:
        for player in players:
            finished = player.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image': screenshot()}, format='multipart')
            test.assertEqual(finished.data['code'], 0, finished.data)
        reports = approve_reports()
        case = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {
            'reason': '甲的主单部分退款', 'affected_provider_account_ids': [accounts[0].pk],
            'client_request_id': 'double-main-a-case'}, format='json')
    else:
        case = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
            'reason': '甲的主单部分退款', 'affected_provider_account_ids': [accounts[0].pk],
            'client_request_id': 'double-main-a-case'}, format='json')
    test.assertEqual(case.status_code, 200, case.data)
    case_id = case.data['data']['id']
    proposed = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
        'request_id': 'double-main-a', 'expected_version': 0, 'refund_units': 500,
        'provider_terms': [{'account_id': accounts[0].pk, 'retained_units': 5500}],
        'explanation': '甲的主单部分退款'}, format='json')
    test.assertEqual(proposed.status_code, 200, proposed.data)
    proposal_id = proposed.data['data']['id']
    url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
    test.assertEqual(players[1].get(url).status_code, 404)
    for actor in (boss, players[0]):
        response = actor.post(url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
        test.assertEqual(response.data['code'], 0, response.data)
    posted = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
        'proposal_id': proposal_id, 'client_request_id': 'double-main-a-execute'}, format='json')
    test.assertEqual(posted.status_code, 200, posted.data)
    coin_refunded = posted.data['data']['refund_coin_units']
    if late_purchase:
        buy_second()
    settled_before_addon = None
    if main_first and partially_settled:
        from itertools import count
        continued = players[1].post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': screenshot()}, format='multipart')
        test.assertEqual(continued.data['code'], 0, continued.data)
        reports = approve_reports()
        case = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {
            'reason': '乙的加购售后', 'affected_provider_account_ids': [accounts[1].pk],
            'client_request_id': 'double-addon-b-case'}, format='json')
        test.assertEqual(case.status_code, 200, case.data)
        future = max(row.submitted_at for row in reports) + timedelta(hours=73)
        ticks = count()
        with patch('django.utils.timezone.now', side_effect=lambda: future + timedelta(microseconds=next(ticks))):
            test.assertFalse(process_order(order.pk))
            settled_before_addon = Transaction.objects.get(order=order, tx_type='INCOME', wallet__account=accounts[0],
                                                             remark=f'订单收入：{order.order_no}')
            test.assertEqual(settled_before_addon.amount, 5500)
            test.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME', wallet__account=accounts[1]).exists())
            refund_addon(case.data['data']['id'])
    elif main_first and not keep_addons:
        refund_addon()
    if main_first and not (keep_addons or late_purchase or partially_settled or two_addons or split or gift):
        from console.roi_period import points_negotiation_cost
        now = timezone.now()
        cost = points_negotiation_cost(type(order).objects.get(pk=order.pk),
                                       now - timedelta(days=1), now + timedelta(days=1))
        test.assertIsNotNone(cost)
        test.assertEqual(cost['provider'], 11250 + addon_income - 500 - price)
        test.assertEqual((cost['referral'], cost['gift_compensation']), (0, 0))
    if main_first and not keep_addons:
        from orders.models import OrderAddonFundingReleaseRun
        from orders.addon_funding_release import verify_run
        from rest_framework.exceptions import ValidationError
        from stores.db import transaction
        runs = list(OrderAddonFundingReleaseRun.objects.filter(refund__payment_id__in=payment_ids).order_by('refund_id'))
        test.assertEqual(len(runs), 2 if split or two_addons else 1)
        run = runs[-1]
        with transaction.atomic():
            modified = dict(run.refunded_main_snapshot)
            modified['funding'] = {**modified['funding'], 'provider_income_units': 1}
            OrderAddonFundingReleaseRun.objects.filter(pk=run.pk).update(refunded_main_snapshot=modified)
            run.refresh_from_db()
            with test.assertRaises(ValidationError):
                verify_run(run)
            transaction.set_rollback(True)
        if preallocate:
            run.refresh_from_db()
            expected = ((220, 20, 200) if gift and split else (320, 20, 300) if gift else
                        (0, 0, 0) if two_addons else (20, 20, 0))
            test.assertEqual((run.allocated_before, run.required_amount, run.released_amount), expected)
            if gift and split:
                test.assertEqual((runs[0].allocated_before, runs[0].required_amount, runs[0].released_amount),
                                 (320, 220, 100))
            test.assertEqual(sum(item[1] for item in run.allocation_snapshot[0][4]),
                             70 if two_addons and not late_purchase else 140)
    if not preapproved and not partially_settled:
        continued = players[1].post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': screenshot()}, format='multipart')
        test.assertEqual(continued.data['code'], 0, continued.data)
        reports = approve_reports()
    expected_reports = [6000, 5250] if preapproved else [5500, 5250] if partially_settled else [5500, b_income]
    test.assertEqual([row.payout_amount for row in reports], expected_reports)
    retained_addon_paid = price * len(payment_ids) if keep_addons else 0
    retained_addon_income = addon_income * len(payment_ids) if keep_addons else 0
    required = max(0, 5500 + b_income + retained_addon_income
                   - (11000 - coin_refunded + retained_addon_paid - (price if late_gift else 0)))
    if late_gift:
        with patch('django.utils.timezone.now', return_value=max(row.submitted_at for row in reports) + timedelta(hours=73)):
            test.assertFalse(process_order(order.pk))
        test.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        allocation = test.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': required - 20})
        test.assertEqual(allocation.status_code, 200, allocation.data)
    if small and not preallocate:
        test.assertGreater(required, 0)
        with patch('django.utils.timezone.now', return_value=max(row.submitted_at for row in reports) + timedelta(hours=73)):
            test.assertFalse(process_order(order.pk))
        test.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        test.assertEqual(test.verify().status_code, 200)
        allocation = test.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': required})
        test.assertEqual(allocation.status_code, 200, allocation.data)
    completion_time = max(row.submitted_at for row in reports) + timedelta(hours=73, seconds=1 if partially_settled else 0)
    with patch('django.utils.timezone.now', return_value=completion_time):
        test.assertTrue(process_order(order.pk))
        test.assertFalse(process_order(order.pk))
    settlement = OrderPointsRefundSettlement.objects.get(posting__proposal_id=proposal_id)
    test.assertEqual(settlement.provider_units, 5500 + b_income)
    test.assertEqual(settlement.addon_paid_units, retained_addon_paid)
    test.assertEqual(settlement.addon_provider_units, retained_addon_income)
    test.assertEqual(settlement.funding_units, required)
    addon_rows = OrderAddonSettlement.objects.filter(refund_settlement=settlement)
    if keep_addons:
        test.assertEqual(sorted(addon_rows.values_list('payment_id', 'amount')),
                         sorted((pk, addon_income) for pk in payment_ids))
    else:
        test.assertFalse(addon_rows.exists())
    balances = dict(Transaction.objects.filter(order=order, tx_type='INCOME',
        remark=f'订单收入：{order.order_no}').values_list('wallet__account_id', 'amount'))
    test.assertEqual(balances, {accounts[0].pk: 5500, accounts[1].pk: b_income})
    if settled_before_addon:
        test.assertEqual(Transaction.objects.filter(pk=settled_before_addon.pk, amount=5500).count(), 1)
    confirmation = ConsumptionConfirmation.objects.get(order=order)
    test.assertEqual((confirmation.amount, confirmation.refunded_amount), (11000, coin_refunded))
    for payment_id in payment_ids:
        consumption = OrderAddonConsumption.objects.get(payment_id=payment_id)
        test.assertEqual((consumption.paid_units, consumption.refunded_units), (price, 0 if keep_addons else price))
