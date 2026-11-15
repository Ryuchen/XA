"""Two handoffs per seat across pre-service and in-service workflows."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from orders.models import OrderTransferSeat, OrderTransferSeatEvent
from orders.tests import test_prestart_handoff as prestart_fixture
from orders.tests import test_staff_team_dispatch as team_fixture
from orders.tests import test_release_flow as flow


@override_settings(PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TransferLimitApiTest(APITestCase):
    setUp = prestart_fixture.PrestartHandoffTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    replacement = prestart_fixture.PrestartHandoffTest.replacement
    transfer = prestart_fixture.PrestartHandoffTest.transfer

    def test_two_prestart_changes_including_return_then_third_is_rejected(self):
        replacement = self.replacement(); order=self.new_order()
        self.ok(self.transfer(order.pk, self.provider, replacement))
        self.ok(self.transfer(order.pk, replacement, self.provider))
        failed=self.transfer(order.pk, self.provider, replacement)
        self.assertNotEqual(failed.data.get('code'), 0)
        self.assertIn('两次', failed.data['msg'])
        seat=OrderTransferSeat.objects.get(order=order)
        self.assertEqual((seat.transfer_count,seat.current_account_id), (2,self.accounts[self.provider.pk].pk))
        self.assertEqual(list(seat.events.order_by('sequence').values_list('sequence', flat=True)), [1,2])

    def test_prestart_and_service_transfers_share_one_limit(self):
        replacement=self.replacement(); order=self.new_order()
        self.ok(self.transfer(order.pk, self.provider, replacement))
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, replacement, self.provider))
        denied=self.transfer(order.pk, self.provider, replacement)
        self.assertNotEqual(denied.data.get('code'), 0)
        self.assertIn('两次', denied.data['msg'])
        self.assertEqual(OrderTransferSeatEvent.objects.filter(seat__order=order).count(), 2)

    def test_failed_transfer_does_not_consume_allowance(self):
        replacement=self.replacement(); order=self.new_order()
        denied=self.transfer(order.pk, self.provider, replacement, split_value=10)
        self.assertNotEqual(denied.data.get('code'), 0)
        self.assertFalse(OrderTransferSeat.objects.filter(order=order).exists())
        self.ok(self.transfer(order.pk, self.provider, replacement))
        self.assertEqual(OrderTransferSeat.objects.get(order=order).transfer_count, 1)

    def test_mismatched_counter_and_event_history_is_rejected(self):
        replacement=self.replacement(); order=self.new_order()
        self.ok(self.transfer(order.pk,self.provider,replacement))
        OrderTransferSeat.objects.filter(order=order).update(transfer_count=2)
        denied=self.transfer(order.pk,replacement,self.provider)
        self.assertNotEqual(denied.data.get('code'),0)
        self.assertIn('凭证异常',denied.data['msg'])
        self.assertEqual(OrderTransferSeatEvent.objects.filter(seat__order=order).count(),1)


@override_settings(PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TransferLimitDoubleApiTest(APITestCase):
    setUp = team_fixture.StaffTeamDispatchTest.setUp
    auth = team_fixture.StaffTeamDispatchTest.auth
    create = team_fixture.StaffTeamDispatchTest.create
    assign = team_fixture.StaffTeamDispatchTest.assign

    def transfer(self, order, old, new):
        self.auth(self.staff)
        return self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'客服协调更换', 'transfers':[
            {'old_provider_id':self.accounts[old.pk].pk, 'new_provider_id':self.accounts[new.pk].pk}]}, format='json')

    def test_each_seat_has_its_own_two_changes(self):
        order=self.create(); self.assertEqual(self.assign(order).data['code'], 0)
        first, second, replacement=self.providers
        for old,new in ((first,replacement),(replacement,first)):
            result=self.transfer(order,old,new); self.assertEqual(result.data['code'],0,result.data)
        denied=self.transfer(order,first,replacement)
        self.assertNotEqual(denied.data.get('code'),0); self.assertIn('两次',denied.data['msg'])
        for old,new in ((second,replacement),(replacement,second)):
            result=self.transfer(order,old,new); self.assertEqual(result.data['code'],0,result.data)
        seats=list(OrderTransferSeat.objects.filter(order=order).order_by('slot'))
        self.assertEqual([seat.transfer_count for seat in seats],[2,2])
        self.assertEqual(OrderTransferSeatEvent.objects.filter(seat__order=order).count(),4)


@skipUnlessDBFeature('has_select_for_update')
@override_settings(PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TransferLimitRaceTest(APITransactionTestCase):
    setUp = prestart_fixture.PrestartHandoffTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    replacement = prestart_fixture.PrestartHandoffTest.replacement
    transfer = prestart_fixture.PrestartHandoffTest.transfer

    def test_competing_second_handoffs_only_one_commits(self):
        from club_accounts.tokens import issue_account_tokens
        first=self.replacement('第一名'); second=self.replacement('第二名'); order=self.new_order()
        self.ok(self.transfer(order.pk,self.provider,first))
        staff_token=issue_account_tokens(self.accounts[self.admin.pk])[0]
        old_id=self.accounts[first.pk].pk
        alternatives=[self.accounts[self.provider.pk].pk,self.accounts[second.pk].pk]
        barrier=Barrier(2)
        def run(new_id):
            close_old_connections()
            try:
                client=APIClient(HTTP_AUTHORIZATION='Bearer '+staff_token)
                barrier.wait(timeout=10)
                result=client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'并发转单', 'transfers':[
                    {'old_provider_id':old_id,'new_provider_id':new_id}]},format='json')
                return result.data.get('code')
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,alternatives))
        self.assertEqual(results.count(0),1)
        self.assertEqual(OrderTransferSeat.objects.get(order=order).transfer_count,2)
        self.assertEqual(OrderTransferSeatEvent.objects.filter(seat__order=order).count(),2)
