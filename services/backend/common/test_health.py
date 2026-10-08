from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings


@override_settings(ALLOWED_HOSTS=['testserver'])
class ReadinessTests(SimpleTestCase):
    def test_ready_and_no_cache(self):
        with patch('common.health.connection') as database, patch('common.health.Redis') as redis:
            database.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
            redis.from_url.return_value.ping.return_value = True
            response = self.client.get('/api/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertIn('no-store', response['Cache-Control'])
        redis.from_url.return_value.close.assert_called_once()

    def test_dependency_failures_hide_details(self):
        for dependency in ('database', 'redis'):
            with self.subTest(dependency=dependency), patch('common.health.connection') as database, patch('common.health.Redis') as redis:
                if dependency == 'database':
                    database.cursor.side_effect = RuntimeError('secret://password@database')
                else:
                    redis.from_url.return_value.ping.side_effect = RuntimeError('secret://password@redis')
                response = self.client.get('/api/health/')
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {'status': 'unavailable'})

    def test_false_redis_ping_is_unavailable(self):
        with patch('common.health.connection'), patch('common.health.Redis') as redis:
            redis.from_url.return_value.ping.return_value = False
            self.assertEqual(self.client.get('/api/health/').status_code, 503)
        redis.from_url.return_value.close.assert_called_once()

    def test_read_only(self):
        self.assertEqual(self.client.post('/api/health/').status_code, 405)
