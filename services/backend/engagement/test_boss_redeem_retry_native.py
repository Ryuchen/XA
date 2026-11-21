"""A lost redemption response must remain recoverable after stock sells out."""
from contextlib import contextmanager

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import MemberTag, PointLedger, RewardClaim, RewardItem
from orders.tests.factories import make_console_user, make_user
from stores.context import resolve_store, store_context
from stores.db import connection


@contextmanager
def different_event_key_collation():
    if connection.vendor != 'mysql':
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT @@collation_connection')
        original, = cursor.fetchone()
        cursor.execute(
            'SELECT COLLATION_NAME FROM information_schema.COLUMNS '
            'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (%s, %s) AND COLUMN_NAME=%s',
            [MemberTag._meta.db_table, PointLedger._meta.db_table, 'event_key'],
        )
        column_collations = [row[0] for row in cursor.fetchall()]
        assert len(column_collations) == 2
        different = next(name for name in ('utf8mb4_unicode_ci', 'utf8mb4_general_ci', 'utf8mb4_unicode_520_ci')
                         if name not in column_collations)
        cursor.execute('SET SESSION collation_connection = %s', [different])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SET SESSION collation_connection = %s', [original])


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class BossRedemptionRetryNativeTest(LiveServerTestCase):
    def test_response_loss_after_sold_out_redemption(self):
        self.enterContext(store_context(resolve_store()))
        boss, staff = make_user(), make_console_user(['engagement:points', 'engagement:fulfill', 'user:edit', 'user:retire'])
        account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (boss, staff)]
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        staff_client = APIClient(HTTP_HOST='admin.esports.club')
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        credited = staff_client.post('/api/admin/external-points/', {'account_id': account.pk,
            'kind': 'KOOK_CHECKIN', 'day': timezone.localdate().isoformat(), 'source_id': 'native-retry-checkin'}, format='json')
        self.assertEqual(credited.data['code'], 0, credited.data)
        reward = RewardItem.objects.create(code='native-retry-reward', name='最后一份权益', kind='SERVICE',
            audience='BOSS', point_cost=5, in_shop=True, is_active=True, stock=1)
        boss_client = APIClient(HTTP_HOST='sl.esports.club')
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        first = boss_client.post('/api/club/redeem/', {'reward_id': reward.pk,
            'client_request_id': 'native-retry-lost-response'}, format='json')
        self.assertEqual(first.data['code'], 0, first.data)
        retry = boss_client.post('/api/club/redeem/', {'reward_id': reward.pk,
            'client_request_id': 'native-retry-lost-response'}, format='json')
        self.assertEqual(retry.data['code'], 0, retry.data)
        self.assertEqual(retry.data['data']['id'], first.data['data']['id'])
        cancelled = staff_client.post(f'/api/admin/club-claims/{first.data["data"]["id"]}/process/',
            {'status': 'CANCELLED', 'note': '库存损坏，退回积分'}, format='json')
        self.assertEqual(cancelled.data['code'], 0, cancelled.data)
        redeemed = boss_client.post('/api/club/redeem/', {'reward_id': reward.pk,
            'client_request_id': 'retirement-redeem-real'}, format='json')
        self.assertEqual(redeemed.data['code'], 0, redeemed.data)
        reward.refresh_from_db()
        self.assertEqual((reward.stock, reward.redeemed_count), (0, 1))
        self.assertEqual(RewardClaim.objects.filter(account=account, reward=reward).count(), 2)
        client = APIClient(HTTP_HOST='sl.esports.club')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(client.get('/api/club/points/').data['data']['points'], 0)
        claims = list(RewardClaim.objects.filter(account=account, reward=reward).order_by('id'))
        with different_event_key_collation():
            self.assertTrue(client.get(f'/api/club/claims/{claims[0].pk}/').data['data']['points_returned'])
            self.assertFalse(client.get(f'/api/club/claims/{claims[1].pk}/').data['data']['points_returned'])
            rows = client.get('/api/club/claims/?page_size=20').data['data']['items']
        self.assertEqual({row['id']: row['points_returned'] for row in rows},
                         {claims[0].pk: True, claims[1].pk: False})
        messages = client.get('/api/messages/').data['data']['messages']
        self.assertTrue(any('5 积分已退回' in item['preview'] for item in messages))
        preview = staff_client.get(f'/api/admin/users/{account.pk}/retire-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        retired = staff_client.post(f'/api/admin/users/{account.pk}/retire/',
            {'reason': '隔离测试退店', 'confirm': True,
             'confirmation_token': preview.data['data']['confirmation_token']}, format='json')
        self.assertEqual(retired.data['code'], 0, retired.data)
