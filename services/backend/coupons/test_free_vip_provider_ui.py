"""Assigned provider sees the actual free VIP order income before starting."""
import json
from pathlib import Path
import subprocess
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from common.fronted_test_support import fronted_root
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon
from engagement.models import VipTier
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service, make_user, make_wallet
from orders.models import Order
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, VIP_SAFE_BOX_SUBSIDY_ENABLED=False)
class FreeVipProviderUITest(LiveServerTestCase):
    def test_zero_income_is_explicit_before_starting(self):
        fronted_root('provider')
        boss, provider, staff = make_user(), make_provider(), make_console_user(['order:view','order:dispatch'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss,provider,staff)]
        link_legacy_relations(boss,accounts[0]); link_legacy_relations(provider,accounts[1])
        provider.escort_profile.account=accounts[1]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account=accounts[2]; staff.admin_membership.save(update_fields=['account'])
        service=make_service(price=10000,benefit_kind='SAFEBOX_33',platform='PC')
        provider.escort_profile.service_items.add(service)
        tier=make_boss_type(name='免费券验收会员',discount_rate=90)
        VipTier.objects.create(boss_type=tier,level=6,recharge_threshold=0,safe_box_service_ids=[service.pk])
        accounts[0].boss_type=tier; accounts[0].save(update_fields=['boss_type'])
        make_wallet(boss,balance=10000); grant_periodic(accounts[0])
        voucher=UserCoupon.objects.get(account=accounts[0],coupon__scope='SAFE_BOX')
        clients=[]
        for account in accounts:
            client=APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0]); clients.append(client)
        order=clients[0].post('/api/orders/create/', {'service_id':service.pk,'user_coupon_id':voucher.pk,
            'dispatch_route':'STAFF','client_request_id':'free-vip-income-display'}, format='json').data
        self.assertEqual(order['code'],0,order); self.assertEqual(order['data']['amount'],0)
        oid=order['data']['id']
        assigned=clients[2].post(f'/api/admin/orders/{oid}/assign/', {'provider_id':accounts[1].pk}, format='json').data
        self.assertEqual(assigned['code'],0,assigned)
        self.assertEqual(clients[1].get(f'/api/orders/orders/{oid}/').data['data']['expected_income'],0)
        repo=Path(settings.BASE_DIR).parent.parent
        result=subprocess.run(['node',str(repo/'scripts/scenarios/provider-zero-income-live.cjs')],cwd=repo,
            input=json.dumps({'base':self.live_server_url,
                'id':accounts[1].pk,'token':issue_account_tokens(accounts[1])[0], 'orderId':oid}),
            text=True,encoding='utf-8',capture_output=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        print(result.stdout.strip())
        persisted=Order.objects.get(pk=oid)
        self.assertEqual((persisted.status,persisted.payment_status,persisted.amount),('GRABBED','PAID',0))
        self.assertEqual(Wallet.objects.get(user=boss).balance,10000)
        self.assertEqual(Wallet.objects.get(user=provider).balance,0)
        self.assertFalse(Transaction.objects.filter(order=persisted,tx_type='INCOME').exists())
        voucher.refresh_from_db()
        self.assertEqual(voucher.status,'USED')
