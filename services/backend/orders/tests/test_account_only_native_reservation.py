"""Native reservation controllers work with an account-only boss over real HTTP."""
import json
import os
import subprocess
from pathlib import Path
from io import BytesIO
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import AfterSale, Order, Reservation
from orders.tests.factories import make_console_user, make_provider, make_service
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AccountOnlyNativeReservationTest(LiveServerTestCase):
    def test_native_create_confirm_and_convert_without_legacy_boss(self):
        boss = ClubAccount.objects.create_account(username='native-account-only-boss', account_type='BOSS')
        provider = make_provider(); provider_account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = provider_account; provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(account=boss); wallet.balance = 50000; wallet.save(update_fields=['balance'])
        service = make_service(price=10000)
        token, refresh = issue_account_tokens(boss)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node',str(repo/'scripts/native-reservation-live.cjs')],cwd=repo,
            input=json.dumps({'base':self.live_server_url,'serviceId':service.pk,'providerId':provider_account.pk,
                'providerToken':issue_account_tokens(provider_account)[0],'adminToken':'',
                'session':{'token':token,'refresh':refresh,'user':{'id':str(boss.pk),'role':'customer','nickname':'隔离老板'}}}),
            text=True,encoding='utf-8',capture_output=True,timeout=35)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        reservation = Reservation.objects.get(customer_account=boss,status='CONVERTED')
        self.assertIsNone(reservation.customer_id)
        self.assertIsNone(reservation.order.customer_id)
        self.assertEqual(Order.objects.filter(customer_account=boss).count(),1)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance,40000)
        self.assertEqual(Transaction.objects.filter(wallet=wallet,order=reservation.order,tx_type='PAY').count(),1)
        self.assertFalse(LegacyAccountMap.objects.filter(account=boss).exists())
        media=TemporaryDirectory(prefix='xa-account-native-aftersale-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        provider_client=APIClient(); provider_client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(provider_account)[0])
        from PIL import Image
        image=BytesIO(); Image.new('RGB',(2,2),'white').save(image,format='PNG')
        for action,field in [('start','entry_image'),('complete','completion_image')]:
            response=provider_client.post(f'/api/orders/orders/{reservation.order_id}/{action}/',
                {field:SimpleUploadedFile(action+'.png',image.getvalue(),content_type='image/png')},format='multipart').data
            self.assertEqual(response['code'],0,response)
        staff=make_console_user(['order:view','order:settle'])
        staff_account=get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account=staff_account; staff.admin_membership.save(update_fields=['account'])
        result=subprocess.run(['node',str(repo/'scripts/native-aftersale-concurrent-live.cjs')],cwd=repo,
            input=json.dumps({'base':self.live_server_url,'orderId':reservation.order_id,
                'staffToken':issue_account_tokens(staff_account)[0],
                'session':{'token':token,'refresh':refresh,'user':{'id':str(boss.pk),'role':'customer'}}}),
            text=True,encoding='utf-8',capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        case=AfterSale.objects.get(order_id=reservation.order_id)
        self.assertEqual((case.status,case.reason),('OPEN','第二设备填写的新说明'))
        owner=APIClient(); owner.credentials(HTTP_AUTHORIZATION='Bearer '+token)
        self.assertTrue(any(row['title']=='售后已处理' for row in owner.get('/api/messages/').data['data']['messages']))
        result=subprocess.run(['node',str(repo/'scripts/native-account-only-evaluation-live.cjs')],cwd=repo,
            input=json.dumps({'base':self.live_server_url,'orderId':reservation.order_id,'token':token,
                'bossId':str(boss.pk),'providerId':provider_account.pk}),
            text=True,encoding='utf-8',capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        from orders.models import Evaluation
        evaluation=Evaluation.objects.get(order_id=reservation.order_id)
        self.assertIsNone(evaluation.customer_id)
        self.assertEqual((evaluation.customer_account_id,evaluation.provider_account_id), (boss.pk,provider_account.pk))
        mine=provider_client.get('/api/orders/evaluations/mine/',{'page':1,'page_size':20}).data['data']
        self.assertEqual((mine['total'],mine['pending_total'],mine['list'][0]['content']),(1,1,'账号老板的真实评价'))
        role=staff.admin_membership.roles.first()
        role.permissions=[*role.permissions,'evaluation:view','evaluation:reply','evaluation:delete']; role.save(update_fields=['permissions'])
        provider_root=os.environ.get('XA_PROVIDER_VUE_ROOT'); admin_root=os.environ.get('XA_ADMIN_VUE_ROOT')
        if provider_root and admin_root:
            browser=subprocess.run(['node',str(repo/'scripts/account-only-evaluation-three-role-live.cjs')],cwd=repo,
                input=json.dumps({'base':self.live_server_url,'orderId':reservation.order_id,
                    'providerRoot':provider_root,'adminRoot':admin_root,
                    'provider':{'id':provider_account.pk,'token':issue_account_tokens(provider_account)[0]},
                    'staffToken':issue_account_tokens(staff_account)[0]}),
                text=True,encoding='utf-8',capture_output=True,timeout=40)
            self.assertEqual(browser.returncode,0,browser.stdout+browser.stderr)
        else:
            reply=provider_client.post(f'/api/orders/evaluations/{evaluation.pk}/reply/',
                {'reply_content':'感谢老板','reply_version':0},format='json').data
            self.assertEqual(reply['code'],0,reply)
        staff_client=APIClient(); staff_client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff_account)[0])
        admin=staff_client.get(f'/api/admin/evaluations/{evaluation.pk}/').data['data']
        self.assertEqual((admin['customer_username'],admin['reply_content']),(boss.username,'感谢老板'))
        detail=owner.get(f'/api/orders/orders/{reservation.order_id}/').data['data']
        self.assertEqual(detail['evaluations'][0]['reply_content'],'感谢老板')
        notices=owner.get('/api/messages/').data['data']['messages']
        self.assertEqual(len([row for row in notices if row['title']=='陪玩回复了你的评价'
                              and row['related_order_id']==reservation.order_id]),1)
        response=staff_client.post(f'/api/admin/evaluations/{evaluation.pk}/reply/',
            {'reply_content':'客服核对后的回复','reply_version':admin['reply_version']},format='json').data
        self.assertEqual(response['code'],0,response)
        self.assertEqual(owner.get(f'/api/orders/orders/{reservation.order_id}/').data['data']['evaluations'][0]['reply_content'],'客服核对后的回复')
        notices=owner.get('/api/messages/').data['data']['messages']
        self.assertEqual(len([row for row in notices if row['title']=='客服回复了你的评价'
                              and row['related_order_id']==reservation.order_id]),1)
        provider.escort_profile.refresh_from_db()
        self.assertEqual(provider.escort_profile.rating_count,1)
        deleted=staff_client.delete(f'/api/admin/evaluations/{evaluation.pk}/',
            {'reply_version':response['data']['reply_version']},format='json').data
        self.assertEqual(deleted['code'],0,deleted)
        provider.escort_profile.refresh_from_db()
        self.assertEqual(provider.escort_profile.rating_count,0)
        result=subprocess.run(['node',str(repo/'scripts/native-account-only-evaluation-live.cjs')],cwd=repo,
            input=json.dumps({'base':self.live_server_url,'orderId':reservation.order_id,'token':token,
                'bossId':str(boss.pk),'providerId':provider_account.pk,'content':'重新评价陪玩'}),
            text=True,encoding='utf-8',capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        provider.escort_profile.refresh_from_db()
        self.assertEqual(provider.escort_profile.rating_count,1)
        self.assertEqual(owner.get(f'/api/orders/orders/{reservation.order_id}/').data['data']['evaluations'][0]['content'],'重新评价陪玩')
        self.assertFalse(LegacyAccountMap.objects.filter(account=boss).exists())
