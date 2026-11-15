from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.models import (LotteryCampaign, LotteryPrize, MemberProfile, PointFundingLot,
                               PointLedger, RechargePackage, RewardItem, VipTier)
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import Transaction
from wallet.services import get_wallet


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(BOSS_FUNDING_SELECTION_ENABLED=False, REQUIRE_RECHARGE_PROOF=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=False, LEGACY_FORCE_AUTH_COMPAT=False,
                   ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class PrincipalOnlyPointsMultistoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_actual_gift_sources_do_not_earn_points_in_either_store(self):
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                operation = config()
                operation.vip_enabled = operation.points_enabled = True
                operation.base_points_rate = 100; operation.save()
                boss_user = make_user()
                boss = get_or_create_account_for_legacy_user(boss_user)
                link_legacy_relations(boss_user, boss)
                tier = VipTier.objects.create(level=7, boss_type=make_boss_type(), recharge_threshold=0,
                    points_rate=150, benefits={'issuance': {'birthday_multiplier': 2}})
                boss.boss_type = tier.boss_type; boss.save(update_fields=['boss_type'])
                profile = MemberProfile.objects.create(account=boss, points_balance=10000, birthday=timezone.localdate())
                staff_user = make_console_user(['wallet:recharge', 'wallet:adjust', 'order:refund', 'order:view', 'support:edit',
                                                'report:view', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                provider_user = make_provider(deposit_mode='EARNINGS')
                provider = get_or_create_account_for_legacy_user(provider_user)
                link_legacy_relations(provider_user, provider)
                provider_user.escort_profile.account = provider; provider_user.escort_profile.save(update_fields=['account'])
                def client(account):
                    return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                owner, admin, worker = client(boss), client(staff), client(provider)
                RechargePackage.objects.create(amount=1000, gift_amount=100, active=True, points_rate=400)
                response = admin.post('/api/admin/wallets/recharge/', {'account_id': boss.pk, 'amount': 1000,
                    'gift_amount': 100, 'trade_no': 'synthetic-d18', 'client_request_id': 'synthetic-d18'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                wallet = get_wallet(account=boss, user=boss_user)
                response = admin.post(f'/api/admin/wallets/{wallet.pk}/adjust/', {'amount': 300,
                    'balance_kind': 'GIFT', 'remark': '测试赠送', 'client_request_id': 'synthetic-d18-gift'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                response = admin.post(f'/api/admin/wallets/{wallet.pk}/adjust/', {'amount': 200,
                    'balance_kind': 'COMPENSATION', 'remark': '测试赔付', 'client_request_id': 'synthetic-d18-comp'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                reward = RewardItem.objects.create(code='d18-lottery', name='抽奖赠送', kind='BALANCE',
                    audience='BOSS', in_shop=False, is_active=True, balance_amount=500, stock=1)
                campaign = LotteryCampaign.objects.create(name='抽奖验证', point_cost=10, enabled=True)
                LotteryPrize.objects.create(campaign=campaign, reward=reward, probability=10000)
                draw = owner.post('/api/club/lottery/', {'campaign_id': campaign.pk, 'version': campaign.version,
                    'client_request_id': 'synthetic-d18-lottery', 'count': 1}, format='json')
                self.assertEqual(draw.data['code'], 0, draw.data)
                wallet.refresh_from_db(); self.assertEqual(wallet.balance, 2100)
                # Full cancellation before earnings never invents a negative points entry.
                early_service = make_service(price=500)
                early = owner.post('/api/orders/create/', {'service_id': early_service.pk, 'provider_id': provider.pk,
                    'client_request_id': 'synthetic-d18-before-award'}, format='json')
                self.assertEqual(early.data['code'], 0, early.data)
                early_id = early.data['data']['id']
                early_preview = admin.get(f'/api/admin/orders/{early_id}/refund-preview/')
                self.assertEqual(early_preview.data['code'], 0, early_preview.data)
                early_refund = admin.post(f'/api/admin/orders/{early_id}/refund/', {'reason': '未实销全退',
                    'plan_token': early_preview.data['data']['plan_token'], 'client_request_id': 'synthetic-d18-early-refund'}, format='json')
                self.assertEqual(early_refund.data['code'], 0, early_refund.data)
                profile.refresh_from_db(); wallet.refresh_from_db()
                self.assertEqual((profile.points_balance, wallet.balance), (9000, 2100))
                self.assertFalse(PointLedger.objects.filter(order_id=early_id).exists())
                self.exercise_negotiated_refund(boss, provider, owner, admin, worker, wallet, tier, profile)

    @override_settings(BOSS_FUNDING_SELECTION_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
        NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
        ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
        PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
        SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
        SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
        SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=True,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=True)
    def exercise_negotiated_refund(self, boss, provider, owner, admin, worker, wallet, tier, profile):
        from engagement.confirmation import process_order
        from engagement.models import OrderFundingAllocation
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport

        service = make_service(price=2000, commission_rate=60)
        response = owner.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': provider.pk,
            'gift_balance_amount': 900, 'compensation_balance_amount': 100,
            'client_request_id': 'synthetic-d18-order'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        from django.db.models import Sum
        sources = {row['kind']: row['total'] for row in order.funding_allocations.values('kind').annotate(total=Sum('amount'))}
        self.assertEqual(sources, {'PREPAID': 1000, 'GIFT': 900, 'COMPENSATION': 100})
        self.assertEqual((order.funding_terms['points_rate'], order.funding_terms['birthday_multiplier']), (150, 2))
        self.assertTrue(PointFundingLot.objects.filter(account=boss, kind='GIFT', source_key__startswith='reward-claim:').exists())
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='GIFT').count(), 2)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='ADJUST', amount=300).count(), 1)
        tier.points_rate = 100; tier.save(update_fields=['points_rate'])
        profile.birthday = None; profile.save(update_fields=['birthday'])
        started = worker.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
        self.assertEqual(started.data['code'], 0, started.data)
        case = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
            'affected_provider_account_ids': [provider.pk], 'reason': '协商终止',
            'client_request_id': 'd18-case'}, format='json')
        self.assertEqual(case.data['code'], 0, case.data)
        case_id = case.data['data']['id']
        proposal = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
            'request_id': 'd18-proposal', 'expected_version': 0, 'refund_units': 500,
            'provider_terms': [{'account_id': provider.pk, 'retained_units': order.provider_income - 500}],
            'explanation': '双方同意部分退款'}, format='json')
        self.assertEqual(proposal.data['code'], 0, proposal.data)
        proposal_id = proposal.data['data']['id']
        for actor in (owner, worker):
            decision = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                  {'proposal_id': proposal_id, 'accepted': True}, format='json')
            self.assertEqual(decision.data['code'], 0, decision.data)
        refund = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/',
            {'proposal_id': proposal_id, 'client_request_id': 'd18-execution'}, format='json')
        self.assertEqual(refund.data['code'], 0, refund.data)
        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
        principal = OrderFundingAllocation.objects.get(order=order, kind='PREPAID')
        self.assertEqual(principal.refunded_amount, 500)
        from engagement.services import wallet_funding_parts
        wallet.refresh_from_db()
        self.assertEqual(wallet_funding_parts(wallet), {'prepaid_balance': 500, 'gift_balance': 0,
                                                       'compensation_balance': 100})
        report = ProviderReport.objects.get(order=order, provider_account=provider)
        for kind in ('COMPLETION', 'RESULT'):
            image = worker.post(f'/api/wallet/reports/{report.pk}/images/',
                                {'kind': kind, 'image': screenshot()}, format='multipart')
            self.assertEqual(image.data['code'], 0, image.data)
        self.assertEqual(worker.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
        self.assertEqual(admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data['code'], 0)
        report.refresh_from_db()
        with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            self.assertTrue(process_order(order.pk))
            self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 1500)
            late = admin.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 500})
            if late.data['code'] == 0:
                denied = admin.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '售后已过期',
                    'refund_amount': 500, 'plan_token': late.data['data']['plan_token'],
                    'client_request_id': 'd18-after-deadline'}, format='json')
                self.assertNotEqual(denied.data['code'], 0, denied.data)
            else:
                self.assertNotEqual(late.data['code'], 0)
        profile.refresh_from_db()
        self.assertEqual(profile.points_balance, 10500)

    def test_gift_referral_is_spendable_principal_in_both_stores(self):
        from orders.models import ServiceCategory
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                sender_user, inviter_user = make_user(), make_user()
                sender = get_or_create_account_for_legacy_user(sender_user)
                inviter = get_or_create_account_for_legacy_user(inviter_user)
                for user, account in ((sender_user, sender), (inviter_user, inviter)):
                    link_legacy_relations(user, account)
                sender.inviter = inviter; sender.inviter_commission_rate = 50
                sender.save(update_fields=['inviter', 'inviter_commission_rate'])
                staff_user = make_console_user(['wallet:recharge'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                provider_user = make_provider(deposit_mode='EARNINGS')
                provider = get_or_create_account_for_legacy_user(provider_user)
                link_legacy_relations(provider_user, provider)
                provider_user.escort_profile.account = provider; provider_user.escort_profile.save(update_fields=['account'])
                def client(account):
                    return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                admin, buyer, referral_boss = client(staff), client(sender), client(inviter)
                RechargePackage.objects.create(amount=2000, gift_amount=0, active=True)
                recharge = admin.post('/api/admin/wallets/recharge/', {'account_id': sender.pk, 'amount': 2000,
                    'gift_amount': 0, 'trade_no': 'gift-referral-d18', 'client_request_id': 'gift-referral-d18'}, format='json')
                self.assertEqual(recharge.data['code'], 0, recharge.data)
                category = ServiceCategory.objects.create(name='赠礼推荐验证', is_gift=True)
                gift = make_service(price=2000, commission_rate=20, service_category=category)
                sent = buyer.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'quantity': 1,
                    'provider_id': provider.pk, 'expected_amount': 2000, 'client_request_id': 'gift-referral-send-d18'}, format='json')
                self.assertEqual(sent.data['code'], 0, sent.data)
                gift_order = Order.objects.get(pk=sent.data['data']['id'])
                wallet = get_wallet(account=inviter, user=inviter_user)
                tx = Transaction.objects.get(order=gift_order, wallet=wallet, tx_type='INCOME')
                self.assertGreater(tx.amount, 0)
                lot = PointFundingLot.objects.get(account=inviter, source_key=f'referral-income:{tx.pk}')
                self.assertEqual((lot.kind, lot.remaining, wallet.balance), ('PREPAID', tx.amount, tx.amount))
                service = make_service(price=tx.amount)
                paid = referral_boss.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': provider.pk,
                    'client_request_id': 'gift-referral-spend-d18'}, format='json')
                self.assertEqual(paid.data['code'], 0, paid.data)
                order = Order.objects.get(pk=paid.data['data']['id'])
                self.assertEqual(sum(row.points_amount for row in order.point_allocations.all()), tx.amount)
                wallet.refresh_from_db(); self.assertEqual(wallet.balance, 0)
