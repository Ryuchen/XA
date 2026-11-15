"""Lock-order regression; the receipt allocation is an isolated test fixture."""
import os
import time
from threading import Event, Thread
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from orders.models import OrderAddonHandover, OrderFinancialLock, OrderPointsFundingAllocation
from orders.tests.test_addon_handover import AddonHandoverTests
from stores.db import transaction
from users.models import PassRefundRepaymentReceipt


@skipUnless(os.environ.get('DJANGO_SETTINGS_MODULE') == 'config.ordinary_followup_mysql_test_settings',
            'requires the dedicated disposable MySQL test environment')
@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                   ADDON_HANDOVER_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonHandoverReceiptLockMySQLTest(TransactionTestCase):
    setUp = AddonHandoverTests.setUp
    create = AddonHandoverTests.create
    agree = AddonHandoverTests.agree

    def test_final_confirmation_waits_for_shared_receipt_before_source_lot(self):
        self.assertEqual(connection.vendor, 'mysql')
        proposal = self.create()
        for response in self.agree(proposal['id'], [self.fixture.client, self.fixture.provider_client]):
            self.assertEqual(response.status_code, 200, response.data)
        receipt = PassRefundRepaymentReceipt.objects.create(request_key='handover-lock-receipt',
            external_reference='handover-lock-offline', amount=100, received_at=timezone.now(),
            evidence='isolated lock fixture', registered_by='test')
        OrderPointsFundingAllocation.objects.create(request_key='handover-lock-allocation', receipt=receipt,
            service_order=self.fixture.order, amount=100, allocated_by='test')
        OrderFinancialLock.objects.get_or_create(pk=1)
        handover = OrderAddonHandover.objects.get(pk=proposal['id'])
        lot = handover.payment.allocations.first().lot
        started, finished = Event(), Event()
        results = []

        def confirm():
            close_old_connections()
            try:
                started.set()
                results.append(self.second_client.post(self.user_url + f"{proposal['id']}/decision/",
                                                       {'accepted': True}, format='json'))
            except BaseException as exc:
                results.append(exc)
            finally:
                close_old_connections()
                finished.set()

        with transaction.atomic():
            PassRefundRepaymentReceipt.objects.select_for_update().get(pk=receipt.pk)
            worker = Thread(target=confirm, daemon=True)
            worker.start()
            self.assertTrue(started.wait(5))
            deadline = time.monotonic() + 10
            waiting = False
            while time.monotonic() < deadline and not finished.is_set():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM performance_schema.data_lock_waits w "
                        "JOIN performance_schema.data_locks l ON l.ENGINE = w.ENGINE "
                        "AND l.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID "
                        "WHERE l.OBJECT_SCHEMA = DATABASE() "
                        "AND l.OBJECT_NAME = 'users_passrefundrepaymentreceipt'")
                    waiting = bool(cursor.fetchone()[0])
                if waiting:
                    break
                time.sleep(0.05)
            self.assertTrue(waiting, 'confirmation did not reach a receipt-row lock wait')
            # A competing transaction holding the receipt can still acquire
            # the same real payment lot; the endpoint has not locked it first.
            type(lot).objects.select_for_update(nowait=True).get(pk=lot.pk)
        worker.join(15)
        self.assertFalse(worker.is_alive(), 'confirmation did not finish after receipt unlock')
        self.assertEqual(len(results), 1)
        self.assertFalse(isinstance(results[0], BaseException), repr(results[0]))
        # The synthetic allocation lacks platform authorization: the existing
        # funding gate must reject it and roll back the final decision.
        self.assertEqual(results[0].status_code, 400, results[0].data)
        handover.refresh_from_db()
        self.assertEqual(handover.status, 'PENDING')
