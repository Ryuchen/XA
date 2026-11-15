from django.test import override_settings
from django.test import TestCase
from django.conf import settings
from unittest import skipUnless
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase, APITransactionTestCase
from stores.db import transaction

from orders.negotiation_execution import prepare_locked
from orders.negotiation_proposals import decide
from orders.tests.factories import make_console_user
from orders.tests.test_negotiation_proposals import NegotiationProposalTests
from wallet.models import Transaction, Wallet
from wallet.services import get_wallet


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True)
class NegotiationExecutionPreparationTests(NegotiationProposalTests):
    def accepted(self, refund=5000):
        case = self.case()
        proposal = self.propose(case, refund=refund)
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        return case, proposal

    def refund_staff(self):
        return self.account(make_console_user(['order:view', 'order:refund']))

    def test_locked_preparation_uses_pending_then_wallet_then_deposit_without_posting(self):
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 600; wallet.save(update_fields=['balance'])
        profile = self.a.escort_profile
        profile.deposit_paid = 800; profile.save(update_fields=['deposit_paid'])
        case, proposal = self.accepted()
        before = (wallet.balance, profile.deposit_paid, Transaction.objects.count())
        with transaction.atomic():
            plan = prepare_locked(case.pk, proposal.pk, operator=self.refund_staff())
            person = plan['providers'][0]
            self.assertEqual((person['pending_units'], person['wallet_units'], person['deposit_units'],
                              person['shortfall_units']), (4000, 600, 400, 0))
            self.assertEqual(plan['calculation']['provider_terms'][0]['retained_units'], 0)
        wallet.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((wallet.balance, profile.deposit_paid, Transaction.objects.count()), before)
        self.assertFalse(self.order.refund_records.exists())


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NegotiationExecutionPostingTests(APITestCase):
    account = NegotiationProposalTests.account
    client_for = NegotiationProposalTests.client_for
    case = NegotiationProposalTests.case
    propose = NegotiationProposalTests.propose

    def setUp(self):
        NegotiationProposalTests.setUp(self)
        self.operator = make_console_user(['order:view', 'order:refund'])
        self.operator_account = self.account(self.operator)

    refund_staff = NegotiationExecutionPreparationTests.refund_staff

    def accepted(self, amount=5000, *, refund=None):
        if refund is not None:
            amount = refund
        case = self.case(); proposal = self.propose(case, refund=amount)
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        return case, proposal

    def execute(self, case, proposal, request_id='refund-execution-1'):
        from orders.negotiation_execution import apply_refund
        return apply_refund(case.pk, proposal.pk, operator=self.operator_account,
                            legacy_operator=self.operator, request_id=request_id)

    def cash_deposit(self, amount):
        from wallet.models import CashDepositTopup, get_platform_wallet
        profile = self.a.escort_profile
        profile.deposit_mode = 'CASH'; profile.deposit_paid = amount
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        platform = get_platform_wallet(); before = platform.balance
        platform.balance += amount; platform.save(update_fields=['balance'])
        source = Transaction.objects.create(wallet=platform, tx_type='DEPOSIT_INCOME', amount=amount,
                                            balance_before=before, balance_after=platform.balance)
        CashDepositTopup.objects.create(profile=profile, account=self.a_account, operator=self.operator_account,
            amount=amount, paid_before=0, paid_after=amount, target_snapshot=amount,
            reason='隔离测试收款', reference='cash-proof', client_request_id='cash-proof', platform_transaction=source)
        return profile, platform

    def test_deposit_clock_excludes_only_verified_unaffected_teammate(self):
        from console.deposit_clearance import _verified_unaffected_refund
        case, proposal = self.accepted(1000)
        receipt = self.execute(case, proposal)
        refund = receipt.refund
        self.assertTrue(_verified_unaffected_refund(refund, self.b_account.pk))
        self.assertFalse(_verified_unaffected_refund(refund, self.a_account.pk))
        refund.negotiated_allocation[0]['amount'] += 1
        refund.save(update_fields=['negotiated_allocation'])
        self.assertFalse(_verified_unaffected_refund(refund, self.b_account.pk))

    def test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction(self):
        from wallet.models import RefundDepositDeficit
        boss_wallet = get_wallet(user=self.boss, account=self.boss_account)
        player_wallet = get_wallet(user=self.a, account=self.a_account)
        player_wallet.balance = 600; player_wallet.save(update_fields=['balance'])
        unaffected_wallet = get_wallet(user=self.b, account=self.b_account)
        unaffected_wallet.balance = 900; unaffected_wallet.save(update_fields=['balance'])
        profile, platform = self.cash_deposit(500)
        case, proposal = self.accepted(5000)
        receipt = self.execute(case, proposal)
        self.assertEqual(self.execute(case, proposal).pk, receipt.pk)
        boss_wallet.refresh_from_db(); player_wallet.refresh_from_db(); unaffected_wallet.refresh_from_db()
        profile.refresh_from_db(); platform.refresh_from_db(); self.order.refresh_from_db()
        share = receipt.shares.get()
        self.assertEqual((boss_wallet.balance, player_wallet.balance, unaffected_wallet.balance,
                          profile.deposit_paid, platform.balance), (14000, 0, 900, 100, 100))
        self.assertEqual((share.pending_deduction, share.wallet_deduction, share.deposit_deduction,
                          share.retained_income), (4000, 600, 400, 0))
        self.assertEqual((receipt.refund.amount, receipt.refund.recovered_amount, self.order.payment_status),
                         (5000, 1000, 'PARTIALLY_REFUNDED'))
        self.assertEqual(RefundDepositDeficit.objects.get(refund=receipt.refund).remaining, 400)
        self.assertEqual(self.order.refund_records.count(), 1)
        self.assertEqual(case.status, 'OPEN')
        self.assertFalse(self.order.settled_at)

    def test_exception_after_boss_credit_rolls_back_all_funds_and_receipts(self):
        from unittest.mock import patch
        from engagement.models import OrderFundingAllocation, PointFundingLot
        from wallet.models import RefundDepositDeficit, get_platform_wallet
        from orders.models import NegotiationExecution
        boss = get_wallet(user=self.boss, account=self.boss_account)
        player = get_wallet(user=self.a, account=self.a_account)
        player.balance = 600; player.save(update_fields=['balance'])
        profile, platform = self.cash_deposit(500)
        self.order.funding_terms = {'vip_enabled':True, 'points_enabled':True, 'points_rate':100,
                                    'birthday_multiplier':1}
        self.order.save(update_fields=['funding_terms'])
        lot = PointFundingLot.objects.create(account=self.boss_account, source_key='rollback-execution-principal',
                                             kind='PREPAID', remaining=0)
        source = OrderFundingAllocation.objects.create(order=self.order, lot=lot, kind='PREPAID', amount=10000)
        case, proposal = self.accepted(5000)
        before = (boss.balance, player.balance, profile.deposit_paid, platform.balance, Transaction.objects.count())
        with patch('wallet.refund_deposit.charge_refund_deposit', side_effect=RuntimeError('模拟扣押金失败')):
            with self.assertRaisesMessage(RuntimeError, '模拟扣押金失败'):
                self.execute(case, proposal)
        boss.refresh_from_db(); player.refresh_from_db(); profile.refresh_from_db(); platform.refresh_from_db()
        lot.refresh_from_db(); source.refresh_from_db()
        self.assertEqual((boss.balance, player.balance, profile.deposit_paid, platform.balance, Transaction.objects.count()), before)
        self.assertEqual((lot.remaining, source.refunded_amount), (0, 0))
        self.assertFalse(self.order.refund_records.exists())


        self.assertFalse(NegotiationExecution.objects.exists())
        self.assertFalse(RefundDepositDeficit.objects.exists())

    def test_insufficient_funds_no_refund_and_no_partial_wallet_debit(self):
        boss = get_wallet(user=self.boss, account=self.boss_account)
        player = get_wallet(user=self.a, account=self.a_account)
        player.balance = 500; player.save(update_fields=['balance'])
        case, proposal = self.accepted(5000)
        before = (boss.balance, player.balance, Transaction.objects.count())
        with self.assertRaisesMessage(ValidationError, '不足'):
            self.execute(case, proposal)
        boss.refresh_from_db(); player.refresh_from_db()
        self.assertEqual((boss.balance, player.balance, Transaction.objects.count()), before)
        self.assertFalse(self.order.refund_records.exists())

    def test_retry_rejects_changed_request_or_tampered_posting(self):
        get_wallet(user=self.a, account=self.a_account)
        case, proposal = self.accepted(1000)
        receipt = self.execute(case, proposal)
        with self.assertRaisesMessage(ValidationError, '已处理'):
            self.execute(case, proposal, request_id='other-request')
        tx = receipt.customer_transaction
        tx.amount = 990; tx.save(update_fields=['amount'])
        with self.assertRaisesMessage(ValidationError, '凭证不一致'):
            self.execute(case, proposal)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_unrelated_legacy_operator_is_rejected_before_posting(self):
        from orders.negotiation_execution import apply_refund
        get_wallet(user=self.a, account=self.a_account)
        case, proposal = self.accepted(1000)
        stranger = make_console_user(['order:refund'])
        with self.assertRaisesMessage(ValidationError, '历史身份不一致'):
            apply_refund(case.pk, proposal.pk, operator=self.operator_account,
                         legacy_operator=stranger, request_id='mismatched-operator')
        self.assertFalse(self.order.refund_records.exists())

    def test_retry_rejects_tampered_provider_allocation(self):
        get_wallet(user=self.a, account=self.a_account)
        case, proposal = self.accepted(1000)
        receipt = self.execute(case, proposal)
        receipt.refund.negotiated_allocation[0]['retained'] += 1
        receipt.refund.save(update_fields=['negotiated_allocation'])
        with self.assertRaisesMessage(ValidationError, '协商退款分摊记录'):
            self.execute(case, proposal)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_replay_still_verifies_after_case_is_reopened_for_later_episode(self):
        from django.utils import timezone
        get_wallet(user=self.a, account=self.a_account)
        case, proposal = self.accepted(1000)
        receipt = self.execute(case, proposal)
        case.created_at = timezone.now(); case.client_request_id = 'later-episode'
        case.save(update_fields=['created_at', 'client_request_id'])
        self.assertEqual(self.execute(case, proposal).pk, receipt.pk)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_original_funding_sources_return_principal_before_gift_and_compensation(self):
        from engagement.models import OrderFundingAllocation, PointFundingLot
        player = get_wallet(user=self.a, account=self.a_account)
        player.balance = 1000; player.save(update_fields=['balance'])
        self.order.funding_terms = {'vip_enabled':True, 'points_enabled':True, 'points_rate':100,
                                    'birthday_multiplier':1}
        self.order.save(update_fields=['funding_terms'])
        lots = {}
        for kind, amount in (('PREPAID', 5000), ('GIFT', 3000), ('COMPENSATION', 2000)):
            lot = PointFundingLot.objects.create(account=self.boss_account, source_key=f'execution-{kind}',
                                                 kind=kind, remaining=0)
            OrderFundingAllocation.objects.create(order=self.order, lot=lot, kind=kind, amount=amount)
            lots[kind] = lot
        case, proposal = self.accepted(5000)
        self.execute(case, proposal)
        for lot in lots.values(): lot.refresh_from_db()
        self.assertEqual((lots['PREPAID'].remaining, lots['GIFT'].remaining, lots['COMPENSATION'].remaining),
                         (5000, 0, 0))
        allocation = OrderFundingAllocation.objects.get(order=self.order, kind='PREPAID')
        allocation.refunded_amount -= 10; allocation.save(update_fields=['refunded_amount'])
        with self.assertRaisesMessage(ValidationError, '原资金来源记录不完整'):
            self.execute(case, proposal)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_double_provider_refund_tracks_per_person_rounding_surplus(self):
        for user, account in ((self.a, self.a_account), (self.b, self.b_account)):
            get_wallet(user=user, account=account)
        scope = sorted([self.a_account.pk, self.b_account.pk])
        case = self.case(scope)
        proposal = self.propose(case, scope=scope, refund=510)
        for actor in (self.boss_account, self.a_account, self.b_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        receipt = self.execute(case, proposal)
        self.assertEqual(receipt.rounding_to_store_units, 10)
        self.assertEqual(list(receipt.shares.order_by('account_id').values_list('burden', 'retained_income')),
                         [(260, 3740), (260, 3740)])
        self.assertEqual(receipt.refund.amount, 510)

    def test_refund_does_not_reverse_unaffected_provider_already_settled(self):
        from django.utils import timezone
        get_wallet(user=self.a, account=self.a_account)
        case = self.case(); proposal = self.propose(case, refund=1000)
        self.order.status = 'COMPLETED'; self.order.completed_at = timezone.now()
        self.order.save(update_fields=['status', 'completed_at'])
        share = self.order.providers.get(provider_account=self.b_account)
        share.settled_at = timezone.now(); share.save(update_fields=['settled_at'])
        wallet = get_wallet(user=self.b, account=self.b_account)
        wallet.balance = 4000; wallet.save(update_fields=['balance'])
        credit = Transaction.objects.create(wallet=wallet, order=self.order, tx_type='INCOME', amount=4000,
            balance_before=0, balance_after=4000, remark=f'订单收入：{self.order.order_no}')
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        receipt = self.execute(case, proposal)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 4000)
        self.assertFalse(credit.reversals.exists())
        allocation = next(row for row in receipt.refund.negotiated_allocation if row['account_id'] == self.b_account.pk)
        self.assertEqual((allocation['amount'], allocation['retained'], allocation['settled']), (0, 4000, True))

    def test_full_refund_restores_eligible_coupon_and_records_result(self):
        from coupons.models import UserCoupon
        from orders.tests.factories import make_user_coupon
        player = get_wallet(user=self.a, account=self.a_account)
        player.balance = 6000; player.save(update_fields=['balance'])
        coupon = make_user_coupon(self.boss, account=self.boss_account, status='USED', order=self.order)
        case, proposal = self.accepted(10000)
        receipt = self.execute(case, proposal)
        coupon.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((coupon.status, coupon.order_id, self.order.payment_status), ('UNUSED', None, 'REFUNDED'))
        self.assertEqual(receipt.refund.benefit_effects['order_coupon_results'][0]['outcome'], 'RETURNED')

    def test_insufficient_total_never_touches_wallet_or_creates_refund(self):
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 300; wallet.save(update_fields=['balance'])
        profile = self.a.escort_profile
        profile.deposit_paid = 600; profile.save(update_fields=['deposit_paid'])
        case, proposal = self.accepted()
        before = Transaction.objects.count()
        with transaction.atomic(), self.assertRaisesMessage(ValidationError, '不足'):
            prepare_locked(case.pk, proposal.pk, operator=self.refund_staff())
        wallet.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((wallet.balance, profile.deposit_paid, Transaction.objects.count()), (300, 600, before))
        self.assertFalse(self.order.refund_records.exists())

    def test_agreement_must_be_latest_and_all_parties_must_accept(self):
        case, first = self.accepted(refund=1000)
        second = self.propose(case, version=1, request_id='new-version', refund=2000)
        staff = self.refund_staff()
        with transaction.atomic(), self.assertRaisesMessage(ValidationError, '方案已变化'):
            prepare_locked(case.pk, first.pk, operator=staff)
        with transaction.atomic(), self.assertRaisesMessage(ValidationError, '尚未获得'):
            prepare_locked(case.pk, second.pk, operator=staff)
        decide(case.pk, actor=self.a_account, proposal_id=second.pk, accepted=False)
        with transaction.atomic(), self.assertRaisesMessage(ValidationError, '尚未获得'):
            prepare_locked(case.pk, second.pk, operator=staff)

    def test_permission_and_changed_financial_snapshot_are_rechecked(self):
        case, proposal = self.accepted(refund=1000)
        with transaction.atomic(), self.assertRaisesMessage(ValidationError, '退款权限'):
            prepare_locked(case.pk, proposal.pk, operator=self.staff)
        self.order.providers.filter(provider_account=self.a_account).update(provider_income=3900)
        with transaction.atomic(), self.assertRaises(ValidationError):
            prepare_locked(case.pk, proposal.pk, operator=self.refund_staff())
        self.assertFalse(self.order.refund_records.exists())

    def test_other_provider_cannot_fund_the_affected_person(self):
        get_wallet(user=self.a, account=self.a_account)
        other_wallet = get_wallet(user=self.b, account=self.b_account)
        other_wallet.balance = 10000; other_wallet.save(update_fields=['balance'])
        case, proposal = self.accepted()
        with transaction.atomic(), self.assertRaisesMessage(ValidationError, '不足'):
            prepare_locked(case.pk, proposal.pk, operator=self.refund_staff())
        other_wallet.refresh_from_db()
        self.assertEqual(other_wallet.balance, 10000)

    def test_funding_lot_is_checked_again_after_agreement(self):
        from engagement.models import OrderFundingAllocation, PointFundingLot
        get_wallet(user=self.a, account=self.a_account)
        case, proposal = self.accepted(refund=1000)
        self.order.funding_terms = {'vip_enabled': True}
        self.order.save(update_fields=['funding_terms'])
        lot = PointFundingLot.objects.create(account=self.boss_account, source_key='negotiation-execution-lot',
                                              kind='PREPAID', remaining=0)
        OrderFundingAllocation.objects.create(order=self.order, lot=lot, kind='PREPAID', amount=10000)
        staff = self.refund_staff()
        with transaction.atomic():
            self.assertEqual(prepare_locked(case.pk, proposal.pk, operator=staff)['proposal'].pk, proposal.pk)
        lot.kind = 'GIFT'; lot.save(update_fields=['kind'])
        with transaction.atomic(), self.assertRaisesMessage(ValidationError, '资金来源'):
            prepare_locked(case.pk, proposal.pk, operator=staff)
        self.assertFalse(self.order.refund_records.exists())


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class NegotiationPostingActualOrderTests(APITransactionTestCase):
    from orders.tests.test_provider_aftersale_status import ProviderAfterSaleStatusTest as flow
    setUp = flow.setUp
    login = flow.login
    ok = flow.ok

    def test_real_order_and_confirmed_proposal_refund_the_boss_in_isolation(self):
        from orders.models import Order
        from orders.negotiation_execution import apply_refund
        from orders.tests.test_provider_aftersale_status import image
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'post-real-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':image()}, format='multipart'))
        get_wallet(user=self.provider, account=self.accounts[self.provider.pk])
        boss_wallet = get_wallet(user=self.boss, account=self.accounts[self.boss.pk])
        before = boss_wallet.balance
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'order:refund']; role.save(update_fields=['permissions'])
        self.login(self.staff)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'affected_provider_account_ids':[self.accounts[self.provider.pk].pk], 'reason':'联系协商',
            'client_request_id':'post-real-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id':'post-real-proposal', 'expected_version':0, 'refund_units':5000,
            'provider_terms':[{'account_id':self.accounts[self.provider.pk].pk, 'retained_units':3000}],
            'explanation':'退款并保留已履约收益'}, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id':proposal['id'], 'accepted':True}, format='json'))
        receipt = apply_refund(case['id'], proposal['id'], operator=self.accounts[self.staff.pk],
                               legacy_operator=self.staff, request_id='post-real-refund')
        boss_wallet.refresh_from_db(); order = Order.objects.get(pk=order_id)
        self.assertEqual(boss_wallet.balance, before + 5000)
        self.assertEqual((receipt.refund.amount, receipt.shares.get().retained_income, order.payment_status),
                         (5000, 3000, 'PARTIALLY_REFUNDED'))
        self.assertEqual(order.refund_records.count(), 1)
        self.assertFalse(order.settled_at)


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class NegotiationPostingStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_matching_ids_refund_only_the_authenticated_store(self):
        from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
        from club_accounts.tokens import issue_account_tokens
        from orders.models import OrderProvider, OrderRefund
        from orders.negotiation_execution import apply_refund
        from orders.tests.factories import make_order, make_provider, make_service, make_user
        from rest_framework.test import APIClient
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        rows = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                boss, player = make_user(), make_provider()
                boss_account = get_or_create_account_for_legacy_user(boss)
                player_account = get_or_create_account_for_legacy_user(player)
                link_legacy_relations(boss, boss_account); link_legacy_relations(player, player_account)
                player.escort_profile.account = player_account; player.escort_profile.save(update_fields=['account'])
                staff_user = make_console_user(['order:view', 'support:edit', 'order:refund'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                order = make_order(boss, service=make_service(price=1000), provider=player, status='IN_SERVICE',
                    customer_account=boss_account, provider_account=player_account, payment_status='PAID',
                    original_amount=1000, provider_income=800, shop_income=200)
                OrderProvider.objects.create(order=order, provider=player, provider_account=player_account,
                    settlement_base=1000, provider_income=800, commission_rate=20)
                wallet = get_wallet(user=boss, account=boss_account); wallet.balance = 2000; wallet.save(update_fields=['balance'])
                get_wallet(user=player, account=player_account)
                Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-1000,
                                           balance_before=3000, balance_after=2000)
                def client(account):
                    return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                admin, customer, provider_client = client(staff), client(boss_account), client(player_account)
                case = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id':order.pk,
                    'affected_provider_account_ids':[player_account.pk], 'reason':'门店协商',
                    'client_request_id':'same-case'}, format='json').data['data']
                proposal = admin.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
                    'request_id':'same-proposal', 'expected_version':0, 'refund_units':100,
                    'provider_terms':[{'account_id':player_account.pk, 'retained_units':700}],
                    'explanation':'本店协商退款'}, format='json').data['data']
                for member in (customer, provider_client):
                    result = member.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                         {'proposal_id':proposal['id'], 'accepted':True}, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                rows.append((store, order.pk, case['id'], proposal['id'], staff, staff_user, wallet.pk))
        self.assertEqual(rows[0][1:4], rows[1][1:4])
        with store_context(rows[0][0]):
            receipt = apply_refund(rows[0][2], rows[0][3], operator=rows[0][4], legacy_operator=rows[0][5],
                                   request_id='same-refund')
            self.assertEqual(receipt.refund.amount, 100)
            self.assertEqual(Wallet.objects.get(pk=rows[0][6]).balance, 2100)
            self.assertEqual(OrderRefund.objects.count(), 1)
        with store_context(rows[1][0]):
            self.assertEqual(Wallet.objects.get(pk=rows[1][6]).balance, 2000)
            self.assertEqual(OrderRefund.objects.count(), 0)
