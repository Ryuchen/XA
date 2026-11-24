"""Gift benefit from boss purchase through staff delivery and boss review."""
import os
from tempfile import TemporaryDirectory

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import GiftBenefitRule, RewardClaim, RewardItem
from orders.models import ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class GiftClaimMiniappJourneyTest(LiveServerTestCase):
    host = '127.0.0.1'

    def test_gift_entitlement_delivery_is_visible_to_boss(self):
        media = TemporaryDirectory(prefix='xa-gift-claim-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = make_user()
        provider = make_provider(display_name='权益验收陪玩', is_verified=True, deposit_mode='CASH')
        staff = make_console_user(['wallet:recharge', 'engagement:view', 'engagement:fulfill'])
        fulfiller = make_console_user(['engagement:fulfill'])
        points_clerk = make_console_user(['engagement:points'])
        boss_account, provider_account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        fulfiller_account = get_or_create_account_for_legacy_user(fulfiller)
        points_account = get_or_create_account_for_legacy_user(points_clerk)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        fulfiller.admin_membership.account = fulfiller_account
        fulfiller.admin_membership.save(update_fields=['account'])
        points_clerk.admin_membership.account = points_account
        points_clerk.admin_membership.save(update_fields=['account'])
        category = ServiceCategory.objects.create(name='验收礼物', is_gift=True)
        gift = make_service(name='权益验收礼物', price=2100, service_category=category)
        reward = RewardItem.objects.create(code='gift-claim-ui', name='赠礼专属权益', kind='SERVICE', in_shop=False)
        GiftBenefitRule.objects.create(name='礼物权益验收', reward=reward, category=category,
            threshold=2100, inclusive=True, enabled=True)
        staff_client, boss_client = APIClient(), APIClient()
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        ok(staff_client.post('/api/admin/wallets/recharge/', {'account_id': boss_account.pk, 'amount': 10000,
            'gift_amount': 0, 'trade_no': 'gift-claim-recharge', 'proof_image': screenshot(),
            'client_request_id': 'gift-claim-recharge'}, format='multipart'))
        if os.environ.get('XA_NATIVE_GIFT_CLAIM') == '1':
            self.delivery_with_return(boss_account, provider_account.pk, gift.pk, staff_account)
        else:
            ok(boss_client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'quantity': 1,
                'provider_id': provider_account.pk, 'expected_amount': 2100, 'client_request_id': 'gift-claim-send'}, format='json'))
        claim = RewardClaim.objects.get(account=boss_account, reward=reward)
        if os.environ.get('XA_NATIVE_GIFT_CLAIM') != '1':
            self.assertEqual(claim.status, 'PENDING')
            self.assertEqual(ok(boss_client.get(f'/api/club/claims/{claim.pk}/'))['status'], 'PENDING')
            filtered = ok(staff_client.get('/api/admin/club-claims/', {'status': 'PENDING', 'source': 'GIFT', 'search': '赠礼专属'}))
            self.assertEqual([row['id'] for row in filtered['list']], [claim.pk])
            self.assertEqual(ok(staff_client.get('/api/admin/club-claims/', {'status': 'COMPLETED', 'source': 'GIFT'}))['total'], 0)
            self.admin_delivery(fulfiller_account, claim.pk, boss=boss_account)
            points_client = APIClient()
            points_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(points_account)[0])
            forbidden = points_client.post(f'/api/admin/club-claims/{claim.pk}/process/',
                {'status': 'COMPLETED', 'note': '越权交付'}, format='json')
            self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(ok(boss_client.get(f'/api/club/claims/{claim.pk}/'))['delivery_note'], '权益已交付')
        if os.environ.get('XA_NATIVE_GIFT_CLAIM') == '1':
            self.assertEqual(ok(boss_client.get(f'/api/club/claims/{claim.pk}/'))['details']['game_uid'], '老板未保存UID')
        rows = [item for item in ok(boss_client.get('/api/messages/'))['messages'] if item['title'] == '权益交付进度更新']
        self.assertEqual(len(rows), 3 if os.environ.get('XA_NATIVE_GIFT_CLAIM') == '1' else 1)
        if os.environ.get('XA_NATIVE_GIFT_CLAIM') == '1':
            self.assertTrue(any('已退回补充资料' in item['preview'] for item in rows))
        detail = ok(boss_client.get(f"/api/messages/{rows[0]['id']}/"))
        self.assertEqual(detail['action_url'], f'/pages/reward-claim/index?id={claim.pk}')
        ok(staff_client.post(f'/api/admin/club-claims/{claim.pk}/process/',
            {'status': 'COMPLETED', 'note': '权益已交付', 'expected_status': 'PENDING'}, format='json'))
        conflict = staff_client.post(f'/api/admin/club-claims/{claim.pk}/process/',
            {'status': 'COMPLETED', 'note': '另一条交付说明', 'expected_status': 'PENDING'}, format='json')
        self.assertEqual((conflict.status_code, conflict.data['errorCode']), (409, 'CLAIM_STATUS_CHANGED'))
        self.assertEqual(len([item for item in ok(boss_client.get('/api/messages/'))['messages']
                              if item['title'] == '权益交付进度更新']), 3 if os.environ.get('XA_NATIVE_GIFT_CLAIM') == '1' else 1)
        if os.environ.get('XA_NATIVE_GIFT_CLAIM') != '1':
            ok(boss_client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'quantity': 1,
                'provider_id': provider_account.pk, 'expected_amount': 2100, 'client_request_id': 'gift-claim-race'}, format='json'))
            other_claim = RewardClaim.objects.filter(account=boss_account, reward=reward).exclude(pk=claim.pk).get()
            self.admin_delivery(fulfiller_account, other_claim.pk, boss=boss_account, other_staff=staff_account)
            self.assertEqual(ok(boss_client.get(f'/api/club/claims/{other_claim.pk}/'))['delivery_note'], '第一位客服已交付')
            self.assertEqual(len([item for item in ok(boss_client.get('/api/messages/'))['messages']
                                  if item['title'] == '权益交付进度更新']), 2)

    def delivery_with_return(self, account, provider_id, gift_id, staff):
        boss_client, staff_client = APIClient(), APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        ok(boss_client.post('/api/orders/gifts/send/', {'gift_service_id': gift_id, 'quantity': 1,
            'provider_id': provider_id, 'expected_amount': 2100, 'client_request_id': 'gift-claim-send'}, format='json'))
        claim = RewardClaim.objects.get(account=account, source='GIFT')
        process = f'/api/admin/club-claims/{claim.pk}/process/'
        ok(staff_client.post(process, {'status': 'FULFILLING', 'note': '客服正在处理'}, format='json'))
        locked = boss_client.patch(f'/api/club/claims/{claim.pk}/',
            {'details': {**claim.details, 'game_uid': '老板未保存UID'}}, format='json')
        self.assertEqual((locked.status_code, locked.data['errorCode']), (409, 'CLAIM_NOT_EDITABLE'))
        ok(staff_client.post(process, {'status': 'PENDING', 'note': '请补充游戏UID',
            'expected_status': 'FULFILLING', 'expected_details': claim.details}, format='json'))
        saved = {**claim.details, 'game_uid': '老板未保存UID'}
        ok(boss_client.patch(f'/api/club/claims/{claim.pk}/', {'details': saved}, format='json'))
        ok(staff_client.post(process, {'status': 'COMPLETED', 'note': '权益已交付',
            'expected_status': 'PENDING', 'expected_details': saved}, format='json'))

    def admin_delivery(self, staff, claim_id, boss, other_staff=None):
        staff_client, boss_client = APIClient(), APIClient()
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        process = f'/api/admin/club-claims/{claim_id}/process/'
        details = {'game_uid': 'new-uid-375', 'note': '最新交付要求'}
        ok(boss_client.patch(f'/api/club/claims/{claim_id}/', {'details': details}, format='json'))
        if other_staff:
            other_client = APIClient()
            other_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other_staff)[0])
            ok(other_client.post(process, {'status': 'COMPLETED', 'note': '第一位客服已交付',
                'expected_status': 'PENDING', 'expected_details': details}, format='json'))
            conflict = staff_client.post(process, {'status': 'COMPLETED', 'note': '第二位客服的说明',
                'expected_status': 'PENDING', 'expected_details': details}, format='json')
            self.assertEqual((conflict.status_code, conflict.data['errorCode']), (409, 'CLAIM_STATUS_CHANGED'))
            self.assertEqual(ok(staff_client.get(f'/api/admin/club-claims/{claim_id}/'))['delivery_note'], '第一位客服已交付')
            return
        ok(boss_client.patch(f'/api/club/claims/{claim_id}/',
            {'details': {'game_uid': 'updated-uid-375', 'note': '变更后的交付要求'}}, format='json'))
        stale = staff_client.post(process, {'status': 'COMPLETED', 'note': '权益已交付',
            'expected_status': 'PENDING', 'expected_details': details}, format='json')
        self.assertEqual((stale.status_code, stale.data['errorCode']), (409, 'CLAIM_DETAILS_CHANGED'))
        self.assertEqual(ok(staff_client.get(f'/api/admin/club-claims/{claim_id}/'))['status'], 'PENDING')
        self.assertEqual(len([item for item in ok(boss_client.get('/api/messages/'))['messages']
                              if item['title'] == '权益交付进度更新']), 0)
        ok(staff_client.post(process, {'status': 'COMPLETED', 'note': '权益已交付'}, format='json'))