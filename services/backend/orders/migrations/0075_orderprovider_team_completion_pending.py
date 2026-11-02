from django.db import migrations, models
from django.db.models import Exists, OuterRef


def reconcile_inflight_team_transfers(apps, schema_editor):
    """Old transfers have no per-member re-entry timestamp; refresh current evidence."""
    alias = schema_editor.connection.alias
    assignment = apps.get_model('orders', 'OrderProvider')
    reports = apps.get_model('wallet', 'ProviderReport').objects.using(alias).filter(
        order_id=OuterRef('order_id'), provider_account_id=OuterRef('provider_account_id'),
    ).exclude(completion_image__isnull=True).exclude(completion_image='')
    transfers = apps.get_model('orders', 'OrderStatusLog').objects.using(alias).filter(
        order_id=OuterRef('order_id'), action='TRANSFER',
    )
    assignment.objects.using(alias).filter(
        order__status='IN_SERVICE', order__escort_mode='DOUBLE',
        order__requires_boss_selection=True, released_at__isnull=True,
        provider_account_id__isnull=False,
    ).annotate(has_completion=Exists(reports), had_transfer=Exists(transfers)).filter(
        has_completion=True, had_transfer=True,
    ).update(team_completion_pending=True)


class Migration(migrations.Migration):
    dependencies = [('orders', '0074_direct_refund_inbox_done')]

    operations = [
        migrations.AddField(
            model_name='orderprovider',
            name='team_completion_pending',
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.RunPython(reconcile_inflight_team_transfers, migrations.RunPython.noop),
    ]
