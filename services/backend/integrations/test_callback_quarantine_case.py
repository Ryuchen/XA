"""Regression reproducer for authenticated quarantine and notification identity."""
import json
from integrations.test_wechatpay_test_callback import IsolatedCallbackTest
from integrations.wechatpay_test_callback import isolated_callback
from orders.direct_callback_models import DirectCallbackQuarantine
from orders.direct_payment_delivery_models import DirectPaymentInbox, DirectPaymentOutbox


class CallbackQuarantineConflictAudit(IsolatedCallbackTest):
    def test_previously_quarantined_signed_event_id_cannot_later_activate_conflicting_event(self):
        # This signed envelope is durable but cannot be interpreted as a
        # supported event. Its id equals the later payment event's id.
        damaged = json.dumps({'id': 'event-1', 'event_type': 'TRANSACTION.UNKNOWN',
                              'resource_type': 'encrypt-resource', 'resource': {}}).encode()
        self.assertEqual(isolated_callback(self.request(damaged)).status_code, 503)
        self.assertEqual(DirectCallbackQuarantine.objects.count(), 1)
        body = self.envelope(event_id='event-1')
        self.assertEqual(isolated_callback(self.request(body)).status_code, 503)
        self.assertEqual(DirectCallbackQuarantine.objects.count(), 2)
        self.assertFalse(DirectPaymentInbox.objects.exists())
        self.intent.refresh_from_db()
        # An authenticated conflicting event ID must not silently activate
        # dispatch until the previously quarantined evidence is reconciled.
        with self.subTest('status'):
            self.assertNotEqual(self.intent.status, 'PAID')
        with self.subTest('dispatch'):
            self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())
