import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase
from django.utils import timezone
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import OrderProvider, Reservation
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_RESERVATION_TRANSFER_VISIBILITY_BROWSER') == '1', 'Requires isolated ASGI and Vite')
class ReservationTransferVisibilityBrowserTest(LiveServerTestCase):
    def test_three_clients_show_original_and_current_assignments(self):
        boss = make_user(); boss_account = get_or_create_account_for_legacy_user(boss)
        old = make_provider(display_name='原陪玩验证', deposit_mode='EARNINGS'); old_account = get_or_create_account_for_legacy_user(old)
        new = make_provider(display_name='接替陪玩验证', deposit_mode='EARNINGS'); new_account = get_or_create_account_for_legacy_user(new)
        for user, account in ((old,old_account),(new,new_account)):
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
        staff = make_console_user(['order:view']); staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        service = make_service(name='隔离预约服务')
        order = make_order(boss, service=service, customer_account=boss_account, provider=new, provider_account=new_account,
                           status='IN_SERVICE', payment_status='PAID')
        OrderProvider.objects.create(order=order, provider=old, provider_account=old_account,
                                     provider_name_snapshot='原陪玩验证', released_at=timezone.now())
        OrderProvider.objects.create(order=order, provider=new, provider_account=new_account, provider_name_snapshot='接替陪玩验证')
        booking = Reservation.objects.create(customer=boss, customer_account=boss_account, provider=old, provider_account=old_account,
            provider_name_snapshot='原陪玩验证', service=service, service_name_snapshot=service.name, order=order, status='CONVERTED',
            start_time=timezone.now()+timedelta(days=1), end_time=timezone.now()+timedelta(days=1,hours=1))
        def identity(account, permissions=None):
            return {'id':account.pk, 'token':issue_account_tokens(account)[0], 'permissions':permissions or []}
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/reservation-transfer-visibility-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'boss':identity(boss_account), 'provider':identity(new_account),
                                  'staff':identity(staff_account,['order:view']), 'booking':booking.pk, 'bookingNo':booking.reservation_no}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
