"""Real redemption and staff fulfillment update an open native claim page."""
import json
import os
from pathlib import Path
import subprocess
from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.models import MemberTag, RewardClaim, RewardItem
from orders.tests.factories import make_console_user, make_user
from users.test_native_checkin_order_realtime import isolated_redis_asgi


@skipUnless(os.environ.get('XA_CLAIM_LIVE_TEST') == '1', 'Requires isolated Redis and ASGI processes')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NativeClaimProgressLiveTest(LiveServerTestCase):
    def test_redeem_and_admin_delivery_keep_open_draft(self):
        boss, staff = make_user(), make_console_user(['engagement:points', 'engagement:fulfill'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff)]
        for user, account in zip((boss, staff), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]
        staff.admin_membership.save(update_fields=['account'])
        boss_client, admin = APIClient(), APIClient()
        for client, account in zip((boss_client, admin), accounts):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        for index in range(8):
            credit = admin.post('/api/admin/external-points/', {'account_id':accounts[0].pk, 'kind':'KOOK_CHECKIN',
                'day':(timezone.localdate() - timedelta(days=index)).isoformat(), 'source_id':f'claim-live-checkin-{index}'}, format='json')
            self.assertEqual(credit.data['code'], 0, credit.data)
        reward = RewardItem.objects.create(code='native-claim-live', name='实时交付权益', kind='SERVICE',
            point_cost=1, stock=30, in_shop=True, is_active=True)
        claim_id = None
        for index in range(24):
            redeemed = boss_client.post('/api/club/redeem/', {'reward_id':reward.pk,
                'client_request_id':f'native-claim-live-{index}'}, format='json')
            self.assertEqual(redeemed.data['code'], 0, redeemed.data)
            if claim_id is None: claim_id = redeemed.data['data']['id']
        saved = boss_client.patch(f'/api/club/claims/{claim_id}/', {'details':{'game_uid':'已提交的游戏 UID'}}, format='json')
        self.assertEqual(saved.data['code'], 0, saved.data)
        retry = boss_client.patch(f'/api/club/claims/{claim_id}/', {'details':{'game_uid':'已提交的游戏 UID'},'expected_details':{}}, format='json')
        self.assertEqual(retry.data['code'], 0, retry.data)
        self.assertEqual(retry.data['data']['details']['game_uid'], '已提交的游戏 UID')
        stale = boss_client.patch(f'/api/club/claims/{claim_id}/', {'details':{'game_uid':'旧设备资料'},'expected_details':{}}, format='json')
        self.assertEqual((stale.status_code,stale.data['errorCode']),(409,'CLAIM_DETAILS_CHANGED'))
        other = get_or_create_account_for_legacy_user(make_user())
        with isolated_redis_asgi() as (base, ws_base):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/native-claim-progress-live.cjs')], cwd=root,
                input=json.dumps({'base':base, 'wsBase':ws_base, 'account':accounts[0].pk, 'claim':claim_id,
                    'token':issue_account_tokens(accounts[0])[0], 'staffToken':issue_account_tokens(accounts[1])[0],
                    'otherToken':issue_account_tokens(other)[0], 'reward':reward.pk,
                    'creditDay':(timezone.localdate()-timedelta(days=10)).isoformat()}),
                encoding='utf-8', text=True, capture_output=True, timeout=35)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        claim = RewardClaim.objects.get(pk=claim_id)
        self.assertEqual((claim.status, claim.delivery_note), ('COMPLETED', '权益已交付'))
        self.assertEqual(claim.details['game_uid'], '响应丢失但已保存')

    def test_suspended_tag_expires_while_native_page_is_open(self):
        boss, staff = make_user(), make_console_user(['engagement:points', 'engagement:fulfill'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff)]
        for user, account in zip((boss, staff), accounts): link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        clients = [APIClient(), APIClient()]
        for client, account in zip(clients, accounts): client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        credit = clients[1].post('/api/admin/external-points/', {'account_id':accounts[0].pk,'kind':'KOOK_CHECKIN',
            'day':timezone.localdate().isoformat(),'source_id':'native-tag-expiry-credit'}, format='json')
        self.assertEqual(credit.data['code'],0,credit.data)
        reward = RewardItem.objects.create(code='native-tag-expiry',name='到期标签',kind='TAG',point_cost=1,
            stock=1,duration_days=7,in_shop=True,is_active=True)
        result = clients[0].post('/api/club/redeem/', {'reward_id':reward.pk,'details':{'tag':'原有标签'},
            'client_request_id':'native-tag-expiry'},format='json')
        self.assertEqual(result.data['code'],0,result.data); claim_id=result.data['data']['id']
        base=f'/api/admin/club-claims/{claim_id}'
        returned=clients[1].post(base+'/process/',{'status':'PENDING','note':'请调整标签'},format='json')
        self.assertEqual(returned.data['code'],0,returned.data)
        root=Path(settings.BASE_DIR).parent.parent
        native=subprocess.run(['node',str(root/'scripts/native-tag-return-live.cjs')],cwd=root,
            input=json.dumps({'base':self.live_server_url,'account':accounts[0].pk,'claim':claim_id,
                'token':issue_account_tokens(accounts[0])[0]}),encoding='utf-8',text=True,capture_output=True,timeout=20)
        self.assertEqual(native.returncode,0,native.stdout+native.stderr)
        result=clients[1].post(base+'/process/',{'status':'COMPLETED','note':'审核通过','tag_reviewed':True},format='json')
        self.assertEqual(result.data['code'],0,result.data)
        result=clients[1].post(base+'/suspend-tag/',{'reason':'需要修改','expected_tag':'审核修改标签'},format='json')
        self.assertEqual(result.data['code'],0,result.data)
        expiry=timezone.now()+timedelta(seconds=6)
        RewardClaim.objects.filter(pk=claim_id).update(expires_at=expiry)
        MemberTag.objects.filter(event_key=f'claim:{claim_id}:tag').update(expires_at=expiry)
        root=Path(settings.BASE_DIR).parent.parent
        result=subprocess.run(['node',str(root/'scripts/native-claim-tag-expiry-live.cjs')],cwd=root,
            input=json.dumps({'base':self.live_server_url,'account':accounts[0].pk,'claim':claim_id,
                'token':issue_account_tokens(accounts[0])[0]}),encoding='utf-8',text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
