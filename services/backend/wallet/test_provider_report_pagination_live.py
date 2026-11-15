"""A provider can navigate and refresh a real paginated report history."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_order, make_provider, make_user
from users.test_native_checkin_order_realtime import isolated_redis_asgi
from wallet.models import ProviderReport


@skipUnless(os.environ.get('XA_PROVIDER_REPORT_ROOT'), 'Requires isolated provider build and Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderReportPaginationLiveTest(LiveServerTestCase):
    def test_open_report_updates_when_provider_changes_evidence_elsewhere(self):
        import base64
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
        with isolated_redis_asgi() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo / 'scripts/provider-report-evidence-realtime.cjs')], cwd=repo,
                input=json.dumps({'base':base, 'wsBase':ws_base, 'root':os.environ['XA_PROVIDER_REPORT_ROOT'],
                    'id':account.pk, 'token':issue_account_tokens(account)[0], 'reviewerToken':issue_account_tokens(reviewer_account)[0], 'reportId':report.pk,
                    'orderNo':order.order_no, 'image':base64.b64encode(screenshot().read()).decode()}),
                text=True, encoding='utf-8', capture_output=True, timeout=55)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if os.environ.get('XA_ADMIN_REPORT_ROOT'):
                result = subprocess.run(['node', str(repo / 'scripts/admin-report-evidence-live.cjs')], cwd=repo,
                    input=json.dumps({'base':base, 'wsBase':ws_base, 'root':os.environ['XA_ADMIN_REPORT_ROOT'],
                        'token':issue_account_tokens(account)[0], 'reviewerToken':issue_account_tokens(reviewer_account)[0],
                        'reportId':report.pk, 'image':base64.b64encode(screenshot().read()).decode()}),
                    text=True, encoding='utf-8', capture_output=True, timeout=55)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
        with isolated_redis_asgi() as (base,ws_base):
            repo=Path(settings.BASE_DIR).parent.parent
            result=subprocess.run(['node',str(repo/'scripts/provider-report-pagination-live.cjs')],cwd=repo,
                input=json.dumps({'base':base,'wsBase':ws_base,'root':os.environ['XA_PROVIDER_REPORT_ROOT'],
                    'id':account.pk,'token':issue_account_tokens(account)[0], 'oldId':old.pk,'oldNo':old.order_no,
                    'newId':new.pk,'newNo':new.order_no,'targetNo':target.order_no,
                    'directId':direct.pk,'directNo':direct.order_no,
                    'cancelledId':cancelled.pk,'cancelledNo':cancelled.order_no}),text=True,encoding='utf-8',capture_output=True,timeout=70)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(ProviderReport.objects.filter(provider_account=account).count(),29)
        self.assertTrue(ProviderReport.objects.filter(provider_account=account,order=new).exists())
        self.assertTrue(ProviderReport.objects.filter(provider_account=account,order=target).exists())
        self.assertTrue(ProviderReport.objects.filter(provider_account=account,order=direct).exists())
