"""Verify same-wallet referral and full-refund reporting in a rolled-back test."""
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations  # noqa: E402
from console.payroll_calculation import refunded_referral_cost  # noqa: E402
from orders.analytics import partial_share_rows  # noqa: E402
from orders.models import OrderRefund  # noqa: E402
from orders.tests.factories import make_escort_profile, make_order, make_service, make_user  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402
from wallet.models import Transaction  # noqa: E402
from wallet.services import get_wallet  # noqa: E402

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
    nonce = uuid.uuid4().hex
    boss = make_user(username='qa-ref-boss-' + nonce)
    player = make_user(role='PROVIDER', username='qa-ref-player-' + nonce)
    make_escort_profile(player, display_name='隔离推荐陪玩')
    account = get_or_create_account_for_legacy_user(player)
    link_legacy_relations(player, account)
    wallet = get_wallet(user=player, account=account)
    order = make_order(boss, service=make_service(name='隔离佣金样例-' + nonce, price=10000),
        provider=player, provider_account=account, inviter=player, inviter_account=account,
        inviter_commission=1000, provider_income=8000, amount=10000,
        status='CANCELLED', payment_status='REFUNDED')
    OrderRefund.objects.create(order=order, amount=10000, reason='隔离测试')
    income = Transaction.objects.create(wallet=wallet, order=order, tx_type='INCOME', amount=8000,
                                         remark=f'订单收入：{order.order_no}')
    Transaction.objects.create(wallet=wallet, order=order, tx_type='INCOME', amount=-8000, reversal_of=income)
    referral = Transaction.objects.create(wallet=wallet, order=order, tx_type='INCOME', amount=1000,
                                           remark=f'推荐分佣：{order.order_no}')
    rows = partial_share_rows(type(order).objects.filter(pk=order.pk), include_refunded=True)
    if refunded_referral_cost(order) != 1000 or len(rows) != 1 or rows[0]['income'] != 0:
        raise RuntimeError('Service and referral income did not reconcile')
    referral.remark = '历史来源不明'
    referral.save(update_fields=['remark'])
    if refunded_referral_cost(order) is not None:
        raise RuntimeError('Missing referral evidence was incorrectly treated as known')
    transaction.set_rollback(True)
print(f'{slug}: isolated MySQL referral reporting passed; test transaction rolled back')
