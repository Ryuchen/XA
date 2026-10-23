"""Read-only production configuration, catalog and queue verification."""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
from orders.models import Gameplay, ServiceItem
from config.celery import app

assert not settings.DEBUG
assert not settings.WECHAT_MOCK_LOGIN
assert settings.DATABASES['default']['USER'] != 'root'
assert settings.REQUIRE_CLIENT_REQUEST_ID
assert settings.REQUIRE_ORDER_EVIDENCE_IMAGES
assert settings.REQUIRE_RECHARGE_PROOF
print({'debug':False, 'mock_login':False, 'non_root_database':True,
       'strict_money_and_proofs':True, 'gameplays':Gameplay.objects.count(),
       'services':ServiceItem.objects.count(), 'active_services':ServiceItem.objects.filter(is_active=True).count()})
pings=app.control.ping(timeout=4)
assert len(pings)==1, 'Expected one production worker'
result=app.send_task('celery.accumulate',args=[1])
assert result.get(timeout=20)==[1]
print('PASS Redis -> Celery worker -> result backend (built-in no-op task).')
