import os
from types import SimpleNamespace
from unittest.mock import patch
from django.test import override_settings
from rest_framework.test import APITransactionTestCase
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from unittest import skipUnless

from common.private_media import may_read
from orders.models import Order, OrderParticipationSegment
from orders.serializers import OrderSerializer
from orders.tests import test_prestart_handoff as fixture
from orders.tests import test_release_flow as flow
from orders.tests import test_staff_team_dispatch as team_fixture
from wallet.models import ProviderReport, Transaction


class TransferEntryActionTest(SimpleTestCase):
    def test_paid_points_transfer_entry_does_not_require_a_report_snapshot(self):
        account = SimpleNamespace(pk=7, account_type='PROVIDER')
        request = SimpleNamespace(account=account, legacy_user=SimpleNamespace(pk=8))
        serializer = OrderSerializer(context={'request': request})
        order = SimpleNamespace(status=Order.Status.IN_SERVICE, provider_account_id=account.pk,
                                payment_status=Order.PaymentStatus.PAID)
        segment = SimpleNamespace(attribution=SimpleNamespace(status='ACTIVE'), is_service_transfer=True,
                                  released_at=None, started_at=None)
        assignment = SimpleNamespace(provider_id=8, provider_account_id=account.pk, released_at=None)
        with (patch.object(serializer, '_has_points_evidence', return_value=True),
              patch('orders.points_checkout_test.assignment_enabled', return_value=False),
              patch.object(serializer, '_my_participation', return_value=[segment]) as participation,
              patch.object(serializer, '_requester_assignment', return_value=assignment),
              patch.object(serializer, '_points_fulfillment_available', return_value=False)):
            self.assertEqual(serializer.get_allowed_actions(order), ['START_PARTICIPATION'])
            participation.return_value.append(SimpleNamespace(attribution=segment.attribution,
                is_service_transfer=False, released_at=None, started_at=object()))
            self.assertEqual(serializer.get_allowed_actions(order), [])
            participation.return_value.pop()
            assignment.provider_id = 10
            self.assertEqual(serializer.get_allowed_actions(order), [])
            assignment.provider_id = 8
            order.payment_status = Order.PaymentStatus.PARTIALLY_REFUNDED
            self.assertEqual(serializer.get_allowed_actions(order), [])


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TransferParticipationEntryTest(APITransactionTestCase):
    setUp = fixture.PrestartHandoffTest.setUp
    login = fixture.PrestartHandoffTest.login
    ok = fixture.PrestartHandoffTest.ok
    recharge = fixture.PrestartHandoffTest.recharge
    new_order = fixture.PrestartHandoffTest.new_order
    replacement = fixture.PrestartHandoffTest.replacement
    transfer = fixture.PrestartHandoffTest.transfer

    def prepare_transfer(self):
        replacement = self.replacement()
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                                  {'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained': 4000, 'transferred': 6000, 'agreement': '双方确认原价份额'}))
        segment = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        return order, replacement, segment

    def entry(self, order, segment, request_id='transfer-entry'):
        return self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': segment.pk, 'entry_image': flow.screenshot(),
             'client_request_id': request_id}, format='multipart')

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '转单参与入场/证据/回放被转入陪玩抽成快照能力闸门拦截属正式契约深水区（18-盘点 转单/逐段结算待联调）；最终联调启用')
    def test_only_current_provider_can_record_entry_and_replay(self):
        order, replacement, segment = self.prepare_transfer()
        count = Transaction.objects.filter(order=order).count()
        self.login(replacement)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertIn('START_PARTICIPATION', detail['allowed_actions'])
        self.assertNotIn('COMPLETE', detail['allowed_actions'])
        self.assertEqual([row['id'] for row in detail['my_participation_segments']], [segment.pk])
        blocked = self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                                   {'completion_image': flow.screenshot()}, format='multipart')
        self.assertNotEqual(blocked.data['code'], 0)
        self.login(self.provider)
        self.assertNotEqual(self.entry(order, segment, 'other-provider').data['code'], 0)
        self.login(self.admin)
        self.assertNotEqual(self.entry(order, segment, 'admin-entry').data['code'], 0)
        blocked_admin = self.client.post(f'/api/admin/orders/{order.pk}/complete/', {}, format='json')
        self.assertNotEqual(blocked_admin.data['code'], 0)
        segment.refresh_from_db(); self.assertIsNone(segment.started_at)
        self.login(replacement)
        self.ok(self.entry(order, segment))
        segment.refresh_from_db()
        started = segment.started_at
        self.assertEqual(segment.start_source, 'TRANSFER_ENTRY')
        self.assertTrue(segment.entry_image)
        report = ProviderReport.objects.get(order=order, provider=replacement)
        self.assertEqual(report.entry_image.name, segment.entry_image.name)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertNotIn('START_PARTICIPATION', detail['allowed_actions'])
        self.assertIn('COMPLETE', detail['allowed_actions'])
        self.assertTrue(detail['my_participation_segments'][0]['entry_image_url'])
        self.assertTrue(may_read(self.accounts[replacement.pk], segment))
        self.assertFalse(may_read(self.accounts[self.provider.pk], segment))
        self.assertFalse(may_read(self.accounts[self.boss.pk], segment))
        self.assertTrue(self.ok(self.entry(order, segment))['request_replayed'])
        segment.refresh_from_db(); self.assertEqual(segment.started_at, started)
        self.assertEqual(Transaction.objects.filter(order=order).count(), count)

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '转单参与入场/证据/回放被转入陪玩抽成快照能力闸门拦截属正式契约深水区（18-盘点 转单/逐段结算待联调）；最终联调启用')
    def test_returning_provider_gets_new_entry_without_overwriting_old_report(self):
        order, replacement, segment = self.prepare_transfer()
        self.login(replacement); self.ok(self.entry(order, segment))
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        previous_image = report.entry_image.name
        first = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.provider.pk], released_at__isnull=False)
        self.assertEqual(first.entry_image.name, previous_image)
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained': 2000, 'transferred': 4000, 'agreement': '协商转回'}))
        incoming = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.provider.pk], released_at__isnull=True)
        self.login(self.provider)
        rows = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['my_participation_segments']
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(self.entry(order, segment, 'stale-segment').data['code'], 0)
        self.ok(self.entry(order, incoming, 'return-entry'))
        incoming.refresh_from_db(); report.refresh_from_db()
        self.assertTrue(incoming.entry_image)
        self.assertNotEqual(incoming.entry_image.name, previous_image)
        self.assertEqual(report.entry_image.name, previous_image)
        completed = self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image':flow.screenshot()}, format='multipart')
        self.assertEqual(completed.data['code'], 0, completed.data)
        incoming.refresh_from_db(); first.refresh_from_db()
        self.assertTrue(incoming.completion_image)
        self.assertFalse(first.completion_image)
        self.assertEqual(first.entry_image.name, previous_image)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        own = {row['id']:row for row in detail['my_participation_segments']}
        self.assertTrue(own[first.pk]['entry_image_url'])
        self.assertFalse(own[first.pk]['completion_image_url'])
        self.assertTrue(own[incoming.pk]['entry_image_url'])
        self.assertTrue(own[incoming.pk]['completion_image_url'])
        self.assertFalse(may_read(self.accounts[replacement.pk], incoming))
        from urllib.parse import urlsplit
        response = self.client.get(urlsplit(own[incoming.pk]['completion_image_url']).path)
        self.assertEqual(response.status_code, 200); response.close()
        report.refresh_from_db(); latest_completion = report.completion_image.name
        evidence = self.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
            {'segment_id':first.pk, 'completion_image':flow.screenshot()}, format='multipart')
        self.assertEqual(evidence.data['code'], 0, evidence.data)
        first.refresh_from_db(); incoming.refresh_from_db(); report.refresh_from_db()
        self.assertTrue(first.completion_image)
        self.assertNotEqual(first.completion_image.name, incoming.completion_image.name)
        self.assertEqual(report.completion_image.name, latest_completion)
        self.login(self.admin)
        admin = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))
        admin_segments = {row['id']:row for row in admin['original_attribution']['segments']}
        self.assertTrue(admin_segments[incoming.pk]['completion_image_url'])
        self.assertTrue(admin_segments[first.pk]['entry_image_url'])
        self.assertTrue(admin_segments[first.pk]['completion_image_url'])
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user
        no_report = make_console_user(['order:view'])
        no_report_account = get_or_create_account_for_legacy_user(no_report)
        no_report.admin_membership.account = no_report_account
        no_report.admin_membership.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(no_report_account)[0])
        limited = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))
        limited_segments = {row['id']:row for row in limited['original_attribution']['segments']}
        self.assertFalse(limited_segments[incoming.pk]['completion_image_url'])
        self.assertFalse(limited_segments[first.pk]['entry_image_url'])

    def test_incomplete_segment_prevents_attaching_completion_to_wrong_entry(self):
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        segment = OrderParticipationSegment.objects.get(attribution__order=order)
        OrderParticipationSegment.objects.filter(pk=segment.pk).update(started_at=None)
        denied = self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image':flow.screenshot()}, format='multipart')
        self.assertNotEqual(denied.data['code'], 0)
        order.refresh_from_db(); segment.refresh_from_db()
        self.assertEqual(order.status, 'IN_SERVICE')
        self.assertFalse(segment.completion_image)

    @override_settings(SEGMENT_ECONOMICS_CAPTURE_ENABLED=True)
    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '转单参与入场/证据/回放被转入陪玩抽成快照能力闸门拦截属正式契约深水区（18-盘点 转单/逐段结算待联调）；最终联调启用')
    def test_released_provider_can_attach_private_evidence_to_exact_segment(self):
        order, replacement, _ = self.prepare_transfer()
        old = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.provider.pk], released_at__isnull=False)
        url = f'/api/orders/orders/{order.pk}/participation-evidence/'
        self.login(replacement)
        denied = self.client.post(url, {'segment_id':old.pk, 'completion_image':flow.screenshot()}, format='multipart')
        self.assertNotEqual(denied.data['code'], 0)
        old.refresh_from_db(); self.assertFalse(old.completion_image)
        self.login(self.provider)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertTrue(detail['my_participation_segments'][0]['can_upload_completion'])
        def upload(request_id='old-segment-evidence'):
            return self.client.post(url, {'segment_id':old.pk, 'completion_image':flow.screenshot(),
                                         'client_request_id':request_id}, format='multipart')
        result = self.ok(upload())
        self.assertTrue(result['my_participation_segments'][0]['completion_image_url'])
        old.refresh_from_db(); saved = old.completion_image.name
        self.assertTrue(saved)
        report = ProviderReport.objects.get(order=order, provider_account=self.accounts[self.provider.pk])
        self.assertEqual(report.completion_image.name, saved)
        self.assertTrue(self.ok(upload())['request_replayed'])
        old.refresh_from_db(); self.assertEqual(old.completion_image.name, saved)
        conflict = self.client.post(url, {'segment_id':old.pk + 1000, 'completion_image':flow.screenshot(),
            'client_request_id':'old-segment-evidence'}, format='multipart')
        self.assertEqual(conflict.status_code, 409)
        self.ok(upload('replace-old-segment-evidence'))
        old.refresh_from_db(); report.refresh_from_db()
        self.assertNotEqual(old.completion_image.name, saved)
        self.assertEqual(report.completion_image.name, old.completion_image.name)
        saved = old.completion_image.name
        report.status = ProviderReport.Status.PENDING; report.save(update_fields=['status'])
        self.assertNotEqual(upload('locked-report-evidence').data['code'], 0)
        old.refresh_from_db(); self.assertEqual(old.completion_image.name, saved)


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TransferTeamEntryTest(APITransactionTestCase):
    setUp = team_fixture.StaffTeamDispatchTest.setUp
    auth = team_fixture.StaffTeamDispatchTest.auth
    create = team_fixture.StaffTeamDispatchTest.create
    assign = team_fixture.StaffTeamDispatchTest.assign

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '转单参与入场/证据/回放被转入陪玩抽成快照能力闸门拦截属正式契约深水区（18-盘点 转单/逐段结算待联调）；最终联调启用')
    def test_teammate_can_complete_while_incoming_provider_must_first_enter(self):
        self.service.price = 5000; self.service.save(update_fields=['price'])
        order = self.create(); self.assertEqual(self.assign(order).data['code'], 0)
        for provider in self.providers[:2]:
            self.auth(provider)
            result = self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image': team_fixture.screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[self.providers[0].pk].pk,
            'new_provider_id':self.accounts[self.providers[2].pk].pk,
            'split_type':'PERCENT', 'split_value':50, 'penalty_amount':0,
            'original_share':{'retained':2000, 'transferred':3000, 'agreement':'双方确认分配'}}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        incoming = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.providers[2].pk], released_at__isnull=True)
        self.auth(self.providers[2])
        result = self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image':team_fixture.screenshot()}, format='multipart')
        self.assertNotEqual(result.data['code'], 0)
        self.auth(self.providers[1])
        detail = self.client.get(f'/api/orders/orders/{order.pk}/').data['data']
        self.assertNotIn(incoming.pk, [row['id'] for row in detail['my_participation_segments']])
        result = self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image':team_fixture.screenshot()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        teammate = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.providers[1].pk], released_at__isnull=True)
        self.assertTrue(teammate.entry_image); self.assertTrue(teammate.completion_image)
        self.auth(self.providers[2])
        result = self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':incoming.pk, 'entry_image':team_fixture.screenshot()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        result = self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image':team_fixture.screenshot()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        incoming.refresh_from_db(); teammate.refresh_from_db()
        self.assertTrue(incoming.completion_image)
        self.assertNotEqual(incoming.completion_image.name, teammate.completion_image.name)


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True, SEGMENT_REFUND_PREVIEW_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TransferParticipationStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '转单参与入场/证据/回放被转入陪玩抽成快照能力闸门拦截属正式契约深水区（18-盘点 转单/逐段结算待联调）；最终联调启用')
    def test_equal_identifiers_do_not_allow_cross_store_entry_or_image(self):
        from tempfile import TemporaryDirectory
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user, make_provider, make_service, make_user
        from rest_framework.test import APIClient
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.services import get_wallet
        stores = ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club'))
        facts = []
        with TemporaryDirectory(prefix='xa-entry-stores-') as media, override_settings(MEDIA_ROOT=media):
            for store, host in stores:
                with store_context(store):
                    boss = make_user(); boss_account = get_or_create_account_for_legacy_user(boss)
                    wallet = get_wallet(account=boss_account, user=boss)
                    wallet.balance = 3000; wallet.save(update_fields=['balance'])
                    players = [make_provider(deposit_mode='EARNINGS') for _ in range(2)]
                    accounts = [get_or_create_account_for_legacy_user(player) for player in players]
                    for player, account in zip(players, accounts):
                        player.escort_profile.account = account; player.escort_profile.save(update_fields=['account'])
                    staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit', 'order:refund'])
                    operator = get_or_create_account_for_legacy_user(staff)
                    staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
                    service = make_service(price=1000)
                    def client(account):
                        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                    result = client(boss_account).post('/api/orders/create/', {'service_id':service.pk,
                        'provider_id':accounts[0].pk, 'client_request_id':'store-entry-order'}, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                    order_id = result.data['data']['id']
                    result = client(accounts[0]).post(f'/api/orders/orders/{order_id}/start/',
                        {'entry_image':flow.screenshot()}, format='multipart')
                    self.assertEqual(result.data['code'], 0, result.data)
                    result = client(operator).post(f'/api/admin/orders/{order_id}/transfer/',
                        {'reason':'双方协商', 'transfers':[{'old_provider_id':accounts[0].pk,
                            'new_provider_id':accounts[1].pk,
                            'original_share':{'retained':400, 'transferred':600, 'agreement':'双方确认'}}]}, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                    segment = OrderParticipationSegment.objects.get(attribution__order_id=order_id,
                        provider_account_id=accounts[1].pk, released_at__isnull=True)
                    previous = OrderParticipationSegment.objects.get(attribution__order_id=order_id,
                        provider_account_id=accounts[0].pk, released_at__isnull=False)
                    facts.append({'order':order_id, 'segment':segment.pk, 'account':accounts[1].pk,
                                  'token':issue_account_tokens(accounts[1])[0], 'previous':previous.pk,
                                  'previous_token':issue_account_tokens(accounts[0])[0],
                                  'staff_token':issue_account_tokens(operator)[0]})
            self.assertEqual([(x['order'], x['segment'], x['account']) for x in facts],
                             [(facts[0]['order'], facts[0]['segment'], facts[0]['account'])] * 2)
            first, second = facts
            foreign = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+first['token'])
            result = foreign.post(f"/api/orders/orders/{second['order']}/participation-start/",
                {'segment_id':second['segment'], 'entry_image':flow.screenshot()}, format='multipart')
            self.assertNotEqual(result.data.get('code'), 0)
            own = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+first['token'])
            result = own.post(f"/api/orders/orders/{first['order']}/participation-start/",
                {'segment_id':first['segment'], 'entry_image':flow.screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            from urllib.parse import urlsplit
            image_url = result.data['data']['my_participation_segments'][0]['entry_image_url']
            image_path = urlsplit(image_url).path
            self.assertTrue(image_path.startswith('/api/media/private/'))
            image_response = own.get(image_path)
            self.assertEqual(image_response.status_code, 200)
            image_response.close()
            self.assertEqual(foreign.get(image_path).status_code, 404)
            completed = own.post(f"/api/orders/orders/{first['order']}/complete/",
                {'completion_image':flow.screenshot()}, format='multipart')
            self.assertEqual(completed.data['code'], 0, completed.data)
            complete_url = completed.data['data']['my_participation_segments'][0]['completion_image_url']
            complete_path = urlsplit(complete_url).path
            complete_response = own.get(complete_path)
            self.assertEqual(complete_response.status_code, 200); complete_response.close()
            self.assertEqual(foreign.get(complete_path).status_code, 404)
            old_foreign = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+first['previous_token'])
            attempted = old_foreign.post(f"/api/orders/orders/{second['order']}/participation-evidence/",
                {'segment_id':second['previous'], 'completion_image':flow.screenshot()}, format='multipart')
            self.assertNotEqual(attempted.data.get('code'), 0)
            old_own = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+first['previous_token'])
            uploaded = old_own.post(f"/api/orders/orders/{first['order']}/participation-evidence/",
                {'segment_id':first['previous'], 'completion_image':flow.screenshot()}, format='multipart')
            self.assertEqual(uploaded.data['code'], 0, uploaded.data)
            saved_path = urlsplit(uploaded.data['data']['my_participation_segments'][0]['completion_image_url']).path
            self.assertEqual(old_foreign.get(saved_path).status_code, 404)
            report_url = f"/api/orders/orders/{first['order']}/segment-report/"
            def report_action(client, action, key, **extra):
                return client.post(report_url, {'segment_id':first['previous'], 'action':action,
                    'client_request_id':key, **extra}, format='multipart' if action == 'upload' else 'json')
            self.assertNotEqual(report_action(old_foreign, 'open', 'cross-store-open').data.get('code'), 0)
            self.assertEqual(report_action(old_own, 'open', 'own-open').data['code'], 0)
            self.assertEqual(report_action(old_own, 'upload', 'own-upload', image=flow.screenshot()).data['code'], 0)
            submitted = report_action(old_own, 'submit', 'own-submit', version=0)
            self.assertEqual(submitted.data['code'], 0, submitted.data)
            snapshot = submitted.data['data']['my_participation_segments'][0]['segment_report']['submission']
            image_path = urlsplit(snapshot['result_images'][0]['url']).path
            response = old_own.get(image_path)
            self.assertEqual(response.status_code, 200); response.close()
            self.assertEqual(old_foreign.get(image_path).status_code, 404)
            own_staff = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+first['staff_token'])
            foreign_staff = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+first['staff_token'])
            plan_url = f"/api/admin/orders/{first['order']}/segment-settlement-plan/"
            self.assertEqual(own_staff.get(plan_url).data['code'], 0)
            self.assertNotEqual(foreign_staff.get(plan_url).data.get('code'), 0)
            refund_url = f"/api/admin/orders/{first['order']}/segment-refund-preview/"
            refund_preview = own_staff.post(refund_url, {'refund_amount':10}, format='json')
            self.assertEqual(refund_preview.data['code'], 0, refund_preview.data)
            self.assertEqual(refund_preview.data['data']['rounding_to_store'], 10)
            self.assertNotEqual(foreign_staff.post(refund_url, {'refund_amount':10}, format='json').data.get('code'), 0)
            reviewed = own_staff.post(f"/api/admin/orders/{first['order']}/segments/{first['previous']}/report-review/",
                {'action':'approve', 'version':1}, format='json')
            self.assertEqual(reviewed.data['code'], 0, reviewed.data)
            with store_context(stores[0][0]):
                segment = OrderParticipationSegment.objects.get(pk=first['segment'])
                self.assertIsNotNone(segment.started_at); self.assertTrue(segment.entry_image); self.assertTrue(segment.completion_image)
                from datetime import timedelta
                from django.utils import timezone
                from orders.models import Order, OrderSegmentPersonSettlement, OrderSegmentReportSubmission
                from orders.services import settle_order
                from stores.db import transaction
                now = timezone.now()
                Order.objects.filter(pk=first['order']).update(completed_at=now-timedelta(hours=100))
                OrderSegmentReportSubmission.objects.filter(report__segment_id=first['previous']).update(
                    submitted_at=now-timedelta(hours=99), reviewed_at=now-timedelta(hours=98))
                with transaction.atomic():
                    locked = Order.objects.select_for_update().get(pk=first['order'])
                    self.assertEqual(len(settle_order(locked)), 1)
                self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order_id=first['order']).count(), 1)
            with store_context(stores[1][0]):
                segment = OrderParticipationSegment.objects.get(pk=second['segment'])
                self.assertIsNone(segment.started_at); self.assertFalse(segment.entry_image); self.assertFalse(segment.completion_image)
                from orders.models import OrderSegmentReport
                self.assertFalse(OrderSegmentReport.objects.exists())
                self.assertFalse(OrderSegmentPersonSettlement.objects.exists())


