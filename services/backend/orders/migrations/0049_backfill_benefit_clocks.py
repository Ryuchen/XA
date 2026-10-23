from datetime import timedelta
from django.db import migrations


def backfill(apps, schema_editor):
    alias = schema_editor.connection.alias
    Category = apps.get_model('orders', 'ServiceCategory')
    for name, kind in {'护航':'GUARD','陪玩':'COMPANION','陪玩单':'COMPANION','代肝':'GRIND','上分':'RANK'}.items():
        Category.objects.using(alias).filter(name=name, is_gift=False, order_kind='').update(order_kind=kind)
    Service = apps.get_model('orders', 'ServiceItem')
    Service.objects.using(alias).filter(commission_policy='COMPANION').update(service_type='COMPANION')
    Service.objects.using(alias).filter(gameplay__theme__iexact='experience').update(service_type='EXPERIENCE')
    Report = apps.get_model('wallet', 'ProviderReport')
    # Old reports did not freeze submission time. Their latest update is a
    # conservative lower bound for the new 72-hour window, never an early grant.
    for report in Report.objects.using(alias).filter(submitted_at__isnull=True, status__in=['PENDING','APPROVED']).iterator(chunk_size=200):
        Report.objects.using(alias).filter(pk=report.pk).update(submitted_at=report.updated_at)
    Order = apps.get_model('orders', 'Order')
    Order.objects.using(alias).filter(service__service_category__is_gift=True).update(is_gift_snapshot=True)
    for order in Order.objects.using(alias).filter(status='COMPLETED', payment_status='PAID').iterator(chunk_size=200):
        shares = list(order.providers.using(alias).all())
        required = {row.provider_id for row in shares if row.settlement_base > 0 or not row.released_at} if shares else {order.provider_id}
        reports = list(Report.objects.using(alias).filter(order_id=order.pk, provider_id__in=required))
        if required and None not in required and len(reports) == len(required) and all(row.submitted_at and row.status in ['PENDING','APPROVED'] for row in reports):
            ready = max(row.submitted_at for row in reports)
            Order.objects.using(alias).filter(pk=order.pk).update(report_ready_at=ready, consumption_ready_at=ready+timedelta(hours=72))


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0048_serviceitem_allows_discount_coupons'),
        ('wallet', '0029_providerreport_submitted_at'),
    ]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
