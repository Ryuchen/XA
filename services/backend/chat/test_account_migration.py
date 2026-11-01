"""Preserve historical support conversations when business accounts need no legacy user."""
from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from orders.tests.migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class ChatAccountMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_old_conversation_survives_and_new_account_can_open_one(self):
        old = [('chat', '0003_chatmessage_chat_message_unread_idx_and_more')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        User = apps.get_model('users', 'CustomUser')
        Session = apps.get_model('chat', 'ChatSession')
        Message = apps.get_model('chat', 'ChatMessage')
        user = User.objects.create(username='historical-chat-boss')
        session = Session.objects.create(user_id=user.pk, last_message='旧咨询', unread_support=1)
        message = Message.objects.create(session_id=session.pk, sender_id=user.pk, content='旧咨询')

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        from chat.models import ChatMessage, ChatSession
        from club_accounts.models import ClubAccount
        restored = ChatSession.objects.get(pk=session.pk)
        self.assertEqual((restored.user_id, restored.last_message, restored.unread_support),
                         (user.pk, '旧咨询', 1))
        self.assertEqual(ChatMessage.objects.get(pk=message.pk).content, '旧咨询')
        account = ClubAccount.objects.create_account(username='new-chat-migration-boss',
            account_type=ClubAccount.AccountType.BOSS)
        self.assertIsNone(ChatSession.objects.create(account=account).user_id)
