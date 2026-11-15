from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from orders.tests.test_reservation_api import ReservationApiTestBase
from users.models import EscortProfile


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TeamConfirmationNoticeTest(ReservationApiTestBase):
    def setUp(self):
        from datetime import timedelta
        from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
        from orders.tests.factories import make_provider, make_service
        from orders.tests.test_reservation_api import slot
        super().setUp()
        from engagement.services import config
        config()
        self.members = [self.provider_account]
        user = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(user)
        link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=user).update(account=account)
        self.members.append(account)
        self.team_service = make_service(price=10000, billing_provider_count=2, price_unit='小时')
        self.start = slot(hour=10)
        self.end = self.start + timedelta(minutes=90)
    def create_team(self):
        from orders.reservation_team import create_team_reservation
        return create_team_reservation(customer_account=self.boss_account, customer=self.boss,
            providers=self.members[:2], service=self.team_service, start_time=self.start, end_time=self.end)

    def test_confirmation_rechecks_each_members_wechat_binding(self):
        for index, member in enumerate(self.members[:2]):
            member.openid = f'team-wechat-{index}'
            member.is_openid_bound = True
            member.save(update_fields=['openid', 'is_openid_bound'])
            EscortProfile.objects.filter(account=member).update(wechat_avatar_legacy_exempt=True)
        booking = self.create_team()
        client = lambda member: APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(member)[0])
        path = f'/api/orders/reservations/{booking.pk}/confirm/'
        self.assertEqual(client(self.members[0]).post(path, {}, format='json').data['code'], 0)
        self.members[1].is_openid_bound = False
        self.members[1].save(update_fields=['is_openid_bound'])
        response = client(self.members[1]).post(path, {}, format='json')
        self.assertNotEqual(response.data['code'], 0)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'PENDING')
        self.assertEqual(list(booking.seats.values_list('status', flat=True)), ['CONFIRMED', 'PENDING'])
        self.members[1].is_openid_bound = True
        self.members[1].save(update_fields=['is_openid_bound'])
        self.assertEqual(client(self.members[1]).post(path, {}, format='json').data['code'], 0)

    @override_settings(PROVIDER_WECHAT_GATE_ENABLED=False)
    def test_either_confirmed_provider_cancelling_notifies_other_participants(self):
        from orders.reservation_team import decide
        from site_messages.models import Message
        for actor_index in (0, 1):
            booking = self.create_team()
            for member in self.members[:2]:
                decide(booking.pk, member, accept=True)
            client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.members[actor_index])[0])
            with self.captureOnCommitCallbacks(execute=True):
                result = client.post(f'/api/orders/reservations/{booking.pk}/cancel/', {}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            recipients = Message.objects.filter(title='预约已取消',
                action_url__endswith=f'id={booking.pk}').values_list('recipient_account_id', flat=True)
            self.assertCountEqual(recipients, [self.boss_account.pk, self.members[1 - actor_index].pk])
