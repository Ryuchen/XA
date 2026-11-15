import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderPrestartHandoff
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_PRESTART_HANDOFF_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
class PrestartHandoffBrowserTest(LiveServerTestCase):
    def test_staff_handoff_via_real_order_page(self):
        boss = make_user(); boss_account = get_or_create_account_for_legacy_user(boss)
        wallet = get_wallet(account=boss_account, user=boss); wallet.balance = 20000; wallet.save(update_fields=['balance'])
        providers = [make_provider(display_name=name, deposit_mode='CASH', deposit_paid=40000)
                     for name in ('隔离原陪玩','隔离接替陪玩')]
        accounts = [get_or_create_account_for_legacy_user(user) for user in providers]
        for user, account in zip(providers, accounts):
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
        service = make_service(name='隔离改派服务', price=10000, commission_rate=20)
        fractional = os.environ.get('XA_SEGMENT_FRACTION_BROWSER') == '1'
        coupon_id = None
        if fractional:
            from coupons.models import Coupon, UserCoupon
            service.price = 9000; service.save(update_fields=['price'])
            coupon = Coupon.objects.create(name='隔离金额券', discount_type='DIRECT', amount=7990)
            coupon_id = UserCoupon.objects.create(account=boss_account, coupon=coupon).pk
        legacy_review = os.environ.get('XA_PRESTART_LEGACY_BROWSER') == '1'
        game_transfer = os.environ.get('XA_SEGMENT_GAME_TRANSFER_BROWSER') == '1'
        standard_transfer = os.environ.get('XA_SEGMENT_STANDARD_TRANSFER_BROWSER') == '1'
        if legacy_review or game_transfer:
            from orders.models import GameCategory, GameCommissionProfile
            game = GameCategory.objects.create(name='隔离历史游戏')
            game_profile = GameCommissionProfile.objects.create(game_category=game, game='VALORANT', rules={'companion':20, 'grind':20})
            service.game_category = game; service.provider_kind = 'RANK'; service.commission_policy = 'REGULAR'
            service.save(update_fields=['game_category', 'provider_kind', 'commission_policy'])
        for user in providers:
            user.escort_profile.service_items.add(service)
        staff = make_console_user(['order:view','order:dispatch','escort:view','service:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        buyer = APIClient(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(boss_account)[0])
        response = buyer.post('/api/orders/create/', {'service_id':service.pk, 'provider_id':accounts[0].pk,
            **({'user_coupon_id':coupon_id} if coupon_id else {}),
            'client_request_id':'browser-prestart-handoff'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        if legacy_review:
            for key in ('catalog_family','required_level_id','required_level_rank'):
                order.game_commission_snapshot['service_scope'].pop(key)
            order.save(update_fields=['game_commission_snapshot'])
        original_transfer = os.environ.get('XA_ORIGINAL_TRANSFER_BROWSER') == '1'
        if original_transfer:
            from orders.tests.test_release_flow import screenshot
            player = APIClient(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(accounts[0])[0])
            started = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
        if game_transfer:
            game_profile.rules = {'companion':20, 'grind':50}
            game_profile.version += 1
            game_profile.save(update_fields=['rules', 'version'])
        if standard_transfer:
            service.commission_override = True; service.commission_rate = 35
            service.save(update_fields=['commission_override', 'commission_rate'])
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/admin-prestart-handoff-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'orderId':order.pk, 'orderNo':order.order_no, 'legacy':legacy_review, 'originalTransfer':original_transfer, 'fractional':fractional, 'gameTransfer':game_transfer, 'standardTransfer':standard_transfer, 'staff':{'id':staff_account.pk,
                    'token':issue_account_tokens(staff_account)[0], 'permissions':['order:view','order:dispatch','escort:view','service:view']}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        order.refresh_from_db(); wallet.refresh_from_db()
        self.assertEqual((order.provider_account_id, order.status, wallet.balance),
                         (accounts[1].pk, 'IN_SERVICE' if original_transfer else 'GRABBED', 18990 if fractional else 10000))
        self.assertEqual(OrderPrestartHandoff.objects.filter(order=order).count(), 0 if original_transfer else 1)
        if original_transfer:
            from orders.models import OrderOriginalShareTransfer
            event = OrderOriginalShareTransfer.objects.get(previous_segment__attribution__order=order)
            self.assertEqual((event.retained_share, event.transferred_share, event.agreement),
                             (3000 if fractional else 4000, 6000, '双方确认原价份额'))
        if game_transfer:
            from orders.models import OrderSegmentFinancialSnapshot
            incoming = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order,
                                                                   segment__provider_account=accounts[1])
            self.assertEqual((incoming.commission_rate, incoming.game_commission_snapshot['version']), (50, game_profile.version))
        if standard_transfer:
            from orders.models import OrderSegmentFinancialSnapshot
            incoming = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order,
                                                                   segment__provider_account=accounts[1])
            self.assertEqual((incoming.commission_rate, incoming.commission_source, incoming.income_at_entry),
                             (35, 'service', 3900))
        if legacy_review:
            review = OrderPrestartHandoff.objects.get(order=order).new_snapshot['legacy_minimum_review']
            self.assertEqual((review['choice'], review['evidence']), ('NONE', '原价目与历史记录核对'))
