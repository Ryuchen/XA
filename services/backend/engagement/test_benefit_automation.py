from datetime import timedelta
from unittest.mock import patch
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.exceptions import ValidationError
from orders.tests.factories import make_user, make_provider, make_wallet, make_service, make_service_category, make_superuser
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from orders.models import Order, ExperienceUse, AfterSale, ServiceItem
from coupons.models import Coupon, UserCoupon
from coupons.claims import claim
from coupons.benefits import grant_periodic, issue, restore_order_coupons
from orders.purchase_benefits import prepare_purchase, lock_purchase, record_paid_benefits
from wallet.models import ProviderReport
from users.models import BossType
from .models import VipTier, ConsumptionConfirmation, RewardItem, RewardClaim, MemberProfile, MemberBenefitGrant, RetirementAudit, MemberTag
from .services import completed_consumption, update_vip
from .confirmation import process_order, refresh_report_clock
from .retirement import retire


class BenefitAutomationTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        link_legacy_relations(self.boss, self.account)
        self.wallet = make_wallet(self.boss, balance=5000000)
        self.provider = make_provider()
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        link_legacy_relations(self.provider, self.provider_account)
        self.staff = get_or_create_account_for_legacy_user(make_superuser())
        self.client.force_authenticate(self.boss)
        self.now = timezone.now()

    def service(self, kind='', platform='PC', price=18800, **kwargs):
        return make_service(experience_kind=kind, platform=platform, price=price, **kwargs)

    def coupon(self, service, kind='', **kwargs):
        return Coupon.objects.create(name='资格券', discount_type='QUALIFICATION', amount=0,
            service_ids=[service.pk], limit_units=1, experience_kind=kind,
            platform=service.platform if kind == 'DAILY_VALUE' else '', **kwargs)

    def buy(self, service, **kwargs):
        response = self.client.post('/api/orders/create/', {'service_id':service.pk, **kwargs}, format='json')
        return response

    def completed(self, amount=300000, age=73, submitted_age=73, service=None, **kwargs):
        service = service or self.service()
        order = Order.objects.create(customer=self.boss, customer_account=self.account,
            provider=self.provider, provider_account=self.provider_account, service=service,
            amount=amount, status='COMPLETED', payment_status='PAID',
            completed_at=self.now-timedelta(hours=age), settled_at=self.now,
            settlement_mode='COMPLETION', **kwargs)
        ProviderReport.objects.create(provider=self.provider, provider_account=self.provider_account,
            order=order, game_name='测试', amount=max(1,amount), status='APPROVED',
            submitted_at=self.now-timedelta(hours=submitted_age))
        return order

    def tiers(self):
        thresholds=[300000,600000,1000000,2000000,5000000,10000000]
        rates=[98,97,96,94,92,90]
        return [VipTier.objects.create(level=i+1, boss_type=BossType.objects.create(name=f'VIP{i+1}', discount_rate=rates[i]),
            recharge_threshold=thresholds[i], companion_service_ids=[], safe_box_service_ids=[])
            for i in range(6)]

    def test_new_first_and_value_independent_of_other_consumption(self):
        self.completed()
        first=self.service('NEW_FIRST'); value=self.service('NEW_VALUE')
        c=self.coupon(value,'NEW_VALUE'); claim(self.account,self.boss,c.pk)
        self.assertEqual(self.buy(first).data['code'],0)
        self.assertEqual(self.buy(value).data['code'],0)
        self.assertEqual(ExperienceUse.objects.count(),2)
        self.assertNotEqual(self.buy(first).data.get('code',400),0)
        self.assertNotEqual(self.buy(value).data.get('code',400),0)

    def test_new_first_shared_across_platforms(self):
        self.assertEqual(self.buy(self.service('NEW_FIRST','PC')).data['code'],0)
        self.assertNotEqual(self.buy(self.service('NEW_FIRST','MOBILE')).data.get('code',400),0)

    def test_daily_four_independent_quotas(self):
        for platform in ('PC','MOBILE'):
            daily=self.service('DAILY',platform); value=self.service('DAILY_VALUE',platform)
            claim(self.account,self.boss,self.coupon(value,'DAILY_VALUE').pk)
            self.assertEqual(self.buy(daily).data['code'],0)
            self.assertEqual(self.buy(value).data['code'],0)
            self.assertNotEqual(self.buy(daily,game_uid='another').data.get('code',400),0)
            self.assertNotEqual(self.buy(value).data.get('code',400),0)
        self.assertEqual(ExperienceUse.objects.count(),4)

    def test_daily_claim_same_family_cannot_be_cloned(self):
        s=self.service('DAILY_VALUE'); c=self.coupon(s,'DAILY_VALUE')
        claim(self.account,self.boss,c.pk)
        with self.assertRaises(ValidationError): claim(self.account,self.boss,self.coupon(s,'DAILY_VALUE').pk)

    def test_daily_claim_next_beijing_day_and_expiry(self):
        s=self.service('DAILY_VALUE'); c=self.coupon(s,'DAILY_VALUE')
        old=claim(self.account,self.boss,c.pk)
        self.assertEqual(old.expires_at.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).hour,0)
        with patch('django.utils.timezone.now',return_value=self.now+timedelta(days=1)):
            new=claim(self.account,self.boss,c.pk)
            self.assertNotEqual(old.pk,new.pk)
            self.assertFalse(old.is_usable)
            self.assertTrue(new.is_usable)

    def test_new_value_permanent_and_shared_platform(self):
        s=self.service('NEW_VALUE'); mobile=self.service('NEW_VALUE','MOBILE'); c=self.coupon(s,'NEW_VALUE')
        c.service_ids.append(mobile.pk); c.save()
        row=claim(self.account,self.boss,c.pk)
        self.assertIsNone(row.valid_to)
        self.assertEqual(self.buy(mobile).data['code'],0)
        self.assertNotEqual(self.buy(s).data.get('code',400),0)

    def test_unclaimed_hidden_list_detail_and_quote(self):
        s=self.service('NEW_VALUE'); c=self.coupon(s,'NEW_VALUE')
        self.assertNotIn(s.pk,[row['id'] for row in self.client.get('/api/orders/services/').data['data']])
        self.assertEqual(self.client.get(f'/api/orders/services/{s.pk}/').data['code'],404)
        self.assertNotEqual(self.buy(s).data.get('code',400),0)
        claim(self.account,self.boss,c.pk)
        self.assertIn(s.pk,[row['id'] for row in self.client.get('/api/orders/services/').data['data']])

    def test_refund_preserves_paid_daily_quota_and_used_qualification(self):
        s=self.service('DAILY_VALUE'); c=self.coupon(s,'DAILY_VALUE'); row=claim(self.account,self.boss,c.pk)
        r=self.buy(s); self.assertEqual(r.data['code'],0)
        order=Order.objects.latest('pk')
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/cancel/',{},format='json').data['code'],0)
        row.refresh_from_db(); self.assertEqual(row.status,'USED')
        self.assertNotEqual(self.buy(s).data.get('code',400),0)

    def test_daily_normal_refund_does_not_restore_quota(self):
        s=self.service('DAILY'); self.assertEqual(self.buy(s).data['code'],0); order=Order.objects.latest('pk')
        self.client.post(f'/api/orders/orders/{order.pk}/cancel/',{},format='json')
        self.assertNotEqual(self.buy(s).data.get('code',400),0)

    def test_payment_failure_and_rollback_do_not_take_quota(self):
        s=self.service('DAILY'); self.wallet.balance=0; self.wallet.save()
        self.assertNotEqual(self.buy(s).data['code'],0); self.assertFalse(ExperienceUse.objects.exists())
        self.wallet.balance=500000; self.wallet.save()
        with patch('orders.views.log_only',side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError): self.buy(s)
        self.assertFalse(ExperienceUse.objects.exists())
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance,500000)

    def test_experience_no_discounts_and_single_unit(self):
        tiers=self.tiers(); self.account.boss_type=tiers[5].boss_type; self.account.save()
        s=self.service('DAILY')
        from orders.tests.factories import make_promotion
        make_promotion(discount_rate=50)
        p, promotion, uc=prepare_purchase(self.account,s)
        self.assertEqual(p.amount,s.price); self.assertIsNone(promotion)
        with self.assertRaises(ValidationError): prepare_purchase(self.account,s,2)
        row=issue(self.account,'ordinary',name='折扣券',rate=85,max_discount=10000)[0]
        with self.assertRaises(ValidationError): prepare_purchase(self.account,s,1,row.pk)

    def test_monthly_cap_compare_vip_and_no_consumption_when_worse(self):
        tiers=self.tiers(); self.account.boss_type=tiers[5].boss_type; self.account.save(); grant_periodic(self.account)
        rows=UserCoupon.objects.filter(coupon__grant_key__startswith=f'vip-month:{self.account.pk}:1:')
        self.assertEqual(rows.count(),10)
        row=rows.first(); s=self.service(price=200000)
        pricing,_,uc=prepare_purchase(self.account,s,1,row.pk)
        self.assertEqual(pricing.amount,180000); self.assertIsNone(uc)
        s.price=50000; s.save(); pricing,_,uc=prepare_purchase(self.account,s,1,row.pk)
        self.assertEqual(pricing.coupon_discount,7500); self.assertIsNotNone(uc)
        self.account.boss_type=tiers[1].boss_type; self.account.save()
        pricing,_,_=prepare_purchase(self.account,self.service(price=200000),1,row.pk)
        self.assertEqual(pricing.coupon_discount,10000)

    def test_month_mid_upgrade_topup_and_refund_original_expiry(self):
        tiers=self.tiers(); self.account.boss_type=tiers[1].boss_type; self.account.save(); grant_periodic(self.account)
        row=UserCoupon.objects.filter(coupon__grant_key__startswith='vip-month:').first(); expiry=row.valid_to
        self.account.boss_type=tiers[5].boss_type; self.account.save(); grant_periodic(self.account); grant_periodic(self.account)
        self.assertEqual(UserCoupon.objects.filter(coupon__grant_key__startswith='vip-month:').count(),10)
        row.refresh_from_db(); self.assertEqual(row.coupon.discount_rate,85)
        o=self.completed(); row.status='USED'; row.order=o; row.save()
        restore_order_coupons(o); row.refresh_from_db(); self.assertEqual(row.valid_to,expiry)

    def test_no_upgrade_before_72h_last_report_or_approval(self):
        tiers=self.tiers(); order=self.completed(submitted_age=71)
        self.assertFalse(process_order(order.pk)); self.account.refresh_from_db(); self.assertIsNone(self.account.boss_type)
        report=order.provider_reports.get(); report.submitted_at=self.now-timedelta(hours=74); report.status='PENDING'; report.save()
        self.assertFalse(process_order(order.pk))
        report.status='APPROVED'; report.save(); self.assertTrue(process_order(order.pk))
        self.account.refresh_from_db(); self.assertEqual(self.account.boss_type_id,tiers[0].boss_type_id)
        self.assertFalse(process_order(order.pk)); self.assertEqual(ConsumptionConfirmation.objects.count(),1)
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='COMPANION').count(),2)
        self.assertIsNone(UserCoupon.objects.filter(coupon__scope='COMPANION').first().valid_to)

    def test_only_confirmed_not_pending_consumption_counts(self):
        tiers=self.tiers(); mature=self.completed(amount=200000); pending=self.completed(amount=200000,submitted_age=1)
        process_order(mature.pk); process_order(pending.pk)
        self.assertEqual(completed_consumption(self.account),200000)
        self.account.refresh_from_db(); self.assertIsNone(self.account.boss_type)

    def test_gift_refund_and_old_cycle_excluded(self):
        self.tiers(); gift=self.service(service_category=make_service_category(is_gift=True))
        for order in (self.completed(service=gift),self.completed()):
            if not order.is_gift_snapshot: order.payment_status='REFUNDED'; order.save()
            self.assertFalse(process_order(order.pk))
        old=self.completed(); Order.objects.filter(pk=old.pk).update(business_cycle=0)
        self.assertFalse(process_order(old.pk)); self.assertEqual(completed_consumption(self.account),0)

    def test_multiple_reports_last_submission_and_rejection_restarts_clock(self):
        order=self.completed(); provider=make_provider()
        from orders.models import OrderProvider
        OrderProvider.objects.create(order=order,provider=self.provider,provider_account=self.provider_account,settlement_base=100)
        OrderProvider.objects.create(order=order,provider=provider,settlement_base=100)
        other=ProviderReport.objects.create(order=order,provider=provider,amount=100,game_name='test',status='PENDING',submitted_at=self.now-timedelta(hours=1))
        with transaction.atomic(): refresh_report_clock(order)
        self.assertGreater(order.consumption_ready_at,self.now)
        other.status='REJECTED'; other.save()
        with transaction.atomic(): refresh_report_clock(order)
        self.assertIsNone(order.consumption_ready_at)
        other.status='APPROVED'; other.submitted_at=self.now; other.save()
        with transaction.atomic(): refresh_report_clock(order)
        self.assertGreater(order.consumption_ready_at,self.now+timedelta(hours=71))

    def test_open_aftersale_blocks_confirmation_after_deadline(self):
        self.tiers(); order=self.completed()
        AfterSale.objects.create(order=order,account=self.account,reason='窗口内客诉')
        self.assertFalse(process_order(order.pk)); self.assertEqual(completed_consumption(self.account),0)
        order.aftersales.update(status='RESOLVED',resolution='已处理')
        self.assertTrue(process_order(order.pk))

    def test_birthday_yearly_only_today_capped_no_automatic_discount(self):
        tiers=self.tiers(); self.account.boss_type=tiers[3].boss_type; self.account.save()
        MemberProfile.objects.create(account=self.account,birthday=timezone.localdate())
        grant_periodic(self.account); grant_periodic(self.account)
        rows=UserCoupon.objects.filter(coupon__grant_key__startswith='vip-birthday:')
        self.assertEqual(rows.count(),1); row=rows.get()
        self.assertEqual(row.coupon.max_discount,10000)
        from coupons.benefits import effective_boss_rate
        self.assertEqual(effective_boss_rate(self.account),94)
        self.assertEqual(prepare_purchase(self.account,self.service(price=100000),1,row.pk)[0].amount,90000)
        with patch('django.utils.timezone.now',return_value=self.now+timedelta(days=1)):
            self.assertFalse(row.is_usable)

    def test_safety_one_permanent_low_tier_upgrade_no_reissue(self):
        tiers=self.tiers(); self.account.boss_type=tiers[0].boss_type; self.account.save(); grant_periodic(self.account)
        row=UserCoupon.objects.get(coupon__scope='SAFE_BOX'); self.assertIsNone(row.valid_to)
        self.account.boss_type=tiers[4].boss_type; self.account.save(); grant_periodic(self.account)
        row.refresh_from_db(); self.assertEqual(row.coupon.discount_rate,60)
        row.status='USED'; row.save(); grant_periodic(self.account)
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='SAFE_BOX').count(),1)

    def test_safety6_replenish_after_confirmation_once_refund_original(self):
        tiers=self.tiers(); self.account.boss_type=tiers[5].boss_type; self.account.save(); grant_periodic(self.account)
        row=UserCoupon.objects.get(coupon__scope='SAFE_BOX'); order=self.completed(amount=0,submitted_age=1)
        row.status='USED'; row.order=order; row.save(); grant_periodic(self.account)
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='SAFE_BOX').count(),1)
        self.assertFalse(process_order(order.pk))
        restore_order_coupons(order); grant_periodic(self.account)
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='SAFE_BOX',status='UNUSED').count(),1)
        row.refresh_from_db(); row.status='USED'; row.order=order; row.save()
        order.provider_reports.update(submitted_at=self.now-timedelta(hours=74))
        process_order(order.pk); grant_periodic(self.account); grant_periodic(self.account)
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='SAFE_BOX',status='UNUSED').count(),1)
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='SAFE_BOX').count(),2)

    def test_companion_stack_and_other_entitlements_topup(self):
        tiers=self.tiers(); tag=RewardItem.objects.create(code='tag',name='TAG',kind='TAG',in_shop=False)
        naming=RewardItem.objects.create(code='naming',name='冠名',kind='NAMING',in_shop=False,duration_days=7)
        friend=RewardItem.objects.create(code='friend',name='好友位',kind='SERVICE',vip_quota_kind='FRIEND',in_shop=False)
        for tier in tiers:
            tier.upgrade_rewards=[{'reward':r.pk,'quantity':1} for r in (tag,naming,friend)]; tier.save()
        total=0
        for i,target in enumerate([300000,600000,1000000,2000000,5000000,10000000]):
            o=self.completed(amount=target-total); total=target; process_order(o.pk)
            self.assertEqual(UserCoupon.objects.filter(coupon__scope='COMPANION').count(),[2,4,6,8,11,15][i])
            self.assertEqual(RewardClaim.objects.filter(kind_snapshot='TAG').count(),[1,1,2,2,3,5][i])
            self.assertEqual(RewardClaim.objects.filter(kind_snapshot='NAMING').count(),[3,6,6,6,10,12][i])
            self.assertEqual(RewardClaim.objects.filter(reward=friend).count(),[0,0,1,2,5,10][i])
        self.assertEqual(RewardClaim.objects.filter(kind_snapshot='TAG',highlighted=True).count(),5)
        self.assertTrue(all(c.duration_months==1 for c in RewardClaim.objects.filter(kind_snapshot='NAMING')))
        update_vip(self.account); self.assertEqual(MemberBenefitGrant.objects.count(),6)

    def test_retirement_blocks_inflight_money_and_complaints(self):
        with self.assertRaises(ValidationError): retire(self.account,self.staff,'退店清空')
        self.wallet.balance=0; self.wallet.save(); pending=Order.objects.create(customer=self.boss,customer_account=self.account,service=self.service(),amount=100)
        with self.assertRaises(ValidationError): retire(self.account,self.staff,'退店清空')
        pending.status='CANCELLED'; pending.save(); order=self.completed()
        AfterSale.objects.create(order=order,account=self.account,reason='未结客诉')
        with self.assertRaises(ValidationError): retire(self.account,self.staff,'退店清空')

    def test_retirement_new_cycle_no_old_task_or_grant_dedupe(self):
        self.tiers(); old=self.completed(); process_order(old.pk)
        self.wallet.balance=0; self.wallet.save(); retire(self.account,self.staff,'退店清空')
        self.assertEqual(self.account.business_cycle,2); self.assertIsNone(self.account.boss_type)
        self.assertEqual(completed_consumption(self.account),0); self.assertEqual(RetirementAudit.objects.count(),1)
        self.assertFalse(process_order(old.pk))
        self.assertFalse(UserCoupon.objects.filter(business_cycle=1,status='UNUSED').exists())
        fresh=self.completed(); process_order(fresh.pk)
        self.assertEqual(UserCoupon.objects.filter(business_cycle=2,coupon__scope='COMPANION').count(),2)

    def test_strict_service_binding_not_name_guess(self):
        self.tiers(); fake=self.service(name='33安全箱',price=10000)
        row=issue(self.account,'safety',name='33安全箱',rate=0,scope='SAFE_BOX',service_ids=[fake.pk])[0]
        with self.assertRaises(ValidationError): prepare_purchase(self.account,fake,1,row.pk)
        fake.benefit_kind='SAFEBOX_33'; fake.save()
        self.assertEqual(prepare_purchase(self.account,fake,1,row.pk)[0].amount,0)

    def test_72hour_aftersale_endpoint_boundary(self):
        order=self.completed(age=1)
        r=self.client.post(f'/api/orders/orders/{order.pk}/aftersale/',{'reason':'订单体验有问题'},format='json')
        self.assertEqual(r.data['code'],0)
        expired=self.completed(age=73)
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{expired.pk}/aftersale/',{'reason':'超时客诉'},format='json').data.get('code',400),0)

    def test_discount_coupons_follow_service_setting_for_all_types(self):
        row=issue(self.account,'rate',name='九折券',rate=90)[0]
        for service_type in ('REGULAR','ACTIVITY_LIMITED','PROMOTIONAL','COMPANION'):
            service=self.service(service_type=service_type,allows_discount_coupons=False)
            with self.assertRaises(ValidationError): prepare_purchase(self.account,service,1,row.pk)
            service.allows_discount_coupons=True; service.save()
            self.assertEqual(prepare_purchase(self.account,service,1,row.pk)[0].coupon_discount,1880)

    def test_free_companion_hour_voucher_keeps_bound_redemption(self):
        service=self.service(commission_policy='COMPANION',benefit_kind='COMPANION_HOUR',price_unit='小时')
        row=issue(self.account,'free-hour',name='陪玩一小时',rate=0,scope='COMPANION',limit_units=1,service_ids=[service.pk])[0]
        self.assertEqual(prepare_purchase(self.account,service,1,row.pk)[0].amount,0)

    def test_service_discount_opt_in_is_rechecked_at_payment(self):
        from orders.purchase_benefits import lock_purchase
        row=issue(self.account,"service-switch",name="九折券",rate=90)[0]
        service=self.service(allows_discount_coupons=False)
        with self.assertRaises(ValidationError): prepare_purchase(self.account,service,1,row.pk)
        service.allows_discount_coupons=True; service.save()
        pricing,promotion,coupon=prepare_purchase(self.account,service,1,row.pk)
        ServiceItem.objects.filter(pk=service.pk).update(allows_discount_coupons=False)
        with self.assertRaises(ValidationError): lock_purchase(self.account,service,1,coupon,pricing)

    def test_unclaimed_value_is_hidden_in_other_package_variants(self):
        regular=self.service(catalog_family='experience-family')
        hidden=self.service('NEW_VALUE',catalog_family='experience-family')
        coupon=self.coupon(hidden,'NEW_VALUE')
        path=f'/api/orders/services/{regular.pk}/'
        self.assertEqual([row['id'] for row in self.client.get(path).data['data']['variants']],[regular.pk])
        claim(self.account,self.boss,coupon.pk)
        self.assertEqual([row['id'] for row in self.client.get(path).data['data']['variants']],[regular.pk,hidden.pk])

    def test_gift_report_settles_after_window_without_counting_vip(self):
        from .tasks import confirm_due_consumption
        from orders.models import OrderProvider
        gift=self.service(service_category=make_service_category(is_gift=True))
        order=self.completed(service=gift)
        order.settlement_mode='REPORT_APPROVAL';order.settled_at=None;order.save()
        OrderProvider.objects.create(order=order,provider=self.provider,provider_account=self.provider_account,
            settlement_base=order.amount,provider_income=order.amount,commission_rate=0)
        refresh_report_clock(order)
        self.assertEqual(confirm_due_consumption(),0)
        order.refresh_from_db();self.assertIsNotNone(order.settled_at)
        self.assertEqual(ConsumptionConfirmation.objects.count(),0)
        self.assertEqual(completed_consumption(self.account),0)
