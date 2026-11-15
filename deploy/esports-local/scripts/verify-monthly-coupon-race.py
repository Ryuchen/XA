"""Concurrent monthly coupon calls on one synthetic store account."""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connections  # noqa: E402
from django.test import override_settings  # noqa: E402
from club_accounts.models import ClubAccount  # noqa: E402
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations  # noqa: E402
from coupons.models import UserCoupon, VipMonthlyCouponGrant  # noqa: E402
from coupons.monthly import issue_monthly_coupons  # noqa: E402
from engagement.models import ClubOperationConfig, VipTier  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402
from users.models import BossType  # noqa: E402
from wallet.models import Wallet  # noqa: E402
from wallet.services import get_wallet  # noqa: E402

STORES = {'shenglang': ('default', 'xa_test_fronted_acceptance_20261104'),
          'xinan': ('store_b', 'xa_test_store_preview_b_20261104')}
if len(sys.argv) != 2 or sys.argv[1] not in STORES:
    raise SystemExit('Expected store: shenglang or xinan')
slug = sys.argv[1]
alias, expected = STORES[slug]
db = settings.DATABASES[alias]
if db['NAME'] != expected or db['HOST'] != '127.0.0.1' or str(db['PORT']) != '13306':
    raise SystemExit('Database boundary mismatch')
with connections[alias].cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != expected:
        raise SystemExit('Unexpected connected database')
store = resolve_store(slug)
if store.database_alias != alias:
    raise SystemExit('Unexpected store binding')
nonce = uuid.uuid4().hex
user_id = account_id = tier_id = type_id = None
with override_settings(CHANNEL_LAYERS={'default': {'BACKEND':'channels.layers.InMemoryChannelLayer'}}):
    try:
        with store_context(store), transaction.atomic():
            if not ClubOperationConfig.objects.filter(pk=1, vip_enabled=True).exists():
                raise RuntimeError('VIP disabled in isolated store; configuration not changed')
            level = next((value for value in range(60000, 60100) if not VipTier.objects.filter(level=value).exists()), None)
            if level is None:
                raise RuntimeError('No synthetic tier level available')
            user = get_user_model().objects.create_user(username='qa-month-race-' + nonce,
                password=secrets.token_urlsafe(24), role='CUSTOMER')
            user_id = user.pk
            account = get_or_create_account_for_legacy_user(user); account_id = account.pk
            link_legacy_relations(user, account)
            boss_type = BossType.objects.create(name='隔离月券竞争-' + nonce, discount_rate=95); type_id = boss_type.pk
            tier = VipTier.objects.create(level=level, boss_type=boss_type, recharge_threshold=2000000000,
                monthly_coupon_count=3, monthly_discount=90); tier_id = tier.pk
            account.boss_type = boss_type; account.save(update_fields=['boss_type'])
            get_wallet(user=user, account=account, for_update=True)
        barrier = threading.Barrier(2)
        results = []
        guard = threading.Lock()
        def run():
            try:
                with store_context(store):
                    account = ClubAccount.objects.get(pk=account_id)
                    with connections[alias].cursor() as cursor:
                        cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
                    barrier.wait(timeout=10)
                    grant = issue_monthly_coupons(account, tier_id,
                        now=datetime(2099, 11, 7, 12, tzinfo=ZoneInfo('Asia/Shanghai')), allow_new_period=True)
                    with guard: results.append(('ok', grant.pk))
            except Exception as exc:
                with guard: results.append(('error', type(exc).__name__, str(exc)))
            finally:
                connections[alias].close()
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=20)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError('Monthly issuance race did not finish')
        with store_context(store):
            grants = VipMonthlyCouponGrant.objects.filter(account_id=account_id)
            coupons = UserCoupon.objects.filter(account_id=account_id, coupon__grant_key__startswith='vip-month:')
            if len(results) != 2 or any(row[0] != 'ok' for row in results) or len({row[1] for row in results}) != 1:
                raise RuntimeError(f'Unexpected race results: {results}')
            if grants.count() != 1 or coupons.count() != 3:
                raise RuntimeError('Race duplicated a monthly decision or coupon')
        print(json.dumps({'store':slug, 'two_calls':'passed', 'grants':1, 'coupons':3}))
    finally:
        with store_context(store), transaction.atomic():
            if account_id:
                ClubAccount.objects.filter(pk=account_id).update(is_active=False, can_login=False)
                Wallet.objects.filter(account_id=account_id).update(is_active=False)
            if user_id:
                get_user_model().objects.filter(pk=user_id).update(is_active=False)
            if tier_id:
                VipTier.objects.filter(pk=tier_id).update(active=False)
            if type_id:
                BossType.objects.filter(pk=type_id).update(is_active=False)
