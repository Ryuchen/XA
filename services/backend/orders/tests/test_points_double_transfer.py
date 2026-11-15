"""Real double-provider points transfer, evidence and funding integration."""
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import ConsumptionConfirmation, MemberProfile
from orders.models import (Order, OrderApplication, OrderOriginalShareTransfer, OrderParticipationSegment,
                           OrderPointsTransferFundingRevision, OrderSegmentPersonSettlement)
from orders.tests import test_points_checkout_integration as fixture
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from stores.context import store_context
from stores.db import transaction
from users.models import EscortLevel, EscortProfile
from wallet.models import Transaction
from wallet.services import get_wallet


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
    SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
    SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    SERVICE_POINTS_TRANSFER_CAPTURE_TESTING=True, SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=True,
    SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class DoublePointsTransferTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = fixture.PointsFundingPlatformRecoveryTests.setUp
    payload = fixture.PointsFundingPlatformRecoveryTests.payload
    quoted_payload = fixture.PointsFundingPlatformRecoveryTests.quoted_payload
    create_assigned_points_order = fixture.PointsFundingPlatformRecoveryTests.create_assigned_points_order
    verify = fixture.PointsFundingPlatformRecoveryTests.verify
    fund = fixture.PointsFundingPlatformRecoveryTests.fund

    def test_returned_team_member_must_supply_new_segment_completion(self):
        from console.points_redemption_config import save_points_terms
        from orders.points_redemption_config import current_rules
        from orders.points_funding_evidence import verify_order_funding
        from orders.services import settle_order
        from engagement.confirmation import process_order
        with TemporaryDirectory(prefix='xa-double-points-return-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                user = make_user()
                self.account = get_or_create_account_for_legacy_user(user)
                self.account.boss_type = make_boss_type(discount_rate=90)
                self.account.save(update_fields=['boss_type'])
                self.wallet = get_wallet(user=user, account=self.account)
                self.wallet.balance = 25000; self.wallet.save(update_fields=['balance'])
                self.profile = MemberProfile.objects.create(account=self.account, points_balance=15000)
                self.service = make_service(price=10000)
                self.service.billing_provider_count = 2; self.service.commission_policy = 'REGULAR'
                self.service.save(update_fields=['billing_provider_count', 'commission_policy'])
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
                self.client = boss
                save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                                  max_ratio_basis_points=5000, version=current_rules()['version'], audit=lambda *_: None)
                response = boss.post('/api/orders/create/', self.quoted_payload(), format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                order = Order.objects.get(pk=response.data['data']['id'])
                Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
                users, accounts, players = [], [], []
                for rate in (20, 30):
                    provider = make_provider(deposit_mode='EARNINGS')
                    account = get_or_create_account_for_legacy_user(provider)
                    level = EscortLevel.objects.create(name=f'double-return-{rate}', commission_rate=rate)
                    EscortProfile.objects.filter(user=provider).update(account=account, level=level)
                    users.append(provider); accounts.append(account)
                    players.append(APIClient(HTTP_HOST='xa.esports.club',
                        HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]))
                response = players[0].post(f'/api/orders/orders/{order.pk}/grab/',
                    {'team_size': 2, 'partner_id': accounts[1].pk}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                application = OrderApplication.objects.get(order=order, applicant=accounts[0])
                response = players[1].post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                    {'action': 'CONFIRM_TEAM'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                response = boss.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                for client in players:
                    response = client.post(f'/api/orders/orders/{order.pk}/start/',
                        {'entry_image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                response = players[0].post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                order.refresh_from_db(); self.assertEqual(order.status, 'IN_SERVICE')
            self.client = self.platform_client
            self.assertEqual(self.verify().status_code, 200)
            response = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 250})
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second):
                temporary = make_provider(deposit_mode='EARNINGS')
                temp_account = get_or_create_account_for_legacy_user(temporary)
                temporary.escort_profile.account = temp_account; temporary.escort_profile.save(update_fields=['account'])
                temp_client = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(temp_account)[0])
                staff_user = make_console_user(['order:view', 'order:dispatch', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                url = f'/api/admin/orders/{order.pk}/transfer/'
                self.service.commission_override = True; self.service.commission_rate = 50
                self.service.save(update_fields=['commission_override', 'commission_rate'])
                def transfer(source, target, retained, transferred):
                    response = admin.post(url, {'reason': '双方同意', 'transfers': [
                        {'old_provider_id': source.pk, 'new_provider_id': target.pk, 'split_type': 'PERCENT',
                         'split_value': 0, 'penalty_amount': 0, 'original_share': {'retained': retained,
                             'transferred': transferred, 'agreement': '双方同意'}}]}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                transfer(accounts[0], temp_account, 5000, 5000)
                temp_segment = OrderParticipationSegment.objects.get(attribution__order=order,
                    provider_account=temp_account, released_at__isnull=True)
                response = temp_client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': temp_segment.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                response = players[1].post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                order.refresh_from_db(); self.assertEqual(order.status, 'IN_SERVICE')
                self.service.commission_rate = 25; self.service.save(update_fields=['commission_rate'])
                transfer(temp_account, accounts[0], 0, 5000)
                returned = OrderParticipationSegment.objects.get(attribution__order=order,
                    provider_account=accounts[0], released_at__isnull=True)
                progress = boss.get(f'/api/orders/orders/{order.pk}/').data['data']['team_progress']
                submitted = {member['id']: member['submitted'] for member in progress['members']}
                self.assertEqual(submitted, {accounts[0].pk: False, accounts[1].pk: True})
                denied = players[0].post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                response = players[0].post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': returned.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                with transaction.atomic():
                    funding = verify_order_funding(order.pk)
                    self.assertEqual((funding.required, funding.allocated, funding.remaining), (63, 0, 63))
            with store_context(self.second):
                order.refresh_from_db(); self.assertEqual(order.status, 'IN_SERVICE')
                progress = boss.get(f'/api/orders/orders/{order.pk}/').data['data']['team_progress']
                submitted = {member['id']: member['submitted'] for member in progress['members']}
                self.assertEqual(submitted, {accounts[0].pk: False, accounts[1].pk: True})
                response = players[0].post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                order.refresh_from_db(); self.assertEqual(order.status, 'COMPLETED')
                initial = OrderParticipationSegment.objects.filter(attribution__order=order,
                    provider_account=accounts[0], released_at__isnull=False).order_by('pk').first()
                teammate = OrderParticipationSegment.objects.get(attribution__order=order,
                    provider_account=accounts[1], released_at__isnull=True)
                def report(client, segment):
                    for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                        response = client.post(f'/api/orders/orders/{order.pk}/segment-report/',
                            {'segment_id': segment.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                            format='multipart' if action == 'upload' else 'json')
                        self.assertEqual(response.data['code'], 0, response.data)
                    response = admin.post(f'/api/admin/orders/{order.pk}/segments/{segment.pk}/report-review/',
                        {'action': 'approve', 'version': 1}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                report(players[0], initial); report(players[0], returned)
                future = timezone.now() + timedelta(hours=100)
                def settle():
                    with patch('django.utils.timezone.now', return_value=future), transaction.atomic():
                        return settle_order(Order.objects.select_for_update().get(pk=order.pk))
                self.assertEqual(settle(), [])
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
            response = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 63})
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second):
                self.assertEqual(settle(), [users[0].pk])
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                report(players[1], teammate)
                self.assertEqual(settle(), [users[1].pk])
                order.refresh_from_db()
                self.assertEqual((order.provider_income, order.shop_income, order.settlement_store_funding), (11063, 0, 63))
                self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(), 2)
                with patch('django.utils.timezone.now', return_value=future):
                    self.assertTrue(process_order(order.pk))
                    self.assertFalse(process_order(order.pk))
                self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 11000)

    def test_double_transfer_preserves_segments_and_completes_after_each_current_member(self):
        from console.points_redemption_config import save_points_terms
        from orders.points_redemption_config import current_rules
        from orders.points_funding_evidence import verify_order_funding
        from orders.points_funding_release import receipt_points_used_locked
        from users.models import PassRefundRepaymentReceipt
        with TemporaryDirectory(prefix='xa-double-points-transfer-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                user = make_user()
                self.account = get_or_create_account_for_legacy_user(user)
                self.account.boss_type = make_boss_type(discount_rate=90)
                self.account.save(update_fields=['boss_type'])
                self.wallet = get_wallet(user=user, account=self.account)
                self.wallet.balance = 25000; self.wallet.save(update_fields=['balance'])
                self.profile = MemberProfile.objects.create(account=self.account, points_balance=15000)
                self.service = make_service(price=10000)
                self.service.billing_provider_count = 2; self.service.commission_policy = 'REGULAR'
                self.service.save(update_fields=['billing_provider_count', 'commission_policy'])
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
                self.client = boss
                save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                                  max_ratio_basis_points=5000, version=current_rules()['version'], audit=lambda *_: None)
                response = boss.post('/api/orders/create/', self.quoted_payload(), format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                order = Order.objects.get(pk=response.data['data']['id'])
                Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
                old_accounts, old_users, old_clients = [], [], []
                for rate in (20, 30):
                    provider = make_provider(deposit_mode='EARNINGS')
                    account = get_or_create_account_for_legacy_user(provider)
                    level = EscortLevel.objects.create(name=f'double-transfer-{rate}', commission_rate=rate)
                    EscortProfile.objects.filter(user=provider).update(account=account, level=level)
                    old_users.append(provider); old_accounts.append(account)
                    old_clients.append(APIClient(HTTP_HOST='xa.esports.club',
                        HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]))
                response = old_clients[0].post(f'/api/orders/orders/{order.pk}/grab/',
                    {'team_size': 2, 'partner_id': old_accounts[1].pk}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                application = OrderApplication.objects.get(order=order, applicant=old_accounts[0])
                response = old_clients[1].post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                    {'action': 'CONFIRM_TEAM'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                response = boss.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                for client in old_clients:
                    response = client.post(f'/api/orders/orders/{order.pk}/start/',
                        {'entry_image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                # A former member's completion image must not satisfy either
                # replacement's completion action.
                response = old_clients[1].post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                order.refresh_from_db(); self.assertEqual(order.status, 'IN_SERVICE')
            self.client = self.platform_client
            self.assertEqual(self.verify().status_code, 200)
            response = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 250})
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second):
                newcomers, clients, users = [], [], []
                for _ in range(2):
                    provider = make_provider(deposit_mode='EARNINGS')
                    account = get_or_create_account_for_legacy_user(provider)
                    provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
                    users.append(provider); newcomers.append(account)
                    clients.append(APIClient(HTTP_HOST='xa.esports.club',
                        HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]))
                staff_user = make_console_user(['order:view', 'order:dispatch', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                self.service.commission_override = True; self.service.commission_rate = 50
                self.service.save(update_fields=['commission_override', 'commission_rate'])
                commands = [{'old_provider_id': old.pk, 'new_provider_id': new.pk, 'split_type': 'PERCENT',
                             'split_value': 0, 'penalty_amount': 0,
                             'original_share': {'retained': 5000, 'transferred': 5000, 'agreement': '双方同意'}}
                            for old, new in zip(old_accounts, newcomers)]
                response = admin.post(f'/api/admin/orders/{order.pk}/transfer/',
                    {'reason': '两席同时转单', 'transfers': commands}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                events = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('pk'))
                self.assertEqual(len(events), 2)
                self.assertEqual(events[0].created_at, events[1].created_at)
                revision = OrderPointsTransferFundingRevision.objects.get(order=order)
                self.assertEqual(revision.last_transfer_id, events[-1].pk)
                self.assertEqual(list(revision.releases.values_list('amount', flat=True)), [250])
                with transaction.atomic():
                    proof = verify_order_funding(order.pk, allow_unstarted=True)
                    self.assertEqual((proof.required, proof.allocated, proof.remaining), (0, 0, 0))
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    self.assertEqual(receipt_points_used_locked(receipt), 0)
                old_segments = [OrderParticipationSegment.objects.get(attribution__order=order,
                    provider_account=account, released_at__isnull=False) for account in old_accounts]
                new_segments = [OrderParticipationSegment.objects.get(attribution__order=order,
                    provider_account=account, released_at__isnull=True) for account in newcomers]
                denied = clients[0].post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                denied = old_clients[1].post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                response = clients[0].post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': new_segments[0].pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                with override_settings(SERVICE_POINTS_TRANSFER_CAPTURE_TESTING=False):
                    denied = clients[0].post(f'/api/orders/orders/{order.pk}/complete/',
                        {'completion_image': screenshot()}, format='multipart')
                    self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                    new_segments[0].refresh_from_db()
                    self.assertFalse(new_segments[0].completion_image)
                with transaction.atomic():
                    revision.after_funding = {}; revision.save(update_fields=['after_funding'])
                    denied = clients[0].post(f'/api/orders/orders/{order.pk}/complete/',
                        {'completion_image': screenshot()}, format='multipart')
                    self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                    new_segments[0].refresh_from_db()
                    self.assertFalse(new_segments[0].completion_image)
                    transaction.set_rollback(True)
                response = clients[0].post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot(), 'client_request_id': 'first-teammate-completion'}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                replay = clients[0].post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot(), 'client_request_id': 'first-teammate-completion'}, format='multipart')
                self.assertEqual(replay.data['code'], 0, replay.data)
                self.assertTrue(replay.data['data']['request_replayed'])
                order.refresh_from_db(); self.assertEqual(order.status, 'IN_SERVICE')
                response = clients[1].post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': new_segments[1].pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                response = clients[1].post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                order.refresh_from_db(); self.assertEqual(order.status, 'COMPLETED')
                detail = old_clients[0].get(f'/api/orders/orders/{order.pk}/').data['data']
                action = next(item for item in detail['my_participation_segments'] if item['id'] == old_segments[0].pk)
                self.assertTrue(action['can_upload_completion'])
                response = old_clients[0].post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                    {'segment_id': old_segments[0].pk, 'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                def report(client, segment):
                    for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                        response = client.post(f'/api/orders/orders/{order.pk}/segment-report/',
                            {'segment_id': segment.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                            format='multipart' if action == 'upload' else 'json')
                        self.assertEqual(response.data['code'], 0, response.data)
                    response = admin.post(f'/api/admin/orders/{order.pk}/segments/{segment.pk}/report-review/',
                        {'action': 'approve', 'version': 1}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                report(old_clients[0], old_segments[0])
                from engagement.confirmation import process_order
                from orders.services import settle_order
                future = timezone.now() + timedelta(hours=100)
                def settle():
                    with patch('django.utils.timezone.now', return_value=future), transaction.atomic():
                        return settle_order(Order.objects.select_for_update().get(pk=order.pk))
                self.assertEqual(settle(), [old_users[0].pk])
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                for client, segment in ((old_clients[1], old_segments[1]), *zip(clients, new_segments)):
                    report(client, segment)
                self.assertCountEqual(settle(), [old_users[1].pk, *[provider.pk for provider in users]])
                order.refresh_from_db()
                self.assertEqual((order.provider_income, order.shop_income, order.settlement_store_funding), (9375, 1625, 0))
                self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(), 4)
                with patch('django.utils.timezone.now', return_value=future):
                    self.assertTrue(process_order(order.pk))
                    self.assertFalse(process_order(order.pk))
                self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 11000)
            with store_context(self.first):
                self.assertFalse(OrderOriginalShareTransfer.objects.exists())
