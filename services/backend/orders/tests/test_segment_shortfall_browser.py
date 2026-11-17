import os
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.utils import timezone
from django.test import LiveServerTestCase
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_staff_team_dispatch import screenshot
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_SEGMENT_SHORTFALL_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
class SegmentShortfallBrowserTest(LiveServerTestCase):
    def test_staff_sees_unsettled_store_shortfall_after_team_transfer(self):
        boss = make_user(); buyer = get_or_create_account_for_legacy_user(boss)
        wallet = get_wallet(account=buyer, user=boss); wallet.balance = 1000; wallet.save(update_fields=['balance'])
        players = [make_provider(deposit_mode='EARNINGS') for _ in range(3)]
        accounts = [get_or_create_account_for_legacy_user(player) for player in players]
        for player, account in zip(players, accounts):
            player.escort_profile.account = account; player.escort_profile.save(update_fields=['account'])
        service = make_service(price=100, billing_provider_count=2, commission_override=True,
                               commission_type='FIXED', commission_fixed=7)
        coupon = Coupon.objects.create(name='隔离金额券', discount_type='DIRECT', threshold=0,
            amount=190, valid_to=timezone.now()+timedelta(days=1), is_active=True)
        owned = UserCoupon.objects.create(account=buyer, coupon=coupon)
        staff = make_console_user(['order:view', 'order:dispatch'])
        operator = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
        client = APIClient(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(buyer)[0])
        # This browser scenario verifies the order and finance UI, not the
        # external delayed-task transport; do not run an eager retry loop.
        with patch('orders.views._schedule_auto_cancel'):
            created = client.post('/api/orders/create/', {'service_id':service.pk, 'dispatch_route':'STAFF',
                'user_coupon_id':owned.pk, 'client_request_id':'segment-shortfall-browser'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']
        client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(operator)[0])
        assigned = client.post(f'/api/admin/orders/{order_id}/assign/',
                               {'provider_ids':[accounts[0].pk, accounts[1].pk]}, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)
        for account in accounts[:2]:
            client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
            started = client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
        client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(operator)[0])
        transferred = client.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'隔离验证', 'transfers':[{
            'old_provider_id':accounts[0].pk, 'new_provider_id':accounts[2].pk,
            'original_share':{'retained':80, 'transferred':20, 'agreement':'双方确认份额'}}]}, format='json')
        self.assertEqual(transferred.data['code'], 0, transferred.data)
