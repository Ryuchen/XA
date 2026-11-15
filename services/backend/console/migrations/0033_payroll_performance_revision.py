import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('console', '0032_platform_payroll_confirmation')]

    operations = [
        migrations.CreateModel(
            name='PayrollRefundPerformanceRevision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('delta_fen', models.BigIntegerField()),
                ('corrected_amount_fen', models.PositiveBigIntegerField()),
                ('evidence', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('adjustment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='revisions', to='console.payrollrefundperformanceadjustment')),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='performance_refund_revisions', to='console.payrollrun')),
            ],
            options={'constraints': [
                models.UniqueConstraint(fields=('adjustment', 'run'), name='payroll_perf_revision_unique'),
                models.CheckConstraint(condition=~models.Q(delta_fen=0), name='payroll_perf_revision_nonzero'),
            ]},
        ),
    ]
