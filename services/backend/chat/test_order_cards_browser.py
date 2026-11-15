import json
import os
from pathlib import Path
import subprocess
from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from chat.models import ChatMessage, ChatSession
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_order, make_provider, make_user
from orders.models import Order
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_CHAT_ORDER_CARD_BROWSER') == '1', 'Requires isolated ASGI and three Vite clients')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class ChatOrderCardsBrowserTest(LiveServerTestCase):
    def test_boss_and_provider_send_authorized_cards_to_staff(self):
        boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
        staff = make_console_user(['chat:view', 'chat:reply', 'order:view'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in zip((boss, provider, staff), accounts):
            link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.wechat_avatar_legacy_exempt = True
        provider.escort_profile.save(update_fields=['account', 'wechat_avatar_legacy_exempt'])
        accounts[1].openid = 'isolated-chat-card-provider'
        accounts[1].is_openid_bound = True
        accounts[1].save(update_fields=['openid', 'is_openid_bound'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        order = make_order(boss, customer_account=accounts[0], provider=provider, provider_account=accounts[1], status='IN_SERVICE')
        pool = make_order(boss, customer_account=accounts[0])
        Order.objects.filter(pk=pool.pk).update(created_at=timezone.now() - timedelta(hours=2))
        sessions = [ChatSession.objects.create(account=account, user=user) for user, account in zip((boss, provider), accounts[:2])]
        identities = [{'id': account.pk, 'token': issue_account_tokens(account)[0], 'permissions': permissions}
                      for account, permissions in zip(accounts, [[], [], ['chat:view', 'chat:reply', 'order:view']])]
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/chat-order-card-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'boss':identities[0], 'provider':identities[1], 'staff':identities[2],
                                  'order':order.pk, 'orderNo':order.order_no, 'poolOrder':pool.pk, 'poolOrderNo':pool.order_no,
                                  'bossSession':sessions[0].pk, 'providerSession':sessions[1].pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(ChatMessage.objects.filter(related_order=order).count(), 2)
        self.assertEqual(ChatMessage.objects.filter(related_order=pool).count(), 1)
        if getattr(settings, 'CHAT_NOTIFICATION_OUTBOX_ENABLED', False):
            from chat.models import ChatNotificationOutbox
            self.assertEqual(ChatNotificationOutbox.objects.filter(completed_at__isnull=False).count(), 3)
