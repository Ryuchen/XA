"""Run the new monthly coupon core in an isolated rolled-back MySQL transaction."""
from datetime import datetime
import os
from pathlib import Path
import secrets
import sys
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connections  # noqa: E402
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations  # noqa: E402
from coupons.models import UserCoupon, VipMonthlyCouponGrant  # noqa: E402
from coupons.monthly import issue_monthly_coupons  # noqa: E402
from engagement.models import ClubOperationConfig, VipTier  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402
from users.models import BossType  # noqa: E402

STORES = {'shenglang': ('default', 'xa_test_fronted_acceptance_20261104'),
          'xinan': ('store_b', 'xa_test_store_preview_b_20261104')}
if len(sys.argv) != 2 or sys.argv[1] not in STORES:
    raise SystemExit('Expected store: shenglang or xinan')
slug = sys.argv[1]
alias, expected = STORES[slug]
database = settings.DATABASES[alias]
if database['NAME'] != expected or database['HOST'] != '127.0.0.1' or str(database['PORT']) != '13306':
    raise SystemExit('Database boundary mismatch')
with connections[alias].cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != expected:
        raise SystemExit('Unexpected connected database')
store = resolve_store(slug)
if store.database_alias != alias:
    raise SystemExit('Unexpected store binding')
with store_context(store), transaction.atomic():
    if not ClubOperationConfig.objects.filter(pk=1, vip_enabled=True).exists():
        raise RuntimeError('Isolated store VIP is disabled; configuration was not changed')
    levels = [value for value in range(60000, 60100) if not VipTier.objects.filter(level=value).exists()][:2]
    if len(levels) != 2:
        raise RuntimeError('No isolated fixture tier levels available')
    nonce = uuid.uuid4().hex
    user = get_user_model().objects.create_user(username='qa-month-' + nonce,
        password=secrets.token_urlsafe(24), role='CUSTOMER')
    account = get_or_create_account_for_legacy_user(user)
    link_legacy_relations(user, account)
    types = [BossType.objects.create(name=f'隔离月券{index}-{nonce}', discount_rate=95) for index in (1, 2)]
    first = VipTier.objects.create(level=levels[0], boss_type=types[0], recharge_threshold=1,
        monthly_coupon_count=2, monthly_discount=90)
    second = VipTier.objects.create(level=levels[1], boss_type=types[1], recharge_threshold=2,
        monthly_coupon_count=4, monthly_discount=70)
    account.boss_type = types[0]; account.save(update_fields=['boss_type'])
    now = datetime(2099, 11, 7, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
    grant = issue_monthly_coupons(account, first.pk, now=now, allow_new_period=True)
    issue_monthly_coupons(account, first.pk, now=now, allow_new_period=True)
    coupons = UserCoupon.objects.filter(account=account, coupon__grant_key__startswith='vip-month:')
    if coupons.count() != 2 or VipMonthlyCouponGrant.objects.filter(account=account).count() != 1:
        raise RuntimeError('Repeated issuance did not remain idempotent')
    first.monthly_coupon_count = 1; first.monthly_discount = 80
    first.save(update_fields=['monthly_coupon_count', 'monthly_discount'])
    issue_monthly_coupons(account, first.pk, now=now)
    if coupons.count() != 2 or set(coupons.values_list('coupon__discount_rate', flat=True)) != {80}:
        raise RuntimeError('Same-tier update did not preserve count and update terms')
    account.boss_type = types[1]; account.save(update_fields=['boss_type'])
    issue_monthly_coupons(account, second.pk, now=now)
    grant.refresh_from_db()
    if not grant.terms_frozen or coupons.count() != 2 or coupons.filter(vip_terms_locked_by_downgrade=False).exists():
        raise RuntimeError('Same-month upgrade did not preserve previously issued terms')
    transaction.set_rollback(True)
print(f'{slug}: isolated MySQL monthly coupon core passed; test transaction rolled back')
