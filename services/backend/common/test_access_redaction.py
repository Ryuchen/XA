from io import StringIO
import logging

from daphne.access import AccessLogGenerator
from django.test import SimpleTestCase

from common.logging_utils import JsonLogFormatter, PlainLogFormatter
from config.serve import RedactedServer


class AccessRedactionTest(SimpleTestCase):
    def test_application_formatters_remove_url_credentials(self):
        record = logging.LogRecord('django.request', logging.WARNING, '', 0,
                                   'Not Found: %s', ('/api/media/private/image-capability/',), None)
        for formatter in (PlainLogFormatter(), JsonLogFormatter()):
            self.assertNotIn('image-capability', formatter.format(record))
            self.assertIn('/api/media/private/[redacted]/', formatter.format(record))

    def test_actual_daphne_access_writer_redacts_http_and_websocket_paths(self):
        stream = StringIO()
        server = object.__new__(RedactedServer)
        server.action_logger = AccessLogGenerator(stream)
        http = {'client': '127.0.0.1', 'method': 'GET', 'path': '/api/media/private/image-capability/', 'status': 200, 'size': 128}
        server.log_action('http', 'complete', http)
        for action in ('connecting', 'connected', 'rejected', 'disconnected'):
            server.log_action('websocket', action, {'client': '127.0.0.1', 'path': '/ws/orders/?token=account-jwt&v=1'})
        output = stream.getvalue()
        self.assertNotIn('image-capability', output)
        self.assertNotIn('account-jwt', output)
        self.assertIn('GET /api/media/private/[redacted]/', output)
        self.assertIn('/ws/orders/?token=[redacted]&v=1', output)
        self.assertEqual(http['path'], '/api/media/private/image-capability/')
