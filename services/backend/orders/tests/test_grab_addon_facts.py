"""A paid add-on contributes to the assigned player's ranking amount."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from orders.models import OrderAddonPayment
from orders.grab_original_facts import original_amount_facts
from orders.grab_ranking import SubmittedCandidate, SubmittedMember, build_rankable_candidates, decide
from orders.tests import test_addon_intents as intent_fixture
from orders.tests.test_addon_payment import prepare
from orders.tests.test_addon_offers import authenticated
from orders.tests.test_addon_configuration import staff_client
from orders.tests.factories import make_provider


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class GrabAddonFactsTests(TestCase):
    @override_settings(ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True)
    def test_real_addon_refund_reduces_only_addon_ranking_amount(self):
        fixture = intent_fixture.AddonIntentTests(
            methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        intent_id, wallet = prepare(fixture, inviter=False)
        fixture.provider_user.escort_profile.account = fixture.provider
        fixture.provider_user.escort_profile.save(update_fields=['account'])
        fixture.order.original_amount = 10000
        fixture.order.save(update_fields=['original_amount'])
        paid = fixture.client.post(f'{fixture.url}{intent_id}/pay/', {}, format='json')
        self.assertEqual(paid.status_code, 200, paid.data)
        payment_id = paid.data['data']['payment_id']
        staff = staff_client(['order:view', 'support:edit', 'order:refund'])
        case = staff.post('/api/admin/aftersales/open-negotiation/', {
            'order_id': fixture.order.pk, 'reason': '加购项目协商退款',
            'affected_provider_account_ids': [fixture.provider.pk], 'client_request_id': 'addon-rank-case',
        }, format='json')
        self.assertEqual(case.status_code, 200, case.data)
        staff_url = f'/api/admin/orders/{fixture.order.pk}/addons/payments/{payment_id}/refund-proposals/'
        proposal = staff.post(staff_url, {'request_id':'addon-rank-refund', 'amount_raw':500,
                                          'explanation':'加购服务未完成'}, format='json')
        self.assertEqual(proposal.status_code, 200, proposal.data)
        proposal_id = proposal.data['data']['id']
        user_url = f'/api/orders/orders/{fixture.order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/'
        for client in (fixture.client, fixture.provider_client):
            result = client.post(user_url, {'accepted':True}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
        before = timezone.now()
        result = staff.post(f'{staff_url}{proposal_id}/execute/', {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['data']['refund_executed'])
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 2000)
        self.assertEqual(original_amount_facts([fixture.provider.pk], as_of=before)[fixture.provider.pk].amount, 11500)
        self.assertEqual(original_amount_facts([fixture.provider.pk], as_of=timezone.now())[fixture.provider.pk].amount, 11000)

    def test_paid_addon_is_counted_for_the_assigned_player_and_used_in_ranking(self):
        fixture = intent_fixture.AddonIntentTests(
            methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        intent_id, _ = prepare(fixture, inviter=False)
        player = fixture.provider
        fixture.provider_user.escort_profile.account = player
        fixture.provider_user.escort_profile.save(update_fields=['account'])
        fixture.order.original_amount = 10000
        fixture.order.save(update_fields=['original_amount'])
        other_user = make_provider()
        other, _ = authenticated(other_user)
        other_user.escort_profile.account = other
        other_user.escort_profile.save(update_fields=['account'])
        self.assertEqual(original_amount_facts([player.pk], as_of=timezone.now())[player.pk].amount, 10000)
        result = fixture.client.post(f'{fixture.url}{intent_id}/pay/', {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        payment = OrderAddonPayment.objects.get(pk=result.data['data']['payment_id'])
        before = original_amount_facts([player.pk], as_of=payment.paid_at - timedelta(microseconds=1))[player.pk]
        self.assertEqual(before.amount, 10000)
        now = timezone.now()
        facts = original_amount_facts([player.pk, other.pk], as_of=now)
        self.assertEqual((facts[player.pk].amount, facts[other.pk].amount), (11500, 0))
        candidates = [SubmittedCandidate(1, (SubmittedMember(player.pk, 'GOLD'),), now),
                      SubmittedCandidate(2, (SubmittedMember(other.pk, 'GOLD'),), now)]
        self.assertEqual(decide(build_rankable_candidates(candidates, as_of=now)).winner_id, 1)
        fixture.segment.accepted_at = now - timedelta(days=31)
        fixture.segment.started_at = fixture.segment.accepted_at + timedelta(minutes=1)
        fixture.segment.save(update_fields=['accepted_at', 'started_at'])
        self.assertEqual(original_amount_facts([player.pk], as_of=now)[player.pk].amount, 0)
