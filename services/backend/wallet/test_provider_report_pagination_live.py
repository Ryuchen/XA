"""A provider's paginated report history follows real evidence and creation writes."""
import os
from tempfile import TemporaryDirectory
from unittest import skipUnless

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.tests.factories import make_console_user, make_order, make_provider, make_user
from wallet.models import ProviderReport


@skipUnless(os.environ.get('XA_PROVIDER_REPORT_ROOT'), 'Requires isolated provider build and Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderReportPaginationLiveTest(LiveServerTestCase):
    def test_open_report_updates_when_provider_changes_evidence_elsewhere(self):
        from orders.tests.test_release_flow import screenshot
        media = TemporaryDirectory(prefix='xa-report-evidence-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        provider, boss = make_provider(), make_user()
        reviewer = make_console_user(['report:view'])
        reviewer_account = get_or_create_account_for_legacy_user(reviewer)
        reviewer.admin_membership.account = reviewer_account; reviewer.admin_membership.save(update_fields=['account'])
        account = get_or_create_account_for_legacy_user(provider); link_legacy_relations(provider, account)
        provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        order = make_order(boss, provider=provider, provider_account=account, status='COMPLETED')
        report = ProviderReport.objects.create(provider=provider, provider_account=account, order=order,
            game_name='实时凭证', amount=100, status='REJECTED', audit_remark='请补充战绩')
        provider_token = issue_account_tokens(account)[0]
        reviewer_token = issue_account_tokens(reviewer_account)[0]
        provider_client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + provider_token)
        reviewer_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + reviewer_token)

        def change_evidence():
            uploaded = provider_client.post(f'/api/wallet/reports/{report.pk}/images/',
                {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(uploaded.data['code'], 0, uploaded.data)
            detail = reviewer_client.get(f'/api/admin/reports/{report.pk}/')
            self.assertEqual(detail.data['code'], 0, detail.data)
            self.assertEqual(len(detail.data['data']['result_image_urls']), 1)
            image_id = uploaded.data['data']['result_images'][0]['id']
            deleted = provider_client.delete(f'/api/wallet/reports/{report.pk}/images/{image_id}/')
            self.assertEqual(deleted.data['code'], 0, deleted.data)
            detail = reviewer_client.get(f'/api/admin/reports/{report.pk}/')
            self.assertEqual(detail.data['code'], 0, detail.data)
            self.assertEqual(len(detail.data['data']['result_image_urls']), 0)

        async def verify():
            reviewer_socket = WebsocketCommunicator(application, f'/ws/orders/?token={reviewer_token}')
            self.assertTrue((await reviewer_socket.connect())[0])
            try:
                await database_sync_to_async(change_evidence)()
                changes = []
                while len(changes) < 2:
                    message = await reviewer_socket.receive_json_from(timeout=2)
                    if message['type'] == 'report_status_update' and message['data']['report_id'] == report.pk:
                        changes.append(message)
            finally:
                await reviewer_socket.disconnect()
        async_to_sync(verify)()
        self.assertEqual(report.result_images.count(), 0)

    def test_old_order_link_and_live_new_report_preserve_history(self):
        media=TemporaryDirectory(prefix='xa-report-pages-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        provider,boss=make_provider(),make_user()
        account=get_or_create_account_for_legacy_user(provider); link_legacy_relations(provider,account)
        provider.escort_profile.account=account;provider.escort_profile.save(update_fields=['account'])
        old=make_order(boss,provider=provider,provider_account=account,status='COMPLETED')
        ProviderReport.objects.create(provider=provider,provider_account=account,order=old,game_name='历史关联订单',amount=100,status='DRAFT')
        for index in range(25): ProviderReport.objects.create(provider=provider,provider_account=account,game_name=f'历史报单{index}',amount=100,status='DRAFT')
        new=make_order(boss,provider=provider,provider_account=account,status='GRABBED')
        target=make_order(boss,provider=provider,provider_account=account,status='IN_SERVICE')
        direct=make_order(boss,provider=provider,provider_account=account,status='GRABBED')
        cancelled=make_order(boss,provider=provider,provider_account=account,status='CANCELLED',payment_status='REFUNDED')
        for _ in range(21): make_order(boss,provider=provider,provider_account=account,status='GRABBED')
        provider_client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        for order in (new, target, direct):
            created = provider_client.post('/api/wallet/reports/', {'order_id': order.pk}, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
        blocked = provider_client.post('/api/wallet/reports/', {'order_id': cancelled.pk}, format='json')
        self.assertNotEqual(blocked.data['code'], 0, blocked.data)
        self.assertEqual(ProviderReport.objects.filter(provider_account=account).count(),29)
        self.assertTrue(ProviderReport.objects.filter(provider_account=account,order=new).exists())
        self.assertTrue(ProviderReport.objects.filter(provider_account=account,order=target).exists())
        self.assertTrue(ProviderReport.objects.filter(provider_account=account,order=direct).exists())