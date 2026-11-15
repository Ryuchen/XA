from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('console', '0036_payroll_performance_separation_waiver')]
    operations = [migrations.CreateModel(
        name='PayrollReemploymentRefundWaiver',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('source_payment_id', models.PositiveBigIntegerField()),
            ('reason', models.CharField(default='原周期工资已结清，原周期不追扣', max_length=100)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='refund_waivers', to='console.payrollreemploymentplan')),
            ('settlement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='console.payrollseparationsettlement')),
            ('source_run', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='console.payrollrun')),
            ('refund', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderrefund')),
            ('addon_refund', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonrefund')),
        ],
        options={'constraints': [
            models.UniqueConstraint(fields=('plan', 'refund'), name='payroll_reentry_refund_uniq'),
            models.UniqueConstraint(fields=('plan', 'addon_refund'), name='payroll_reentry_addon_uniq'),
            models.CheckConstraint(condition=(models.Q(refund__isnull=False, addon_refund__isnull=True)
                | models.Q(refund__isnull=True, addon_refund__isnull=False)), name='payroll_reentry_refund_source'),
        ]},
    )]
