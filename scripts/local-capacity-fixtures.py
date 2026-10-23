"""Fixtures and read-only accounting checks for the isolated WSL capacity probe.

Executed via stdin inside the local backend. Tokens are returned only to the
probe's private pipe; never run this command with its output logged or displayed.
"""
import json
import os
import re
import sys
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, ServiceItem
from support.models import SupportContactCard
from users.models import EscortProfile
from wallet.models import Transaction
from wallet.services import get_wallet

mode, run = sys.argv[1:3]
assert re.fullmatch(r'[0-9a-f]{12}', run)
assert os.environ.get('XA_LOCAL_VALIDATION') == 'true'
assert connection.vendor == 'mysql' and connection.settings_dict['NAME'] == 'xa_verify'
assert connection.settings_dict['HOST'] == 'db' and connection.settings_dict['USER'] == 'xa_verify'
assert not settings.KOOK_ENABLED and not settings.WECOM_DISPATCH_ENABLED
prefix = f'local_capacity_{run}_'
accounts = ClubAccount.objects.filter(username__startswith=prefix).order_by('id')

if mode == 'prepare':
    assert not accounts.exists(), 'Use a fresh run ID; fixtures are never overwritten'
    people = []
    with transaction.atomic():
        for kind, count, role in [('boss', 100, 'CUSTOMER'), ('provider', 60, 'PROVIDER')]:
            for index in range(count):
                user = get_user_model().objects.create_user(
                    username=f'{prefix}{kind}_{index:03}', password=None, role=role,
                    nickname=f'容量验证{kind}{index:03}',
                )
                account = get_or_create_account_for_legacy_user(user)
                wallet = get_wallet(account=account)
                if kind == 'boss':
                    wallet.balance = 100000
                    wallet.save(update_fields=['balance'])
                    Transaction.objects.create(wallet=wallet, amount=100000, tx_type='ADJUST',
                        balance_before=0, balance_after=100000, remark=f'本地容量测试初始余额 {run}')
                else:
                    EscortProfile.objects.create(user=user, account=account,
                        display_name=f'容量验证陪玩{index:03}', is_verified=True,
                        pass_tier='BLACK', pass_expires_at=timezone.now() + timedelta(hours=1))
                people.append({'id': account.pk, 'kind': kind, 'index': index,
                               'token': issue_account_tokens(account)[0]})
        service = ServiceItem.objects.create(name=f'本地容量验证服务{run}', price=10000, commission_rate=20)
        card = SupportContactCard.objects.create(name=f'本地容量验证客服{run}')
    print(json.dumps({'run': run, 'people': people, 'service_id': service.pk, 'support_id': card.pk}))
elif mode == 'pending':
    print(json.dumps(list(Order.objects.filter(customer_account__in=accounts, status='PENDING')
                         .values('id', 'customer_account_id'))))
elif mode == 'claims':
    from orders.models import OrderStatusLog
    print(json.dumps([{
        'id': order.pk, 'customer_account_id': order.customer_account_id,
        'status': order.status, 'provider_account_id': order.provider_account_id,
        'active_shares': order.providers.filter(released_at__isnull=True, settled_at__isnull=True).count(),
        'grab_logs': OrderStatusLog.objects.filter(order=order, action='GRAB').count(),
    } for order in Order.objects.filter(customer_account__in=accounts)]))
elif mode == 'audit':
    orders = Order.objects.filter(customer_account__in=accounts)
    assert accounts.count() == 160
    assert not orders.exclude(status='CANCELLED', payment_status='REFUNDED').exists()
    assert not orders.values('customer_account_id').annotate(n=django.db.models.Count('id')).filter(n__gt=1).exists()
    for account in accounts:
        wallet = get_wallet(account=account, create=False)
        expected = 100000 if account.account_type == 'BOSS' else 0
        assert wallet and (wallet.balance, wallet.frozen_amount) == (expected, 0)
        total = wallet.transactions.aggregate(total=Sum('amount'))['total'] or 0
        assert total == expected
    for order in orders:
        rows = Transaction.objects.filter(order=order)
        assert rows.count() == 2 and rows.aggregate(total=Sum('amount'))['total'] == 0
    print(json.dumps({'accounts': accounts.count(), 'cancelled_orders': orders.count(),
                      'wallets_and_ledger_verified': True}))
elif mode == 'retire':
    assert not Order.objects.filter(customer_account__in=accounts, status__in=['PENDING', 'GRABBED', 'IN_SERVICE']).exists()
    with transaction.atomic():
        ServiceItem.objects.filter(name=f'本地容量验证服务{run}').update(is_active=False)
        SupportContactCard.objects.filter(name=f'本地容量验证客服{run}').update(is_active=False)
        EscortProfile.objects.filter(account__in=accounts).update(is_verified=False, status='OFFLINE')
        get_user_model().objects.filter(username__startswith=prefix).update(can_login=False)
        accounts.update(can_login=False)
    print(json.dumps({'fixtures_retired': True}))
elif mode == 'metrics':
    with connection.cursor() as cursor:
        cursor.execute("SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Threads_running','Max_used_connections','Connections','Aborted_connects')")
        result = dict(cursor.fetchall())
        cursor.execute('SELECT @@max_connections')
        result['max_connections'] = cursor.fetchone()[0]
    print(json.dumps(result))
else:
    raise ValueError('Unsupported local probe mode')
