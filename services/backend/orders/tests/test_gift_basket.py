from datetime import timedelta
from unittest.mock import patch
from django.utils import timezone
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from engagement.models import Broadcast, ConsumptionConfirmation, GiftNamingGrant, MemberBenefitGrant, PointLedger, ProviderNaming, PointFundingLot, VipTier
from engagement.services import completed_consumption, config, wallet_funding_parts
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_user, make_provider, make_service, make_wallet, make_order, make_console_user
from wallet.models import Transaction, IdempotencyKey, RechargeRecord


class GiftBasketTests(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.provider = make_provider(display_name='收礼打手', is_verified=True)
        self.account = get_or_create_account_for_legacy_user(self.boss)
        self.recipient = get_or_create_account_for_legacy_user(self.provider)
        link_legacy_relations(self.boss, self.account)
        link_legacy_relations(self.provider, self.recipient)
        self.wallet = make_wallet(self.boss, balance=100000)
        make_wallet(self.provider, balance=0)
        link_legacy_relations(self.boss, self.account)
        link_legacy_relations(self.provider, self.recipient)
        category = ServiceCategory.objects.create(name='节日礼物', is_gift=True)
        self.one = make_service(name='礼物一', price=2100, service_category=category, gift_naming_days=2, gift_festival='中秋限定')
        self.two = make_service(name='礼物二', price=5200, service_category=category, gift_naming_days=3)
        self.client.force_authenticate(self.boss)

    def payload(self, key='basket-request-001'):
        return {'items': [{'gift_service_id': self.one.pk, 'quantity': 2}, {'gift_service_id': self.two.pk, 'quantity': 1}],
            'provider_id': self.recipient.pk, 'naming_title': '老板的默契搭子', 'expected_amount': 9400, 'client_request_id': key}

    def send(self, payload=None, url='/api/orders/gifts/send/'):
        return self.client.post(url, payload or self.payload(), format='json')

    def test_multiple_gifts_repeat_quantities_and_receipt_replay(self):
        result = self.send()
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(result.data['data']['quantity'], 3)
        self.assertEqual(result.data['data']['naming_days'], 7)
        self.assertEqual(Order.objects.filter(customer_account=self.account).count(), 2)
        self.assertEqual(GiftNamingGrant.objects.count(), 2)
        naming = ProviderNaming.objects.get()
        self.assertEqual(naming.title, '老板的默契搭子')
        expiry = naming.expires_at
        replay = self.send()
        self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 90600)
        naming.refresh_from_db()
        self.assertEqual(naming.expires_at, expiry)
        self.send(self.payload('basket-request-002'))
        naming.refresh_from_db()
        self.assertEqual(naming.expires_at, expiry + timedelta(days=7))
        self.assertEqual(Broadcast.objects.filter(kind='GIFT').count(), 4)

    def test_second_line_rejection_rolls_back_every_line_and_outbox(self):
        self.one.gift_naming_days = 0
        self.one.save()
        body = self.payload(); body['naming_title'] = ''
        result = self.send(body)
        self.assertEqual(result.data['code'], 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.filter(customer_account=self.account).exists())
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet).exists())
        self.assertFalse(Broadcast.objects.exists())
        self.assertFalse(IdempotencyKey.objects.filter(account=self.account).exists())

    def test_price_change_insufficient_balance_and_expired_gift_never_debit(self):
        body = self.payload(); body['expected_amount'] = 1
        self.assertEqual(self.send(body).data['errorCode'], 'PRICE_CHANGED')
        self.wallet.balance = 9000; self.wallet.save()
        self.assertEqual(self.send().data['code'], 400)
        self.wallet.balance = 100000; self.wallet.save()
        self.one.gift_ends_at = timezone.now() - timedelta(seconds=1); self.one.save()
        self.assertEqual(self.send().data['code'], 400)
        self.assertFalse(Order.objects.filter(customer_account=self.account).exists())

    def test_gifts_use_only_principal_and_refund_returns_it(self):
        self.wallet.balance = 16000; self.wallet.save(update_fields=['balance'])
        gift = PointFundingLot.objects.create(account=self.account, source_key='principal-only-gift', remaining=3000, kind='GIFT')
        compensation = PointFundingLot.objects.create(account=self.account, source_key='principal-only-compensation', remaining=3000, kind='COMPENSATION')
        principal = PointFundingLot.objects.create(account=self.account, source_key='principal-only-prepaid', remaining=10000, kind='PREPAID')
        context = self.client.get('/api/orders/gifts/context/', {'provider_id': self.recipient.pk})
        self.assertEqual((context.data['data']['balance'], context.data['data']['prepaid_balance']), (16000, 10000))
        single = {'gift_service_id': self.one.pk, 'provider_id': self.recipient.pk, 'quantity': 1,
                  'naming_title': '测试冠名', 'expected_amount': 2100, 'client_request_id': 'principal-only-single'}
        result = self.client.post('/api/orders/gifts/send/', single, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        gift.refresh_from_db(); compensation.refresh_from_db(); principal.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((gift.remaining, compensation.remaining, principal.remaining), (3000, 3000, 7900))
        self.assertEqual(wallet_funding_parts(self.wallet), {'prepaid_balance': 7900, 'gift_balance': 3000, 'compensation_balance': 3000})
        rejected = self.send(self.payload('principal-only-basket'))
        self.assertEqual(rejected.data['code'], 400, rejected.data)
        self.assertIn('本金余额不足', rejected.data['msg'])
        self.assertEqual(Order.objects.filter(customer_account=self.account).count(), 1)
        staff = make_console_user(['order:view', 'order:refund'])
        self.client.force_authenticate(staff)
        order_id = result.data['data']['id']
        preview = self.client.get(f'/api/admin/orders/{order_id}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        refund = self.client.post(f'/api/admin/orders/{order_id}/refund/', {'reason': '本金退款核对',
            'plan_token': preview.data['data']['plan_token'], 'client_request_id': 'principal-only-refund'}, format='json')
        self.assertEqual(refund.data['code'], 0, refund.data)
        self.wallet.refresh_from_db()
        self.assertEqual(wallet_funding_parts(self.wallet), {'prepaid_balance': 10000, 'gift_balance': 3000, 'compensation_balance': 3000})

    def test_mixed_legacy_recharge_keeps_gift_unspent(self):
        self.wallet.balance = 12000; self.wallet.save(update_fields=['balance'])
        recharge = RechargeRecord.objects.create(account=self.account, user=self.boss, amount=10000, gift_amount=2000)
        lot = PointFundingLot.objects.create(account=self.account, source_key=f'recharge:{recharge.pk}', remaining=12000, kind='RECHARGE')
        result = self.client.post('/api/orders/gifts/send/', {'gift_service_id': self.one.pk, 'provider_id': self.recipient.pk,
            'quantity': 1, 'naming_title': '测试冠名', 'expected_amount': 2100, 'client_request_id': 'mixed-principal-gift'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        lot.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual(lot.remaining, 9900)
        self.assertEqual(wallet_funding_parts(self.wallet), {'prepaid_balance': 7900, 'gift_balance': 2000, 'compensation_balance': 0})

    def test_multiple_gifts_only_consume_principal_even_when_gift_lot_is_first(self):
        self.wallet.balance = 14400; self.wallet.save(update_fields=['balance'])
        gift = PointFundingLot.objects.create(account=self.account, source_key='basket-gift-first', remaining=5000, kind='GIFT')
        principal = PointFundingLot.objects.create(account=self.account, source_key='basket-principal', remaining=9400, kind='PREPAID')
        result = self.send(self.payload('principal-only-complete-basket'))
        self.assertEqual(result.data['code'], 0, result.data)
        principal.refresh_from_db(); gift.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((principal.remaining, gift.remaining, self.wallet.balance), (0, 5000, 5000))
        staff = make_console_user(['order:view', 'order:refund'])
        self.client.force_authenticate(staff)
        first = result.data['data']['orders'][0]['id']
        preview = self.client.get(f'/api/admin/orders/{first}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        refund = self.client.post(f'/api/admin/orders/{first}/refund/', {'reason': '礼物本金退回核对',
            'plan_token': preview.data['data']['plan_token'], 'client_request_id': 'principal-basket-refund'}, format='json')
        self.assertEqual(refund.data['code'], 0, refund.data)
        principal.refresh_from_db(); gift.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((principal.remaining, gift.remaining), (4200, 5000))
        self.assertEqual(wallet_funding_parts(self.wallet), {'prepaid_balance': 4200, 'gift_balance': 5000, 'compensation_balance': 0})

    def test_gift_counts_immediately_for_vip_and_refund_reduces_growth(self):
        from users.models import BossType
        tier_type = BossType.objects.create(name='隔离礼物会员', discount_rate=95)
        tier = VipTier.objects.create(boss_type=tier_type, level=1, recharge_threshold=1500)
        sent = self.client.post('/api/orders/gifts/send/', {'gift_service_id': self.one.pk, 'provider_id': self.recipient.pk,
            'quantity': 1, 'naming_title': '测试冠名', 'expected_amount': 2100, 'client_request_id': 'gift-vip-growth'}, format='json')
        self.assertEqual(sent.data['code'], 0, sent.data)
        order = Order.objects.get(pk=sent.data['data']['id'])
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, tier.boss_type_id)
        self.assertEqual(completed_consumption(self.account), 2100)
        self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 2100)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertEqual(MemberBenefitGrant.objects.filter(account=self.account, tier=tier).count(), 1)
        staff = make_console_user(['order:view', 'order:refund'])
        self.client.force_authenticate(staff)
        preview = self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 1000})
        self.assertEqual(preview.data['code'], 0, preview.data)
        refund = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '礼物会员成长退款核对',
            'refund_amount': 1000, 'plan_token': preview.data['data']['plan_token'],
            'client_request_id': 'gift-vip-refund'}, format='json')
        self.assertEqual(refund.data['code'], 0, refund.data)
        self.account.refresh_from_db()
        self.assertEqual(completed_consumption(self.account), 1100)
        self.assertNotEqual(self.account.boss_type_id, tier.boss_type_id)
        self.assertEqual(MemberBenefitGrant.objects.filter(account=self.account, tier=tier).count(), 1)

    def test_gift_during_disabled_vip_has_no_points_or_vip_growth(self):
        settings = config(); settings.vip_enabled = False; settings.points_enabled = True
        settings.save(update_fields=['vip_enabled', 'points_enabled'])
        result = self.client.post('/api/orders/gifts/send/', {'gift_service_id': self.one.pk, 'provider_id': self.recipient.pk,
            'quantity': 1, 'naming_title': '测试冠名', 'expected_amount': 2100, 'client_request_id': 'gift-vip-disabled'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
        settings.vip_enabled = True; settings.save(update_fields=['vip_enabled'])
        from engagement.services import award_order_points, confirm_gift_vip_consumption
        from stores.db import transaction
        with transaction.atomic():
            award_order_points(order)
            confirm_gift_vip_consumption(order)
        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertEqual(completed_consumption(self.account), 0)

    def test_gift_counts_for_enabled_vip_when_points_awards_are_disabled(self):
        settings = config(); settings.vip_enabled = True; settings.points_enabled = False
        settings.save(update_fields=['vip_enabled', 'points_enabled'])
        result = self.client.post('/api/orders/gifts/send/', {'gift_service_id': self.one.pk, 'provider_id': self.recipient.pk,
            'quantity': 1, 'naming_title': '测试冠名', 'expected_amount': 2100, 'client_request_id': 'gift-points-disabled'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual(completed_consumption(self.account), 2100)
        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())

    def test_browse_requires_boss_and_excludes_unsellable_festival_gifts(self):
        self.one.price = 2099; self.one.save()
        result = self.client.get('/api/orders/gifts/context/?browse=1')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertNotIn(self.one.pk, [row['id'] for row in result.data['data']['gifts']])
        self.client.force_authenticate(self.provider)
        self.assertNotEqual(self.client.get('/api/orders/gifts/context/?browse=1').data['code'], 0)

    def test_completed_order_and_direct_gifting_share_the_same_basket(self):
        source = make_order(self.boss, provider=self.provider, customer_account=self.account,
            status='COMPLETED', payment_status='PAID')
        result = self.send(url=f'/api/orders/orders/{source.pk}/tip/')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(Order.objects.filter(source_order=source).count(), 2)
        listing = self.client.get('/api/users/escorts/?directory=1').data['data']
        target = next(row for row in listing if row['id'] == self.recipient.pk)
        self.assertEqual(target['namings'][0]['title'], '老板的默契搭子')
        ProviderNaming.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        target = next(row for row in self.client.get('/api/users/escorts/?directory=1').data['data'] if row['id'] == self.recipient.pk)
        self.assertEqual(target['namings'], [])

    def test_combines_repeated_skus_and_never_accepts_client_duration(self):
        body = self.payload(); body['items'] = [{'gift_service_id': self.one.pk, 'quantity': 1}] * 3
        body['expected_amount'] = 6300; body['naming_days'] = 99999
        result = self.send(body)
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(result.data['data']['naming_days'], 6)
        self.assertEqual(len(result.data['data']['orders']), 1)

    def test_admin_moderation_preserves_expiry_and_hidden_state_on_next_gift(self):
        from orders.tests.factories import make_console_user
        self.assertEqual(self.send().data['code'], 0)
        naming = ProviderNaming.objects.get()
        original_expiry = naming.expires_at
        url = f'/api/admin/provider-namings/{naming.pk}/'
        self.client.force_authenticate(make_console_user(['engagement:view']))
        self.assertEqual(self.client.patch(url, {'is_visible': False}, format='json').status_code, 403)
        self.client.force_authenticate(make_console_user(['engagement:view', 'engagement:edit']))
        result = self.client.patch(url, {'is_visible': False, 'title': '调整后冠名',
            'expires_at': timezone.now() + timedelta(days=999)}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        naming.refresh_from_db()
        self.assertEqual(naming.expires_at, original_expiry)
        self.client.force_authenticate(self.boss)
        self.assertEqual(self.send(self.payload('basket-after-moderation')).data['code'], 0)
        naming.refresh_from_db()
        self.assertFalse(naming.is_visible)
        self.assertEqual(naming.expires_at, original_expiry + timedelta(days=7))
