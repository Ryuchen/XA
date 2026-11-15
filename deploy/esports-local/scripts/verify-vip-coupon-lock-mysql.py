"""Exercise locked/unlocked VIP coupon updates in an isolated store rollback."""
import os
from pathlib import Path
import secrets
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connections  # noqa: E402
from club_accounts.services import get_or_create_account_for_legacy_user  # noqa: E402
from coupons.benefits import issue  # noqa: E402
from coupons.models import Coupon  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402

STORES = {'shenglang': ('default', 'xa_test_fronted_acceptance_20261104'),
          'xinan': ('store_b', 'xa_test_store_preview_b_20261104')}
if len(sys.argv) != 2 or sys.argv[1] not in STORES:
    raise SystemExit('Expected store: shenglang or xinan')
slug = sys.argv[1]
alias, expected = STORES[slug]
config = settings.DATABASES[alias]
if config['NAME'] != expected or config['HOST'] != '127.0.0.1' or str(config['PORT']) != '13306':
    raise SystemExit('Database boundary mismatch')
with connections[alias].cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != expected:
        raise SystemExit('Unexpected connected database')
store = resolve_store(slug)
if store.database_alias != alias:
    raise SystemExit('Unexpected store binding')
with store_context(store), transaction.atomic():
    user = get_user_model().objects.create_user(username='qa-vip-lock-' + uuid.uuid4().hex,
        password=secrets.token_urlsafe(24), role='CUSTOMER')
    account = get_or_create_account_for_legacy_user(user)
    prefix = f'vip-month:{account.pk}:{account.business_cycle}:2099-01'
    locked = issue(account, prefix + ':0', name='隔离锁定券', rate=90)[0]
    unlocked = issue(account, prefix + ':1', name='隔离未锁定券', rate=90)[0]
    locked.vip_terms_locked_by_downgrade = True
    locked.save(update_fields=['vip_terms_locked_by_downgrade'])
    changed = Coupon.objects.filter(grant_key__startswith=prefix + ':', discount_rate__gt=85,
        user_coupons__account=account, user_coupons__status='UNUSED',
        user_coupons__vip_terms_locked_by_downgrade=False).exclude(
        user_coupons__vip_terms_locked_by_downgrade=True).update(discount_rate=85)
    locked.coupon.refresh_from_db(); unlocked.coupon.refresh_from_db()
    if (changed, locked.coupon.discount_rate, unlocked.coupon.discount_rate) != (1, 90, 85):
        raise RuntimeError('VIP coupon update did not preserve locked terms')
    transaction.set_rollback(True)
print(f'{slug}: isolated MySQL VIP coupon update passed; test transaction rolled back')
