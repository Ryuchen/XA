from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderProvider, OrderStatusLog, OrderTransferSeatEvent
from orders.state_machine import log_only
from orders.tests.factories import make_console_user, make_order, make_provider, make_user
from orders.transfer_limit import record_locked
from stores.db import transaction

from .models import Message, ServiceTransferAssignmentNotice
from .service_transfer_assignment_notice import record_or_schedule
from .tasks import recover_service_transfer_notices


@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, SERVICE_TRANSFER_NOTICE_RECOVERY_ENABLED=True)
class ServiceTransferAssignmentNoticeTests(TestCase):
    def setUp(self):
        self.boss_user = make_user()
        self.boss = self.account(self.boss_user, 5)
        self.operator = get_or_create_account_for_legacy_user(make_console_user())
        self.order = make_order(self.boss_user, status=Order.Status.IN_SERVICE, customer_account=self.boss,
                                business_cycle=5, escort_mode=Order.EscortMode.DOUBLE, required_provider_count=2)
        self.old = self.account(make_provider(), 2)
        self.team = self.account(make_provider(), 3)
        self.new = self.account(make_provider(), 4)
        self.old_assignment = self.assign(self.old)
        self.team_assignment = self.assign(self.team)

    def account(self, user, cycle):
        account = get_or_create_account_for_legacy_user(user)
        ClubAccount.objects.filter(pk=account.pk).update(business_cycle=cycle)
        account.business_cycle = cycle
        return account

    def assign(self, account):
        user_id = account.legacy_mapping.legacy_user_id
        return OrderProvider.objects.create(order=self.order, provider_account=account,
                                            provider_id=user_id, provider_business_cycle=account.business_cycle)

    def transfer(self, changes):
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=self.order.pk)
            previous = {row.provider_account_id: row for row in order.providers.all()}
            events = record_locked(order, [(old.pk, new.pk) for old, new in changes])
            at = timezone.now()
            for old, new in changes:
                row = previous[old.pk]
                row.released_at = at
                row.save(update_fields=['released_at'])
                self.assign(new)
            log = log_only(order, action=OrderStatusLog.Action.TRANSFER, operator_account=self.operator)
            record_or_schedule(order, log, events, previous, transferred_at=at, subsidy_segment=False)
        return log

    def messages(self):
        return Message.objects.filter(related_order_id=self.order.pk).order_by('pk')

    def test_all_four_recipients_use_their_original_cycles(self):
        with patch('site_messages.notifier.notify_messages_after_commit') as notify:
            with self.captureOnCommitCallbacks(execute=True):
                log = self.transfer([(self.old, self.new)])
            self.assertEqual(recover_service_transfer_notices(), 0)
        notice = {row.role: row for row in ServiceTransferAssignmentNotice.objects.filter(status_log=log)}
        expected = {'BOSS': (self.boss, '已为你更换大神'), 'OLD': (self.old, '订单已转出'),
                    'NEW': (self.new, '客服转单给你'), 'TEAM': (self.team, '服务搭档已更换')}
        self.assertEqual(set(notice), set(expected))
        for role, (account, title) in expected.items():
            message = notice[role].message
            self.assertEqual((message.recipient_account_id, message.recipient_business_cycle, message.title,
                              message.assignment_alert), (account.pk, account.business_cycle, title, role == 'NEW'))
            notify.assert_any_call(account.pk, business_cycle=account.business_cycle,
                                   assignment_message_id=message.pk if role == 'NEW' else None)
        self.assertEqual(self.messages().count(), 4)

    def test_failed_delivery_retries_and_skips_an_account_that_changed_cycle(self):
        log = self.transfer([(self.old, self.new)])
        with (patch.object(Message.objects, 'create', side_effect=DatabaseError('test write failure')),
              self.assertLogs('site_messages.tasks', level='ERROR')):
            self.assertEqual(recover_service_transfer_notices(), 4)
        self.assertEqual(ServiceTransferAssignmentNotice.objects.filter(status_log=log, completed_at__isnull=True).count(), 4)
        self.assertFalse(self.messages().exists())
        ClubAccount.objects.filter(pk=self.new.pk).update(business_cycle=9)
        self.assertEqual(recover_service_transfer_notices(), 4)
        self.assertEqual(recover_service_transfer_notices(), 0)
        self.assertEqual(self.messages().count(), 3)
        skipped = ServiceTransferAssignmentNotice.objects.get(status_log=log, role='NEW')
        self.assertIsNone(skipped.message_id)
        self.assertIn('周期已变化', skipped.skipped_reason)

    def test_both_seats_transferred_share_one_boss_notice_and_roll_back_together(self):
        another = self.account(make_provider(), 6)
        changes = [(self.old, self.new), (self.team, another)]
        with self.assertRaisesMessage(RuntimeError, 'rollback'), transaction.atomic():
            self.transfer(changes)
            raise RuntimeError('rollback')
        self.assertFalse(ServiceTransferAssignmentNotice.objects.exists())
        self.assertFalse(OrderTransferSeatEvent.objects.filter(seat__order=self.order).exists())
        self.assertFalse(self.messages().exists())
        log = self.transfer(changes)
        self.assertEqual(recover_service_transfer_notices(), 5)
        self.assertEqual(self.messages().filter(recipient_account=self.boss).count(), 1)
        self.assertFalse(ServiceTransferAssignmentNotice.objects.filter(status_log=log, role='TEAM').exists())
        self.assertEqual(self.messages().filter(assignment_alert=True).count(), 2)

    @override_settings(SERVICE_TRANSFER_NOTICE_RECOVERY_ENABLED=False)
    def test_immediate_delivery_keeps_original_cycles_with_recovery_disabled(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.transfer([(self.old, self.new)])
        self.assertFalse(ServiceTransferAssignmentNotice.objects.exists())
        self.assertEqual(dict(self.messages().values_list('recipient_account_id', 'recipient_business_cycle')),
                         {account.pk: account.business_cycle for account in (self.boss, self.old, self.new, self.team)})
