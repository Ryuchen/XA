from django.test import override_settings

from club_accounts.tokens import issue_account_tokens
from orders.models import Reservation
from orders.tests.test_reservation_api import LIST_URL, ReservationApiTestBase
from users.models import EscortProfile


@override_settings(PROVIDER_WECHAT_GATE_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationWechatGateTests(ReservationApiTestBase):
    def login(self, account):
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def test_reservation_rechecks_binding_on_creation_and_provider_confirmation(self):
        self.login(self.boss_account)
        denied = self.client.post(LIST_URL, self.create_payload(), format='json')
        self.assertEqual(denied.data['code'], 400)
        self.assertIn('绑定本人微信', denied.data['msg'])
        self.provider_account.openid = 'reservation-wechat'
        self.provider_account.is_openid_bound = True
        self.provider_account.save(update_fields=['openid', 'is_openid_bound'])
        EscortProfile.objects.filter(account=self.provider_account).update(wechat_avatar_legacy_exempt=True)
        created = self.client.post(LIST_URL, self.create_payload(), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        rid = created.data['data']['id']
        path = f'{LIST_URL}{rid}/'
        self.login(self.staff_account)
        self.assertNotIn('confirm', self.client.get(path).data['data']['allowed_actions'])
        self.assertEqual(self.client.post(path+'confirm/', {}, format='json').status_code, 403)
        self.provider_account.is_openid_bound = False
        self.provider_account.save(update_fields=['is_openid_bound'])
        self.login(self.provider_account)
        blocked = self.client.post(path+'confirm/', {}, format='json')
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(Reservation.objects.get(pk=rid).status, Reservation.Status.PENDING)
        self.provider_account.is_openid_bound = True
        self.provider_account.save(update_fields=['is_openid_bound'])
        self.assertEqual(self.client.post(path+'confirm/', {}, format='json').data['code'], 0)
