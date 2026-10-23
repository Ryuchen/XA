"""Settled refunds recover original successful cashflows, atomically and once."""
from datetime import timedelta
from unittest.mock import patch
from django.db import transaction
from django.db.models import Sum
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation, MemberProfile, PointLedger, RewardClaim, RewardItem, VipTier
from engagement.services import allocate_order_points, completed_consumption
from orders.models import AfterSale, Order, OrderProvider, OrderRefund, RefundRecoveryLine
from orders.refunds import build_refund_plan
from orders.state_machine import transition, IllegalTransitionError
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user, make_boss_type
from wallet.income import release_due_income
from wallet.models import IncomeHold, ProviderReport, Transaction, WithdrawRequest, get_platform_wallet
from wallet.services import get_wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',LEGACY_FORCE_AUTH_COMPAT=False,REQUIRE_ORDER_EVIDENCE_IMAGES=False)
class SettledRefundTest(APITestCase):
    def setUp(self):
        self.boss, self.provider, self.other = make_user(), make_provider(deposit_mode='CASH'), make_provider(deposit_mode='CASH')
        self.admin_user = make_console_user(['order:view','order:refund','order:settle','wallet:view','transaction:view','withdraw:view','withdraw:audit','escort:deposit_refund'])
        self.accounts = {}
        for user in (self.boss,self.provider,self.other,self.admin_user):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user,account)
            self.accounts[user.pk] = account
            if user in (self.provider,self.other):
                profile = user.escort_profile
                profile.account = account
                profile.save(update_fields=['account'])
        member = self.admin_user.admin_membership
        member.account = self.accounts[self.admin_user.pk]
        member.save(update_fields=['account'])
        self.boss_account = self.accounts[self.boss.pk]
        self.provider_account = self.accounts[self.provider.pk]
        self.admin_account = self.accounts[self.admin_user.pk]
        self.boss_wallet = get_wallet(user=self.boss,account=self.boss_account)
        self.boss_wallet.balance = 100000
        self.boss_wallet.save(update_fields=['balance'])
        self.provider_wallet = get_wallet(user=self.provider,account=self.provider_account)
        self.platform = get_platform_wallet()
        self.admin, self.customer = self.client_for(self.admin_account), self.client_for(self.boss_account)
        self.now = timezone.now()

    def client_for(self,account):
        client = APIClient()
        token,_ = issue_account_tokens(account)
        client.credentials(HTTP_AUTHORIZATION='Bearer '+token)
        return client

    def settled(self,*,amount=10000,hold=0,double=False,inviter=None,confirmed=False,perform_settlement=True):
        service = make_service(price=amount,commission_rate=20)
        net, invite = amount*80//100, 0
        if inviter:
            invite = amount*5//100
            net -= invite
        order = make_order(self.boss,service,self.provider,customer_account=self.boss_account,
            provider_account=self.provider_account,status='COMPLETED',payment_status='PAID',amount=amount,
            provider_income=net,shop_income=amount-net-invite,commission_rate=20,
            completed_at=self.now-timedelta(hours=75),settlement_mode='REPORT_APPROVAL',
            inviter=inviter,inviter_account=get_or_create_account_for_legacy_user(inviter) if inviter else None,
            inviter_commission=invite,escort_mode='DOUBLE' if double else 'SINGLE')
        order.income_hold_hours = hold
        order.save(update_fields=['income_hold_hours'])
        providers = (self.provider,self.other) if double else (self.provider,)
        if double:
            for user in providers:
                OrderProvider.objects.create(order=order,provider=user,provider_account=self.accounts[user.pk],
                    settlement_base=amount//2,provider_income=net//2,commission_rate=20)
        for user in providers:
            ProviderReport.objects.create(order=order,provider=user,provider_account=self.accounts[user.pk],
                amount=amount//len(providers),status='APPROVED',game_name='隔离追回样例',
                submitted_at=self.now-timedelta(hours=74) if confirmed else self.now)
        with transaction.atomic():
            self.boss_wallet.refresh_from_db()
            before = self.boss_wallet.balance
            self.boss_wallet.balance -= amount
            self.boss_wallet.save(update_fields=['balance'])
            Transaction.objects.create(wallet=self.boss_wallet,order=order,tx_type='PAY',amount=-amount,
                balance_before=before,balance_after=self.boss_wallet.balance)
            allocate_order_points(order,self.boss_wallet,before)
        if perform_settlement:
            process_order(order.pk)
        order.refresh_from_db()
        if perform_settlement:
            self.assertIsNotNone(order.settled_at)
        return order

    def preview(self,order):
        response = self.admin.get(f'/api/admin/orders/{order.pk}/refund-preview/')
        self.assertEqual(response.data['code'],0,response.data)
        return response.data['data']

    def refund(self,order,*,token=None,aftersale=None):
        payload = {'reason':'隔离全额退款','resolution':'隔离售后退款','plan_token':token or self.preview(order)['plan_token']}
        path = f'/api/admin/aftersales/{aftersale.pk}/refund/' if aftersale else f'/api/admin/orders/{order.pk}/refund/'
        return self.admin.post(path,payload,format='json')

    def ok(self,response):
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['code'],0,response.data)
        return response.data.get('data')

    def snapshot(self):
        return {'wallets':list(Transaction._meta.get_field('wallet').related_model.objects.order_by('pk').values()),
                'tx':list(Transaction.objects.order_by('pk').values()),'holds':list(IncomeHold.objects.order_by('pk').values()),
                'refunds':list(OrderRefund.objects.order_by('pk').values()),'lines':list(RefundRecoveryLine.objects.order_by('pk').values()),
                'profiles':list(type(self.provider.escort_profile).objects.order_by('pk').values())}

    def assert_reversed(self,order,expected_lines):
        order.refresh_from_db()
        self.assertEqual((order.status,order.payment_status),('CANCELLED','REFUNDED'))
        record = OrderRefund.objects.get(order=order)
        self.assertEqual(record.recovery_lines.count(),expected_lines)
        for line in record.recovery_lines.select_related('original_transaction','reversal_transaction'):
            a,b = line.original_transaction,line.reversal_transaction
            self.assertEqual((b.reversal_of_id,b.amount,b.tx_type,b.status),(a.pk,-a.amount,a.tx_type,'SUCCESS'))
            self.assertEqual(b.balance_before+b.amount,b.balance_after)
        self.assertEqual(order.transactions.filter(tx_type='INCOME',status='SUCCESS').aggregate(v=Sum('amount'))['v'],0)
        return record

    def test_available_income_and_platform_commission_reverse_once(self):
        order = self.settled()
        self.ok(self.refund(order))
        self.assert_reversed(order,2)
        self.provider_wallet.refresh_from_db(); self.platform.refresh_from_db(); self.boss_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.platform.balance,self.boss_wallet.balance),(0,0,100000))
        before = self.snapshot()
        self.assertNotEqual(self.refund(order,token='replay').data['code'],0)
        self.assertEqual(before,self.snapshot())
        response = self.client_for(self.provider_account).get('/api/wallet/transactions/')
        self.assertEqual(response.data['data']['total_income'],0)
        self.assertTrue(any(row['is_reversal'] and row['reversal_of'] for row in response.data['data']['transactions']))

    def test_own_hold_is_cancelled_without_touching_foreign_or_withdrawal_frozen(self):
        order = self.settled(hold=72)
        foreign = self.settled(amount=5000,hold=72)
        self.provider_wallet.refresh_from_db()
        self.provider_wallet.frozen_amount += 1000
        self.provider_wallet.save(update_fields=['frozen_amount'])
        hold = IncomeHold.objects.get(income_transaction__order=order)
        other_hold = IncomeHold.objects.get(income_transaction__order=foreign)
        self.ok(self.refund(order))
        self.assert_reversed(order,3)
        hold.refresh_from_db(); other_hold.refresh_from_db(); self.provider_wallet.refresh_from_db()
        self.assertIsNotNone(hold.cancelled_at)
        self.assertIsNone(hold.released_at)
        self.assertIsNone(other_hold.cancelled_at)
        self.assertEqual(self.provider_wallet.frozen_amount,5000)
        self.assertEqual(release_due_income(now=hold.release_at,limit=1),0)

    def test_withdrawn_shortfall_preserves_every_row_and_other_frozen(self):
        order = self.settled()
        self.provider_wallet.refresh_from_db()
        self.provider_wallet.balance = 3000
        self.provider_wallet.frozen_amount = 17000
        self.provider_wallet.save(update_fields=['balance','frozen_amount'])
        before = self.snapshot()
        preview = self.preview(order)
        self.assertFalse(preview['eligible'])
        self.assertEqual(preview['shortfalls'][0]['shortfall'],5000)
        response = self.refund(order,token=preview['plan_token'])
        self.assertEqual(response.data['errorCode'],'REFUND_RECOVERY_INSUFFICIENT')
        self.assertEqual(before,self.snapshot())

    def test_platform_and_inviter_shortfalls_are_atomic(self):
        inviter = make_user()
        inviter_account = get_or_create_account_for_legacy_user(inviter)
        order = self.settled(inviter=inviter)
        inviter_wallet = get_wallet(user=inviter,account=inviter_account)
        inviter_wallet.balance = 0; inviter_wallet.save(update_fields=['balance'])
        self.platform.balance = 0; self.platform.save(update_fields=['balance'])
        before = self.snapshot()
        preview = self.preview(order)
        self.assertEqual({row['wallet_id'] for row in preview['shortfalls']},{inviter_wallet.pk,self.platform.pk})
        self.assertEqual(self.refund(order,token=preview['plan_token']).data['errorCode'],'REFUND_RECOVERY_INSUFFICIENT')
        self.assertEqual(before,self.snapshot())

    def test_double_provider_and_inviter_each_original_credit_reversed(self):
        order = self.settled(double=True,inviter=make_user())
        self.ok(self.refund(order))
        self.assert_reversed(order,4)
        self.assertEqual(order.transactions.filter(tx_type='INCOME',amount__lt=0).count(),3)

    def test_same_provider_and_inviter_wallet_merges_demand_but_reverses_each_credit(self):
        order = self.settled(inviter=self.provider)
        preview = self.preview(order)
        self.assertEqual(sum(row['income_amount'] for row in preview['recoveries'] if row['wallet_id']==self.provider_wallet.pk),8000)
        self.ok(self.refund(order))
        self.assert_reversed(order,3)

    def test_earnings_deposit_safe_source_is_reversed_with_hold(self):
        profile = self.provider.escort_profile
        profile.deposit_mode,profile.deposit_required = 'EARNINGS',40000
        profile.save(update_fields=['deposit_mode','deposit_required'])
        order = self.settled(hold=72)
        preview = self.preview(order)
        own = next(row for row in preview['recoveries'] if row['wallet_id']==self.provider_wallet.pk)
        self.assertEqual((own['deposit_amount'],own['held_amount'],own['available_required']),(4800,3200,0))
        self.ok(self.refund(order))
        self.assert_reversed(order,5)
        profile.refresh_from_db(); self.provider_wallet.refresh_from_db(); self.platform.refresh_from_db()
        self.assertEqual((profile.deposit_paid,self.provider_wallet.balance,self.provider_wallet.frozen_amount,self.platform.balance),(0,0,0,0))

    def test_ambiguous_returned_deposit_uses_only_available_and_preserves_new_deposit(self):
        profile = self.provider.escort_profile
        profile.deposit_mode,profile.deposit_required = 'EARNINGS',40000
        profile.save(update_fields=['deposit_mode','deposit_required'])
        order = self.settled(hold=72)
        # A later historical deposit return means even a replenished profile
        # cannot prove that the original order's deposit remains in the pool.
        Transaction.objects.create(wallet=self.provider_wallet,tx_type='DEPOSIT',amount=4800)
        self.provider_wallet.refresh_from_db(); self.provider_wallet.balance = 4800
        self.provider_wallet.save(update_fields=['balance'])
        profile.refresh_from_db(); profile.deposit_paid = 6000
        profile.save(update_fields=['deposit_paid'])
        own = next(row for row in self.preview(order)['recoveries'] if row['wallet_id']==self.provider_wallet.pk)
        self.assertEqual((own['source_safe'],own['deposit_amount'],own['available_required']),(False,0,4800))
        self.ok(self.refund(order))
        profile.refresh_from_db()
        self.assertEqual(profile.deposit_paid,6000)
        self.assertFalse(order.transactions.filter(tx_type='DEPOSIT',reversal_of__isnull=False).exists())

    def test_stale_forged_and_missing_plan_do_not_mutate_money(self):
        order = self.settled()
        token = self.preview(order)['plan_token']
        self.provider_wallet.refresh_from_db(); self.provider_wallet.balance += 1
        self.provider_wallet.save(update_fields=['balance'])
        before = self.snapshot()
        for value in (token,'forged',''):
            response = self.admin.post(f'/api/admin/orders/{order.pk}/refund/',{'reason':'测试过期计划','plan_token':value},format='json')
            self.assertEqual(response.data['errorCode'],'REFUND_PLAN_CHANGED')
            self.assertEqual(before,self.snapshot())

    def test_settled_confirmed_order_can_reopen_resolved_aftersale_with_history(self):
        order = self.settled(confirmed=True)
        self.assertEqual(completed_consumption(self.boss_account),10000)
        case = AfterSale.objects.create(order=order,account=self.boss_account,status='RESOLVED',reason='原申请',resolution='原处理',resolved_at=self.now)
        payload = {'reason':'重新申请已结算售后','client_request_id':'settled-reopen-1'}
        self.ok(self.customer.post(f'/api/orders/orders/{order.pk}/aftersale/',payload,format='json'))
        case.refresh_from_db()
        self.assertEqual(case.status,'OPEN')
        self.assertEqual(case.history[0]['resolution'],'原处理')
        self.ok(self.refund(order,aftersale=case))
        confirmation = ConsumptionConfirmation.objects.get(order=order)
        self.assertIsNotNone(confirmation.reversed_at)
        self.assertEqual(completed_consumption(self.boss_account),0)
        self.assertEqual(MemberProfile.objects.get(account=self.boss_account).points_balance,0)
        self.assertFalse(order.point_allocations.filter(released=False).exists())
        self.ok(self.customer.post(f'/api/orders/orders/{order.pk}/aftersale/',payload,format='json'))
        case.refresh_from_db(); self.assertEqual(case.status,'REFUNDED')

    def test_permission_and_ordinary_transitions_cannot_recover_settled_money(self):
        order = self.settled()
        for client in (self.customer,self.client_for(self.provider_account)):
            self.assertEqual(client.get(f'/api/admin/orders/{order.pk}/refund-preview/').status_code,403)
            self.assertNotEqual(client.post(f'/api/orders/orders/{order.pk}/cancel/').data['code'],0)
        with transaction.atomic():
            with self.assertRaises(IllegalTransitionError):
                transition(order.pk,'CANCELLED',operator=self.boss,operator_account=self.boss_account,
                           action='REFUND',allow_settled_refund=True)
        self.assertFalse(OrderRefund.objects.filter(order=order).exists())

    def test_late_refund_failure_rolls_back_recoveries_and_notifications(self):
        order = self.settled(hold=72)
        token = self.preview(order)['plan_token']; before = self.snapshot()
        with patch('orders.services.notify_wallet_update') as wallet_notify,patch('console.views.notify_order_update') as order_notify:
            with patch('engagement.refunds.reverse_refund_benefits',side_effect=RuntimeError('late refund failure')):
                with self.assertRaisesRegex(RuntimeError,'late refund failure'):
                    self.refund(order,token=token)
            wallet_notify.assert_not_called();order_notify.assert_not_called()
        self.assertEqual(before,self.snapshot())

    def test_refund_notifications_publish_each_touched_wallet_once_after_commit(self):
        order = self.settled(hold=72)
        with patch('orders.services.notify_wallet_update') as wallet_notify,patch('console.views.notify_order_update') as order_notify:
            with self.captureOnCommitCallbacks(execute=True):
                self.ok(self.refund(order))
                wallet_notify.assert_not_called();order_notify.assert_not_called()
            self.assertEqual({call.args[0].pk for call in wallet_notify.call_args_list},
                             {self.provider_wallet.pk,self.boss_wallet.pk,self.platform.pk})
            self.assertEqual(wallet_notify.call_count,3);order_notify.assert_called_once()

    def test_released_hold_uses_available_without_a_second_release(self):
        order = self.settled(hold=72)
        hold = IncomeHold.objects.get(income_transaction__order=order)
        self.assertEqual(release_due_income(now=hold.release_at,limit=1),1)
        self.ok(self.refund(order))
        self.assert_reversed(order,2)
        self.assertEqual(release_due_income(now=hold.release_at,limit=1),0)
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(0,0))

    def test_platform_cannot_fund_commission_recovery_using_other_deposit_pool(self):
        order = self.settled()
        self.platform.refresh_from_db()
        self.platform.balance = 5000
        self.platform.save(update_fields=['balance'])
        Transaction.objects.create(wallet=self.platform,tx_type='DEPOSIT_INCOME',amount=5000)
        preview = self.preview(order)
        own = next(row for row in preview['recoveries'] if row['role']=='PLATFORM')
        self.assertEqual((own['protected_deposit_amount'],own['shortfall']),(5000,2000))
        before = self.snapshot()
        self.assertEqual(self.refund(order,token=preview['plan_token']).data['errorCode'],'REFUND_RECOVERY_INSUFFICIENT')
        self.assertEqual(before,self.snapshot())

    def test_confirmed_refund_downgrades_vip_and_restores_only_requalified_unused_rights(self):
        from coupons.models import UserCoupon
        from engagement.models import MemberTag
        custom = RewardItem.objects.create(code='custom-refund',name='自定义权益',kind='SERVICE',in_shop=False)
        birthday = RewardItem.objects.create(code='birthday-refund',name='生日礼品',kind='PHYSICAL',in_shop=False,
                                             vip_quota_kind='BIRTHDAY_GIFT',is_active=True)
        low = VipTier.objects.create(level=1,boss_type=make_boss_type(),recharge_threshold=1000,
                                      upgrade_rewards=[{'reward':custom.pk,'quantity':1}])
        high = VipTier.objects.create(level=4,boss_type=make_boss_type(),recharge_threshold=10000,
                                       upgrade_rewards=[{'reward':custom.pk,'quantity':3}])
        profile,_ = MemberProfile.objects.get_or_create(account=self.boss_account)
        profile.birthday = timezone.localdate()
        profile.save(update_fields=['birthday'])
        first = self.settled(amount=2000,confirmed=True)
        order = self.settled(confirmed=True)
        prefix = f'vip-upgrade:{self.boss_account.pk}:1:4:'
        coupons = list(UserCoupon.objects.filter(account=self.boss_account,coupon__grant_key__startswith=prefix).order_by('pk'))
        coupons[0].status,coupons[0].order = 'USED',first
        coupons[0].save(update_fields=['status','order'])
        birthday_claim = RewardClaim.objects.get(account=self.boss_account,reward=birthday)
        self.ok(self.refund(order))
        record = OrderRefund.objects.get(order=order)
        self.boss_account.refresh_from_db(); profile.refresh_from_db();birthday_claim.refresh_from_db()
        self.assertEqual((self.boss_account.boss_type_id,profile.points_balance),(low.boss_type_id,4000))
        self.assertEqual(birthday_claim.status,'CANCELLED')
        self.assertIn(coupons[0].pk,record.benefit_effects['consumed_coupon_ids'])
        coupons[1].refresh_from_db();self.assertEqual(coupons[1].status,'EXPIRED')
        custom_claims = list(RewardClaim.objects.filter(account=self.boss_account,reward=custom).order_by('pk'))
        self.assertEqual([row.status for row in custom_claims],['PENDING','CANCELLED','CANCELLED'])
        self.assertEqual(ConsumptionConfirmation.objects.get(order=order).reversed_by_refund_id,record.pk)
        self.assertEqual(completed_consumption(self.boss_account),2000)
        count = RewardClaim.objects.filter(account=self.boss_account).count()
        self.settled(confirmed=True)
        coupons[0].refresh_from_db();coupons[1].refresh_from_db();birthday_claim.refresh_from_db()
        self.assertEqual((coupons[0].status,coupons[1].status,birthday_claim.status),('USED','UNUSED','PENDING'))
        self.assertEqual(RewardClaim.objects.filter(account=self.boss_account).count(),count)

    def test_refund_summary_hides_other_recipients_from_boss_and_provider(self):
        order = self.settled(double=True,inviter=make_user())
        admin_result = self.ok(self.refund(order))['refund_summary']
        self.assertEqual(len(admin_result['recovery_lines']),4)
        from orders.serializers import OrderSerializer
        from types import SimpleNamespace
        boss = OrderSerializer(order,context={'request':SimpleNamespace(account=self.boss_account,legacy_user=self.boss)}).data['refund_summary']
        provider = OrderSerializer(order,context={'request':SimpleNamespace(account=self.provider_account,legacy_user=self.provider)}).data['refund_summary']
        self.assertNotIn('recovery_lines',boss);self.assertNotIn('recovery_lines',provider)
        self.assertNotIn('recovered_amount',boss)
        self.assertEqual(provider['recovered_amount'],3750)

    def test_edited_amount_or_missing_credit_cannot_be_refunded_even_with_new_preview(self):
        order = self.settled()
        token = self.preview(order)['plan_token']
        Order.objects.filter(pk=order.pk).update(amount=12000)
        before = self.snapshot()
        response = self.admin.post(f'/api/admin/orders/{order.pk}/refund/',{'reason':'拒绝篡改金额','plan_token':token},format='json')
        self.assertEqual(response.data['errorCode'],'REFUND_LEDGER_INCOMPLETE')
        self.assertEqual(before,self.snapshot())
        Order.objects.filter(pk=order.pk).update(amount=10000)
        Transaction.objects.filter(order=order,tx_type='SHOP_INCOME').delete()
        response = self.admin.get(f'/api/admin/orders/{order.pk}/refund-preview/')
        self.assertEqual(response.data['errorCode'],'REFUND_LEDGER_INCOMPLETE')

    def test_legacy_gift_refund_reverses_gift_credit_and_only_its_naming_extension(self):
        from orders.models import ServiceCategory
        from engagement.models import GiftBenefitRule, GiftNamingGrant, ProviderNaming
        category = ServiceCategory.objects.create(name='隔离礼物退款',is_gift=True)
        gift = make_service(price=2100,commission_rate=20,service_category=category,gift_naming_days=2)
        reward = RewardItem.objects.create(code='refund-gift-right',name='赠礼权益',kind='SERVICE',in_shop=False)
        GiftBenefitRule.objects.create(name='隔离赠礼权益',reward=reward,category=category,threshold=1,enabled=True)
        profile = self.provider.escort_profile
        profile.completed_order_count = 5
        profile.save(update_fields=['completed_order_count'])
        orders=[]
        for index in (1,2):
            result = self.ok(self.customer.post('/api/orders/gifts/send/',{'gift_service_id':gift.pk,'quantity':1,
                'provider_id':self.provider_account.pk,'naming_title':f'隔离冠名{index}',
                'expected_amount':2100,'client_request_id':f'legacy-gift-refund-{index}'},format='json'))
            orders.append(Order.objects.get(pk=result['id']))
        first,second = orders
        second_grant = GiftNamingGrant.objects.get(order=second)
        self.ok(self.refund(first))
        first.refresh_from_db();profile.refresh_from_db()
        self.assertEqual((first.status,first.payment_status,profile.completed_order_count),('CANCELLED','REFUNDED',5))
        self.assertEqual(first.transactions.filter(wallet=self.provider_wallet,tx_type='GIFT').aggregate(v=Sum('amount'))['v'],0)
        self.assertEqual(OrderRefund.objects.get(order=first).recovery_lines.count(),2)
        self.assertEqual(RewardClaim.objects.get(source_order=first).status,'CANCELLED')
        self.assertEqual(RewardClaim.objects.get(source_order=second).status,'PENDING')
        naming = ProviderNaming.objects.get()
        self.assertEqual(naming.title,'隔离冠名2')
        self.assertAlmostEqual((naming.expires_at-second_grant.created_at).total_seconds(),2*86400,delta=1)

    def test_refund_of_earlier_gift_rechecks_later_cumulative_claims(self):
        from orders.models import ServiceCategory
        from engagement.models import GiftBenefitRule
        category = ServiceCategory.objects.create(name='累计赠礼退款',is_gift=True)
        gift = make_service(price=2000,commission_rate=20,service_category=category)
        pending = RewardItem.objects.create(code='cumulative-refund-pending',name='未交付累计权益',kind='SERVICE',in_shop=False)
        delivered = RewardItem.objects.create(code='cumulative-refund-delivered',name='已交付累计权益',kind='PHYSICAL',in_shop=False)
        for reward in (pending,delivered):
            GiftBenefitRule.objects.create(name=reward.name,reward=reward,category=category,
                threshold=3000,cumulative=True,once_per_account=True,enabled=True)
        gifts=[]
        for index in (1,2):
            result = self.ok(self.customer.post('/api/orders/gifts/send/',{'gift_service_id':gift.pk,'quantity':1,
                'provider_id':self.provider_account.pk,'expected_amount':2000,
                'client_request_id':f'cumulative-gift-refund-{index}'},format='json'))
            gifts.append(Order.objects.get(pk=result['id']))
        first,second = gifts
        claim = RewardClaim.objects.get(reward=pending)
        completed = RewardClaim.objects.get(reward=delivered)
        self.assertEqual((claim.source_order_id,completed.source_order_id),(second.pk,second.pk))
        completed.status = 'COMPLETED'
        completed.completed_at = timezone.now()
        completed.save(update_fields=['status','completed_at'])
        self.ok(self.refund(first))
        claim.refresh_from_db();completed.refresh_from_db();self.provider_wallet.refresh_from_db()
        effects = OrderRefund.objects.get(order=first).benefit_effects
        self.assertEqual((claim.status,completed.status),('CANCELLED','COMPLETED'))
        self.assertIn(claim.pk,effects['cancelled_claim_ids'])
        self.assertIn(completed.pk,effects['delivered_claim_ids'])
        self.assertEqual(self.provider_wallet.balance,1600)
