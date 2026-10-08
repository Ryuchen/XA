"""菜单表单对应的真实增改查删、错误提示和业务记录联动。"""
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.permissions import ALL_PERMISSIONS
from console.models import AdminRole, AdminMembership, AdminAuditLog
from banners.models import Banner
from chat.models import ChatSession, ChatMessage
from audition.models import AuditionLink, AuditionSignup
from orders.tests.factories import make_console_user, make_user, make_service, make_order, make_coupon
from coupons.models import Coupon, UserCoupon
from site_messages.models import Message
from wallet.models import Wallet, Transaction
from users.models import CheckinGift


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class MenuOperationsTest(APITestCase):
    def setUp(self):
        staff = make_console_user(ALL_PERMISSIONS)
        self.staff = get_or_create_account_for_legacy_user(staff)
        membership = staff.admin_membership
        membership.account = self.staff
        membership.save(update_fields=['account'])
        token, _ = issue_account_tokens(self.staff)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)

    def ok(self, response):
        self.assertIn(response.status_code, (200, 201), getattr(response, 'data', ''))
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def test_configuration_forms_create_edit_read_and_delete(self):
        future = (timezone.now() + timedelta(days=30)).isoformat()
        past = (timezone.now() - timedelta(hours=1)).isoformat()
        forms = [
            ('boss-types', {'name': '菜单老板分级', 'discount_rate': 90}, 'name'),
            ('escort-levels', {'name': '菜单陪玩等级', 'commission_rate': 20}, 'name'),
            ('game-categories', {'name': '菜单游戏'}, 'name'),
            ('service-categories', {'name': '菜单分类', 'is_gift': False}, 'name'),
            ('service-items', {'name': '菜单服务', 'price': 10000}, 'name'),
            ('promotions', {'title': '菜单活动', 'scope': 'ALL', 'discount_rate': 80, 'start_at': past, 'end_at': future}, 'title'),
            ('coupons', {'name': '菜单优惠券', 'discount_type': 'THRESHOLD', 'threshold': 10000, 'amount': 1000, 'valid_to': future}, 'name'),
            ('announcements', {'title': '菜单公告', 'content': '内容'}, 'title'),
            ('achievements', {'code': 'menu-achievement', 'title': '菜单成就', 'metric': 'orders', 'target': 2}, 'title'),
            ('checkin-gifts', {'checkin_day': 2, 'name': '菜单签到礼物', 'reward_amount': 100}, 'name'),
            ('support-cards', {'name': '菜单客服', 'wecom_corp_id': 'wwtest', 'wecom_service_url': 'https://work.weixin.qq.com/kfid/test'}, 'name'),
            ('audition-links', {'title': '菜单试音', 'expire_at': future}, 'title'),
            ('roles', {'code': 'menu-role', 'name': '菜单角色', 'permissions': ['order:view']}, 'name'),
        ]
        for resource, payload, label in forms:
            with self.subTest(menu=resource):
                base = f'/api/admin/{resource}/'
                if resource == 'checkin-gifts':
                    for gift in CheckinGift.objects.filter(checkin_day=payload['checkin_day']):
                        self.ok(self.client.delete(f'{base}{gift.pk}/'))
                created = self.ok(self.client.post(base, payload, format='json'))
                url = f"{base}{created['id']}/"
                self.ok(self.client.patch(url, {label: '已修改菜单配置'}, format='json'))
                self.assertEqual(self.ok(self.client.get(url))[label], '已修改菜单配置')
                self.ok(self.client.delete(url))
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_used_service_delete_is_explained_without_removing_order(self):
        service = make_service()
        order = make_order(self.boss, service=service)
        response = self.client.delete(f'/api/admin/service-items/{service.pk}/')
        self.assertIn(response.status_code, (400, 409))
        order.refresh_from_db()
        self.assertEqual(order.service_id, service.pk)

    def test_invalid_coupon_amount_and_stock_are_rejected_before_write(self):
        future = (timezone.now() + timedelta(days=3)).isoformat()
        response = self.client.post('/api/admin/coupons/', {'name': '无效券', 'amount': 0, 'valid_to': future}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Coupon.objects.filter(name='无效券').exists())
        coupon = make_coupon(total_qty=5, claimed_qty=3)
        response = self.client.patch(f'/api/admin/coupons/{coupon.pk}/', {'total_qty': 2}, format='json')
        self.assertEqual(response.status_code, 400)
        coupon.refresh_from_db()
        self.assertEqual(coupon.total_qty, 5)

    def test_wallet_adjustment_is_once_only_and_bound_to_boss_wallet(self):
        wallet, _ = Wallet.objects.get_or_create(user=self.boss, defaults={'account': self.boss_account})
        wallet.balance = 1000
        wallet.save(update_fields=['balance'])
        url = f'/api/admin/wallets/{wallet.pk}/adjust/'
        payload = {'amount': 200, 'remark': '误充纠正', 'client_request_id': 'menu-adjust-once'}
        self.ok(self.client.post(url, payload, format='json'))
        self.assertEqual(self.client.post(url, payload, format='json').status_code, 409)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 1200)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='ADJUST').count(), 1)
        self.assertEqual(self.client.post(url, {'amount': 200}, format='json').status_code, 400)
        from orders.tests.factories import make_provider
        provider_wallet, _ = Wallet.objects.get_or_create(user=make_provider())
        self.assertEqual(self.client.post(f'/api/admin/wallets/{provider_wallet.pk}/adjust/', {**payload, 'client_request_id': 'wrong-wallet'}, format='json').status_code, 404)

    @patch('console.views._schedule_auto_cancel')
    def test_dispatch_and_cancel_fund_records_identify_actual_operator(self, _schedule):
        wallet, _ = Wallet.objects.get_or_create(user=self.boss, defaults={'account': self.boss_account})
        wallet.balance = 10000
        wallet.save(update_fields=['balance'])
        service = make_service(price=8000)
        order = self.ok(self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': self.boss_account.pk, 'service_id': service.pk, 'game_rounds': 1,
            'client_request_id': 'menu-dispatch-trace',
        }, format='json'))
        self.ok(self.client.post(f"/api/admin/orders/{order['id']}/cancel/", {'reason': '验收取消'}, format='json'))
        records = list(Transaction.objects.filter(order_id=order['id']).order_by('id'))
        self.assertEqual([row.tx_type for row in records], ['PAY', 'REFUND'])
        self.assertTrue(all(row.operator_account_id == self.staff.pk for row in records))
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 10000)

    def test_claimed_coupon_is_retained_and_can_be_disabled(self):
        coupon = make_coupon()
        claim = UserCoupon.objects.create(user=self.boss, account=self.boss_account, coupon=coupon)
        response = self.client.delete(f'/api/admin/coupons/{coupon.pk}/')
        self.assertIn(response.status_code, (400, 409))
        self.assertTrue(UserCoupon.objects.filter(pk=claim.pk).exists())
        self.ok(self.client.patch(f'/api/admin/coupons/{coupon.pk}/', {'is_active': False}, format='json'))
        records = self.ok(self.client.get('/api/admin/user-coupons/', {'coupon': coupon.pk}))
        self.assertEqual(records['total'], 1)

    def test_coupon_claim_records_report_expired_unused_coupons(self):
        coupon = make_coupon(valid_to=timezone.now() - timedelta(days=1))
        UserCoupon.objects.create(user=self.boss, account=self.boss_account, coupon=coupon)
        expired = self.ok(self.client.get('/api/admin/user-coupons/', {'status': 'EXPIRED'}))
        self.assertEqual(expired['total'], 1)
        self.assertEqual(expired['list'][0]['status_display'], '已过期')
        self.assertEqual(expired['list'][0]['user_name'], self.boss_account.nickname)
        self.assertEqual(self.ok(self.client.get('/api/admin/user-coupons/', {'status': 'UNUSED'}))['total'], 0)

    def test_message_recipient_search_and_targeted_delivery_use_business_account(self):
        result = self.ok(self.client.get('/api/admin/options/message-recipients/', {'keyword': self.boss_account.account_no}))
        self.assertEqual([row['id'] for row in result['list']], [self.boss_account.pk])
        self.assertNotIn('wallet_balance', result['list'][0])
        sent = self.ok(self.client.post('/api/admin/messages/push/', {
            'recipient_account_ids': [self.boss_account.pk], 'title': '功能验收通知', 'detail': '完整消息内容',
        }, format='json'))
        self.assertEqual(sent['count'], 1)
        message = Message.objects.get(title='功能验收通知')
        self.assertEqual(message.recipient_account_id, self.boss_account.pk)
        self.assertEqual(self.ok(self.client.get(f'/api/admin/messages/{message.pk}/'))['detail'], '完整消息内容')

    def test_invalid_message_type_or_recipient_does_not_partially_send(self):
        for payload in ({'type': 'UNKNOWN', 'recipient_account_ids': [self.boss_account.pk]},
                        {'recipient_account_ids': [self.boss_account.pk, 99999999]}):
            with self.subTest(payload=payload):
                response = self.client.post('/api/admin/messages/push/', {'title': '不应发送', **payload}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertFalse(Message.objects.filter(title='不应发送').exists())

    def test_invalid_roles_do_not_partially_save_staff_profile(self):
        role = AdminRole.objects.create(code='operator_test', name='功能客服', permissions=['order:view'])
        row = self.ok(self.client.post('/api/admin/admins/', {
            'username': 'menu_staff', 'password': 'Menu-pass-928!', 'nickname': '原昵称', 'roles': [role.pk],
        }, format='json'))
        url = f"/api/admin/admins/{row['id']}/"
        response = self.client.patch(url, {'nickname': '不应保存', 'roles': [99999999]}, format='json')
        self.assertEqual(response.status_code, 400)
        membership = AdminMembership.objects.get(pk=row['id'])
        self.assertEqual(membership.account.nickname, '原昵称')
        self.assertEqual(membership.user.nickname, '原昵称')
        self.ok(self.client.patch(url, {'nickname': '新昵称', 'is_active': False}, format='json'))
        membership.refresh_from_db()
        self.assertFalse(membership.is_active)
        self.assertEqual(membership.account.nickname, '新昵称')
        self.ok(self.client.delete(url))
        self.assertFalse(AdminMembership.objects.filter(pk=membership.pk).exists())

    def test_role_permissions_and_builtin_role_protection(self):
        for permissions in ({'order:view': True}, ['missing:action'], [1]):
            response = self.client.post('/api/admin/roles/', {
                'code': 'bad_role', 'name': '不应创建', 'permissions': permissions,
            }, format='json')
            self.assertEqual(response.status_code, 400)
        role, _ = AdminRole.objects.get_or_create(code='super_admin', defaults={'name': '超级管理员', 'permissions': ALL_PERMISSIONS})
        url = f'/api/admin/roles/{role.pk}/'
        for payload in ({'code': 'renamed'}, {'permissions': []}, {'is_active': False}):
            self.assertEqual(self.client.patch(url, payload, format='json').status_code, 400)
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.ok(self.client.patch(url, {'description': '维护说明'}, format='json'))

    def test_promotion_requires_scope_target(self):
        payload = {'title': '限定活动', 'discount_rate': 80,
                   'start_at': timezone.now().isoformat(), 'end_at': (timezone.now() + timedelta(days=3)).isoformat()}
        for scope in ['CATEGORY', 'ITEMS']:
            self.assertEqual(self.client.post('/api/admin/promotions/', {**payload, 'scope': scope}, format='json').status_code, 400)
        service = make_service()
        promotion = self.ok(self.client.post('/api/admin/promotions/', {**payload, 'scope': 'ITEMS', 'items': [service.pk]}, format='json'))
        self.assertEqual(self.client.patch(f"/api/admin/promotions/{promotion['id']}/", {'items': []}, format='json').status_code, 400)

    def test_banner_upload_link_and_public_visibility(self):
        def image_file(size=(750, 320)):
            output = BytesIO()
            Image.new('RGB', size, '#224466').save(output, format='PNG')
            return SimpleUploadedFile('menu-test.png', output.getvalue(), content_type='image/png')
        with TemporaryDirectory(prefix='xa-menu-media-') as media, override_settings(MEDIA_ROOT=media):
            self.assertEqual(self.client.post('/api/admin/banners/', {'title': '缺少图片'}, format='json').status_code, 400)
            self.assertEqual(self.client.post('/api/admin/banners/', {'image': image_file((100, 100))}, format='multipart').status_code, 400)
            service = make_service()
            banner = self.ok(self.client.post('/api/admin/banners/', {
                'image': image_file(), 'title': '商品轮播', 'is_active': 'true', 'link_type': 'PRODUCT', 'link_value': str(service.pk),
            }, format='multipart'))
            self.assertTrue(banner['image_url'])
            public_rows = self.ok(self.client.get('/api/banners/'))
            self.assertIn(banner['id'], [row['id'] for row in public_rows])
            url = f"/api/admin/banners/{banner['id']}/"
            self.assertEqual(self.client.patch(url, {'link_value': '99999999'}, format='json').status_code, 400)
            self.ok(self.client.patch(url, {'is_active': False}, format='json'))
            self.assertNotIn(banner['id'], [row['id'] for row in self.ok(self.client.get('/api/banners/'))])
            self.ok(self.client.delete(url))
            self.assertFalse(Banner.objects.filter(pk=banner['id']).exists())

    def test_chat_pagination_reply_and_read_permission(self):
        for index in range(21):
            user = make_user()
            ChatSession.objects.create(user=user, account=get_or_create_account_for_legacy_user(user), unread_support=1)
        first = self.ok(self.client.get('/api/admin/chat-sessions/', {'page_size': 20}))
        second = self.ok(self.client.get('/api/admin/chat-sessions/', {'page_size': 20, 'page': 2}))
        self.assertEqual(first['total'], 21)
        self.assertEqual(len(second['list']), 1)
        session_id = second['list'][0]['id']
        url = f'/api/admin/chat-sessions/{session_id}/'
        reply = self.ok(self.client.post(url + 'reply/', {'content': '客服回复验收'}, format='json'))
        self.assertEqual(reply['content'], '客服回复验收')
        self.assertEqual(ChatMessage.objects.get(pk=reply['id']).sender_account_id, self.staff.pk)
        self.assertEqual(self.ok(self.client.get(url + 'messages/'))['total'], 1)
        for role in self.staff.admin_membership.roles.all():
            role.permissions = ['chat:view']
            role.save(update_fields=['permissions'])
        self.assertEqual(self.client.get(url + 'messages/').status_code, 200)
        self.assertEqual(self.client.post(url + 'read/').status_code, 403)
        self.assertEqual(self.client.post(url + 'reply/', {'content': '拒绝'}, format='json').status_code, 403)

    def test_chat_invalid_content_and_missing_session_are_explained(self):
        session = ChatSession.objects.create(user=self.boss, account=self.boss_account)
        self.assertEqual(self.client.post(f'/api/admin/chat-sessions/{session.pk}/reply/', {'content': '字' * 1001}, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/admin/chat-sessions/99999999/reply/', {'content': '测试'}, format='json').status_code, 404)
        self.assertEqual(self.client.post('/api/admin/chat-sessions/99999999/read/').status_code, 404)

    def test_audition_audit_notifies_applicant_and_preserves_records(self):
        from orders.tests.factories import make_provider
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        link = AuditionLink.objects.create(title='待审核试音')
        signup = AuditionSignup.objects.create(link=link, applicant=provider, applicant_account=account)
        self.ok(self.client.post(f'/api/admin/audition-signups/{signup.pk}/approve/'))
        signup.refresh_from_db()
        self.assertEqual(signup.status, 'APPROVED')
        self.assertEqual(signup.auditor_account_id, self.staff.pk)
        self.assertTrue(Message.objects.filter(recipient_account=account, title='试音报名通过').exists())
        self.assertNotEqual(self.client.post(f'/api/admin/audition-signups/{signup.pk}/approve/').data['code'], 0)
        self.assertEqual(self.client.delete(f'/api/admin/audition-links/{link.pk}/').status_code, 400)
        self.ok(self.client.patch(f'/api/admin/audition-links/{link.pk}/', {'is_active': False}, format='json'))
        self.assertTrue(AuditionSignup.objects.filter(pk=signup.pk).exists())

    def test_admin_checkin_and_announcement_config_reaches_boss(self):
        self.ok(self.client.put('/api/admin/config/checkin', {
            'daily_spend_required': 500, 'makeup_card_spend_required': 1000, 'max_makeup_cards': 2,
        }, format='json'))
        gift = CheckinGift.objects.filter(checkin_day=1).first()
        payload = {'checkin_day': 1, 'name': '首签礼物', 'reward_amount': 230}
        self.ok(self.client.patch(f'/api/admin/checkin-gifts/{gift.pk}/', payload, format='json') if gift
                else self.client.post('/api/admin/checkin-gifts/', payload, format='json'))
        announcement = self.ok(self.client.post('/api/admin/announcements/', {'title': '新公告', 'content': '完整公告正文'}, format='json'))
        client = APIClient()
        token, _ = issue_account_tokens(self.boss_account)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        progress = self.ok(client.get('/api/users/checkin/'))
        self.assertEqual(progress['daily_spend_required'], 500)
        self.assertEqual(progress['max_makeup_cards'], 2)
        self.assertEqual(progress['next_reward'], 230)
        self.assertEqual(self.ok(client.get(f"/api/announcements/{announcement['id']}/"))['content'], '完整公告正文')
        self.ok(self.client.patch(f"/api/admin/announcements/{announcement['id']}/", {'is_active': False}, format='json'))
        self.assertNotIn(announcement['id'], [row['id'] for row in self.ok(client.get('/api/announcements/'))])

    def test_audit_tracks_write_operator_and_masks_password(self):
        role = AdminRole.objects.create(code='audit_staff', name='审计客服', permissions=['order:view'])
        self.ok(self.client.post('/api/admin/admins/', {
            'username': 'audit_staff', 'password': 'Menu-secret-928!', 'roles': [role.pk],
        }, format='json'))
        record = AdminAuditLog.objects.get(resource='admins', method='POST')
        self.assertEqual(record.operator_account_id, self.staff.pk)
        self.assertEqual(record.request_body['password'], '***')
        self.assertNotIn('Menu-secret-928!', str(self.ok(self.client.get('/api/admin/audit-logs/'))))
