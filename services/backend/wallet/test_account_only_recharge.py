"""A real business account can receive an audited recharge without a legacy user."""
from tempfile import TemporaryDirectory
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from wallet.models import RechargeRecord, Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=True, REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AccountOnlyRechargeTest(TransactionTestCase):
    def test_account_only_buyer_preserves_linked_referral_on_reservation(self):
        self.run_referral_reservation(account_only_inviter=False)

    def test_account_only_inviter_receives_and_refunds_referral(self):
        self.run_referral_reservation(account_only_inviter=True)

    def test_legacy_linked_buyer_can_refer_account_only_inviter(self):
        self.run_referral_reservation(account_only_inviter=True, linked_buyer=True)

    def test_legacy_buyer_inviter_rate_survives_unmigrated_account_referral(self):
        self.run_referral_reservation(account_only_inviter=False, linked_buyer=True, legacy_referral=True)

    def run_referral_reservation(self, *, account_only_inviter, linked_buyer=False, legacy_referral=False):
        media = TemporaryDirectory(prefix='xa-account-referral-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        if account_only_inviter:
            inviter_account=ClubAccount.objects.create_account(username='isolated-account-only-inviter',account_type='BOSS')
        else:
            inviter = make_user(); inviter_account = get_or_create_account_for_legacy_user(inviter)
        if linked_buyer:
            legacy_boss = make_user()
            if legacy_referral:
                legacy_boss.inviter = inviter
                legacy_boss.inviter_commission_rate = 50
                legacy_boss.save(update_fields=['inviter', 'inviter_commission_rate'])
            boss = get_or_create_account_for_legacy_user(legacy_boss)
            boss.inviter = None if legacy_referral else inviter_account
            boss.inviter_commission_rate = 0 if legacy_referral else 50
            boss.save(update_fields=['inviter','inviter_commission_rate'])
        else:
            boss = ClubAccount.objects.create_account(username='isolated-referral-boss', account_type='BOSS',
                inviter=inviter_account, inviter_commission_rate=50)
        provider = make_provider(); provider_account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = provider_account; provider.escort_profile.save(update_fields=['account'])
        from wallet.services import get_wallet
        wallet = get_wallet(account=boss); wallet.balance=10000; wallet.save(update_fields=['balance'])
        service = make_service(price=500,commission_rate=20)
        if legacy_referral:
            # This policy recalculates the provider and referral split when assigned.
            service.commission_policy = 'REGULAR'
            service.save(update_fields=['commission_policy'])
        owner, worker = APIClient(), APIClient()
        owner.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(boss)[0])
        worker.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(provider_account)[0])
        start = (timezone.now()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        booking = owner.post('/api/orders/reservations/',{'provider_account_id':provider_account.pk,'service_id':service.pk,
            'start_time':start.isoformat(),'end_time':(start+timedelta(hours=1)).isoformat()},format='json').data
        self.assertEqual(booking['code'],0,booking); rid=booking['data']['id']
        self.assertEqual(worker.post(f'/api/orders/reservations/{rid}/confirm/',{},format='json').data['code'],0)
        quote=owner.get(f'/api/orders/reservations/{rid}/quote/').data; self.assertEqual(quote['code'],0,quote)
        result=owner.post(f'/api/orders/reservations/{rid}/convert/',{'client_request_id':'account-only-inviter',
            'expected_amount':quote['data']['amount'],'expected_service':quote['data']['service']},format='json').data
        self.assertEqual(result['code'],0,result); oid=result['data']['order']['id']
        from orders.models import Order
        self.assertEqual(Order.objects.get(pk=oid).inviter_commission_rate_snapshot, 50)
        from PIL import Image
        from io import BytesIO
        image=BytesIO(); Image.new('RGB',(2,2),'white').save(image,format='PNG')
        for action,field in [('start','entry_image'),('complete','completion_image')]:
            result=worker.post(f'/api/orders/orders/{oid}/{action}/',
                {field:SimpleUploadedFile(action+'.png',image.getvalue(),content_type='image/png')},format='multipart').data
            self.assertEqual(result['code'],0,result)
        self.assertEqual(Transaction.objects.get(order_id=oid,wallet__account=inviter_account,tx_type='INCOME').amount,50)
        self.assertEqual(Transaction.objects.get(order_id=oid,tx_type='SHOP_INCOME').amount,50)
        staff=make_console_user(['order:view','order:refund','order:dispatch'])
        staff_account=get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account=staff_account; staff.admin_membership.save(update_fields=['account'])
        operator=APIClient(); operator.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff_account)[0])
        detail=operator.get(f'/api/admin/orders/{oid}/').data
        self.assertEqual(detail['code'],0,detail)
        self.assertEqual(detail['data']['settlement_status'],'SETTLED')
        self.assertEqual(detail['data']['inviter'], inviter_account.pk)
        self.assertEqual(detail['data']['inviter_name'], inviter_account.nickname or inviter_account.username)
        self.assertEqual(detail['data']['inviter_commission'], 50)
        preview=operator.get(f'/api/admin/orders/{oid}/refund-preview/').data
        self.assertEqual(preview['code'],0,preview)
        refunded=operator.post(f'/api/admin/orders/{oid}/refund/',{'reason':'隔离退款',
            'plan_token':preview['data']['plan_token']},format='json').data
        self.assertEqual(refunded['code'],0,refunded)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance,10000)
        self.assertEqual(get_wallet(account=inviter_account).balance,0)
        inviter_client=APIClient(); inviter_client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(inviter_account)[0])
        notices=inviter_client.get('/api/messages/').data['data']['messages']
        self.assertTrue(any('推荐收益变化' in row['preview'] for row in notices),notices)
        transactions = inviter_client.get('/api/wallet/transactions/', {'tx_type': 'INCOME'}).data
        self.assertEqual(transactions['code'], 0, transactions)
        rows = transactions['data']['transactions']
        self.assertEqual(sum(row['amount'] for row in rows if row['is_referral_income']), 0)
        if account_only_inviter and not linked_buyer:
            import json, shutil, subprocess
            from pathlib import Path
            from django.conf import settings
            node = shutil.which('node')
            self.assertIsNotNone(node, 'Node is required to verify native wallet rendering')
            repo = Path(settings.BASE_DIR).parent.parent
            rendered = subprocess.run([node, str(repo / 'scripts/native-referral-wallet-live.cjs')],
                input=json.dumps(rows), text=True, encoding='utf-8', capture_output=True, cwd=repo, timeout=15)
            self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
        if account_only_inviter:
            self.assertFalse(LegacyAccountMap.objects.filter(account=inviter_account).exists())
        gift=make_service(name='隔离推荐礼物',price=3000,service_category=make_service_category(is_gift=True),commission_rate=20)
        sent=owner.post('/api/orders/gifts/send/',{'gift_service_id':gift.pk,'provider_id':provider_account.pk,
            'quantity':1,'expected_amount':3000,'client_request_id':'referral-real-gift'},format='json').data
        self.assertEqual(sent['code'],0,sent)
        self.assertEqual(get_wallet(account=inviter_account).balance,300)
        gift_id=sent['data']['id']
        gift_preview=operator.get(f'/api/admin/orders/{gift_id}/refund-preview/').data
        self.assertEqual(gift_preview['code'],0,gift_preview)
        gift_refund=operator.post(f'/api/admin/orders/{gift_id}/refund/',{'reason':'隔离礼物退款',
            'plan_token':gift_preview['data']['plan_token']},format='json').data
        self.assertEqual(gift_refund['code'],0,gift_refund)
        self.assertEqual(get_wallet(account=inviter_account).balance,0)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance,10000)
        dispatch=operator.post('/api/admin/orders/dispatch/',{'customer_id':boss.pk,'service_id':service.pk,
            'game_rounds':1,'providers':[{'provider_id':provider_account.pk,'commission_rate':20}],
            'client_request_id':'account-only-referral-dispatch'},format='json').data
        self.assertEqual(dispatch['code'],0,dispatch); dispatched_id=dispatch['data']['id']
        for action,field in [('start','entry_image'),('complete','completion_image')]:
            result=worker.post(f'/api/orders/orders/{dispatched_id}/{action}/',
                {field:SimpleUploadedFile('dispatch-'+action+'.png',image.getvalue(),content_type='image/png')},format='multipart').data
            self.assertEqual(result['code'],0,result)
        self.assertEqual(Transaction.objects.get(order_id=dispatched_id,wallet__account=inviter_account,tx_type='INCOME').amount,50)
        direct=owner.post('/api/orders/create/',{'service_id':service.pk,'provider_id':provider_account.pk,
            'client_request_id':'account-referral-direct'},format='json').data
        self.assertEqual(direct['code'],0,direct); direct_id=direct['data']['id']
        for action,field in [('start','entry_image'),('complete','completion_image')]:
            result=worker.post(f'/api/orders/orders/{direct_id}/{action}/',
                {field:SimpleUploadedFile('direct-'+action+'.png',image.getvalue(),content_type='image/png')},format='multipart').data
            self.assertEqual(result['code'],0,result)
        self.assertEqual(Transaction.objects.get(order_id=direct_id,wallet__account=inviter_account,tx_type='INCOME').amount,50)

    def test_staff_recharges_account_and_owner_sees_balance_and_notice(self):
        media = TemporaryDirectory(prefix='xa-account-wallet-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = ClubAccount.objects.create_account(username='isolated-wallet-boss', account_type='BOSS')
        staff = make_console_user(['wallet:view', 'wallet:recharge', 'message:view', 'order:view', 'order:refund', 'order:settle'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        owner, operator = APIClient(), APIClient()
        owner.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(boss)[0])
        operator.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff_account)[0])
        info = owner.get('/api/wallet/info/').data
        self.assertEqual((info['code'],info['data']['balance']),(0,0))
        wallet = Wallet.objects.get(account=boss)
        self.assertIsNone(wallet.user_id)
        self.assertFalse(LegacyAccountMap.objects.filter(account=boss).exists())
        # Real image validation and local-only receipt storage.
        from PIL import Image
        from io import BytesIO
        image = BytesIO(); Image.new('RGB',(2,2),'white').save(image,format='PNG')
        receipt = SimpleUploadedFile('receipt.png', image.getvalue(), content_type='image/png')
        data = {'account_id':boss.pk,'amount':1000,'gift_amount':100,'trade_no':'isolated-wallet-receipt',
                'client_request_id':'isolated-wallet-recharge','proof_image':receipt}
        response = operator.post('/api/admin/wallets/recharge/',data,format='multipart').data
        self.assertEqual(response['code'],0,response)
        self.assertEqual(response['data']['wallet']['account_id'],boss.pk)
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance,wallet.total_recharge,wallet.total_gift),(1100,1000,100))
        record = RechargeRecord.objects.get(account=boss)
        self.assertIsNone(record.user_id)
        self.assertEqual(list(Transaction.objects.filter(wallet=wallet).order_by('id').values_list('amount',flat=True)),[1000,100])
        self.assertEqual(owner.get('/api/wallet/info/').data['data']['balance'],1100)
        messages = owner.get('/api/messages/').data['data']['messages']
        self.assertTrue(any(row['title']=='充值已到账' for row in messages),messages)
        duplicate = operator.post('/api/admin/wallets/recharge/',{**data,'proof_image':SimpleUploadedFile('receipt.png',image.getvalue(),content_type='image/png')},format='multipart').data
        self.assertEqual(duplicate['code'],409,duplicate)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance,1100)
        provider = make_provider()
        provider_account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = provider_account; provider.escort_profile.save(update_fields=['account'])
        provider_client = APIClient(); provider_client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(provider_account)[0])
        service = make_service(price=500)
        start = (timezone.now()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        reservation = owner.post('/api/orders/reservations/',{'provider_account_id':provider_account.pk,
            'service_id':service.pk,'start_time':start.isoformat(),'end_time':(start+timedelta(hours=1)).isoformat()},format='json').data
        self.assertEqual(reservation['code'],0,reservation)
        rid = reservation['data']['id']
        confirmation = provider_client.post(f'/api/orders/reservations/{rid}/confirm/',{},format='json').data
        self.assertEqual(confirmation['code'],0,confirmation)
        quote = owner.get(f'/api/orders/reservations/{rid}/quote/').data
        self.assertEqual(quote['code'],0,quote)
        converted = owner.post(f'/api/orders/reservations/{rid}/convert/',{'client_request_id':'account-only-convert',
            'expected_amount':quote['data']['amount'],'expected_service':quote['data']['service']},format='json').data
        self.assertEqual(converted['code'],0,converted)
        order_id = converted['data']['order']['id']
        for client, prefix in ((owner,'/api/orders/orders/'),(provider_client,'/api/orders/orders/')):
            result = client.get(f'{prefix}{order_id}/').data
            self.assertEqual(result['code'],0,result)
            self.assertEqual(result['data']['customer']['nickname'],boss.username)
        admin_order = operator.get(f'/api/admin/orders/{order_id}/').data
        self.assertEqual(admin_order['code'],0,admin_order)
        self.assertEqual((admin_order['data']['customer'],admin_order['data']['customer_name']),(boss.pk,boss.username))
        wallet.refresh_from_db(); self.assertEqual(wallet.balance,600)
        self.assertEqual(Transaction.objects.filter(wallet=wallet,order_id=order_id,tx_type='PAY').count(),1)
        started = provider_client.post(f'/api/orders/orders/{order_id}/start/',{
            'entry_image':SimpleUploadedFile('entry.png',image.getvalue(),content_type='image/png'),
            'client_request_id':'account-only-start'},format='multipart').data
        self.assertEqual(started['code'],0,started)
        completed = provider_client.post(f'/api/orders/orders/{order_id}/complete/',{
            'completion_image':SimpleUploadedFile('completion.png',image.getvalue(),content_type='image/png'),
            'client_request_id':'account-only-complete'},format='multipart').data
        self.assertEqual(completed['code'],0,completed)
        self.assertEqual(completed['data']['status'],'COMPLETED')
        reviewed = operator.get(f'/api/admin/orders/{order_id}/').data
        self.assertEqual(reviewed['code'],0,reviewed)
        self.assertEqual(reviewed['data']['settlement_status'],'SETTLED')
        self.assertEqual(Transaction.objects.get(order_id=order_id,tx_type='INCOME').amount,400)
        self.assertEqual(Transaction.objects.get(order_id=order_id,tx_type='SHOP_INCOME').amount,100)
        evaluation = owner.post('/api/orders/evaluate/',{'order_id':order_id,'score':5,
            'content':'隔离服务评价'},format='json').data
        self.assertEqual(evaluation['code'],0,evaluation)
        self.assertEqual(evaluation['data']['customer_name'],boss.username)
        self.assertTrue(any(row['title']=='收到新的服务评价' for row in provider_client.get('/api/messages/').data['data']['messages']))
        replied = provider_client.post(f"/api/orders/evaluations/{evaluation['data']['id']}/reply/",
            {'reply_content':'感谢评价','reply_version':evaluation['data']['reply_version']},format='json').data
        self.assertEqual(replied['code'],0,replied)
        self.assertTrue(any(row['title']=='陪玩回复了你的评价' for row in owner.get('/api/messages/').data['data']['messages']))
        case = owner.post(f'/api/orders/orders/{order_id}/aftersale/',{'reason':'隔离售后咨询',
            'client_request_id':'account-only-aftersale'},format='json').data
        self.assertEqual(case['code'],0,case)
        self.assertTrue(any(row['title']=='订单售后处理中' for row in provider_client.get('/api/messages/').data['data']['messages']))
        resolved = operator.post(f"/api/admin/aftersales/{case['data']['id']}/resolve/",
            {'resolution':'已核对相关资料'},format='json').data
        self.assertEqual(resolved['code'],0,resolved)
        self.assertTrue(any(row['title']=='售后已处理' for row in owner.get('/api/messages/').data['data']['messages']))
        reopened = owner.post(f'/api/orders/orders/{order_id}/aftersale/',{'reason':'继续申请退款',
            'client_request_id':'account-only-aftersale-reopen'},format='json').data
        self.assertEqual(reopened['code'],0,reopened)
        # A second account-only wallet must not be mistaken for this buyer on refund.
        from wallet.services import get_wallet
        other = ClubAccount.objects.create_account(username='isolated-other-wallet-boss', account_type='BOSS')
        get_wallet(account=other)
        preview = operator.get(f'/api/admin/orders/{order_id}/refund-preview/').data
        self.assertEqual(preview['code'],0,preview)
        refund = operator.post(f"/api/admin/aftersales/{case['data']['id']}/refund/", {'resolution':'隔离售后退款',
            'plan_token':preview['data']['plan_token']},format='json').data
        self.assertEqual(refund['code'],0,refund)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance,1100)
        self.assertEqual(Transaction.objects.filter(wallet=wallet,order_id=order_id,tx_type='REFUND').count(),1)
        notices = owner.get('/api/messages/').data['data']['messages']
        self.assertTrue(any('退款' in row['title'] for row in notices),notices)
        gift = make_service(name='隔离礼物',price=100,service_category=make_service_category(is_gift=True))
        sent = owner.post('/api/orders/gifts/send/',{'gift_service_id':gift.pk,'provider_id':provider_account.pk,
            'quantity':1,'expected_amount':100,'client_request_id':'account-only-gift'},format='json').data
        self.assertEqual(sent['code'],0,sent)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance,1000)
        provider_messages = provider_client.get('/api/messages/').data['data']['messages']
        self.assertTrue(any(row['title']=='收到老板礼物' for row in provider_messages),provider_messages)
        direct = owner.post('/api/orders/create/',{'service_id':service.pk,'provider_id':provider_account.pk,
            'client_request_id':'account-only-direct-order'},format='json').data
        self.assertEqual(direct['code'],0,direct)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance,500)
        self.assertEqual(direct['data']['customer']['nickname'],boss.username)
        self.assertFalse(LegacyAccountMap.objects.filter(account=boss).exists())
