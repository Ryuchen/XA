from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('console', '0035_catalog_write_lock')]

    operations = [
        migrations.CreateModel(
            name='PayrollReemployment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
                ('registered_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
                ('separation', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='reemployment', to='console.payrollseparationsettlement')),
            ],
        ),
        migrations.CreateModel(
            name='PayrollReemploymentPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plan_revision', models.PositiveIntegerField()),
                ('plan_snapshot', models.JSONField()),
                ('night_config_snapshot', models.JSONField(default=dict)),
                ('effective_at', models.DateTimeField(auto_now_add=True)),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='console.salaryplan')),
                ('registered_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
                ('reemployment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plans', to='console.payrollreemployment')),
            ],
        ),
        migrations.CreateModel(
            name='PayrollPerformanceSeparationWaiver',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount_fen', models.PositiveBigIntegerField()),
                ('reason', models.CharField(default='离职工资结清后发生退款，绩效不再追扣', max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('adjustment', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='separation_waiver', to='console.payrollrefundperformanceadjustment')),
                ('settlement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='performance_waivers', to='console.payrollseparationsettlement')),
            ],
        ),
    ]
