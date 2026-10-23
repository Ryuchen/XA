"""Compare only existing money and order facts across a schema upgrade."""
import hashlib
import json
import os
from decimal import Decimal
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.db import connection
columns = {
    'wallet_wallet': ['id','user_id','account_id','balance','frozen_amount','total_recharge','total_gift','is_active'],
    'wallet_transaction': ['id','wallet_id','amount','balance_before','balance_after','tx_type','order_id'],
    'wallet_withdrawrequest': ['id','user_id','account_id','amount','tax_amount','actual_amount','status'],
    'orders_order': ['id','order_no','status','payment_status','customer_id','customer_account_id','provider_id','quantity','amount','total_price'],
}
result = {}
with connection.cursor() as cursor:
    for table, fields in columns.items():
        existing = {c.name for c in connection.introspection.get_table_description(cursor, table)}
        fields = [field for field in fields if field in existing]
        cursor.execute('SELECT '+','.join(f'`{field}`' for field in fields)+f' FROM `{table}` ORDER BY id')
        rows = cursor.fetchall()
        payload=json.dumps(rows, default=lambda value: str(value) if isinstance(value, Decimal) else value.isoformat(),separators=(',',':')).encode()
        result[table]={'fields':fields,'rows':len(rows),'sha256':hashlib.sha256(payload).hexdigest()}
print(json.dumps(result,sort_keys=True))
