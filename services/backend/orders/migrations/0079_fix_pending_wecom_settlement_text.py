from django.db import migrations
from django.db.models import F, Value
from django.db.models.functions import Replace


OLD = '\n以上为本单打手实得，报单审核通过后结算。\n请到打手端确认订单状态并接单。'
NEW = '\n以上为本单打手分账金额，实际入账以订单结算和钱包流水为准。\n请到打手端确认订单状态并接单。'


def update_pending_wecom(apps, schema_editor):
    Record = apps.get_model('orders', 'WecomDispatchRecord')
    Record.objects.filter(status='PENDING', content__endswith=OLD).update(
        content=Replace(F('content'), Value(OLD), Value(NEW)))


class Migration(migrations.Migration):
    dependencies = [('orders', '0078_alter_evaluation_order_and_more')]
    operations = [migrations.RunPython(update_pending_wecom, migrations.RunPython.noop)]
