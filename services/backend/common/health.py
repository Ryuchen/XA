"""Minimal readiness response without publishing infrastructure or exceptions."""
import logging

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from redis import Redis

logger = logging.getLogger(__name__)


@require_GET
@never_cache
def readiness(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        client = Redis.from_url(settings.CELERY_BROKER_URL,
                                socket_connect_timeout=2, socket_timeout=2)
        try:
            if not client.ping():
                raise ConnectionError('Redis unavailable')
        finally:
            client.close()
    except Exception:
        # Exception text can contain connection strings; keep it out of this API.
        logger.warning('Readiness dependency check failed')
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})
