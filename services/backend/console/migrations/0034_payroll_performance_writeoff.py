from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('console', '0033_payroll_performance_revision')]

    operations = [
        migrations.RemoveConstraint(model_name='payrollrefundperformanceapplication',
                                    name='payroll_performance_application_positive'),
        migrations.AddField(model_name='payrollrefundperformanceapplication', name='waived_fen',
                            field=models.PositiveBigIntegerField(default=0)),
        migrations.AddField(model_name='payrollrefundperformancerevision', name='applied_fen',
                            field=models.BigIntegerField(default=0)),
        migrations.AddField(model_name='payrollrefundperformancerevision', name='waived_fen',
                            field=models.PositiveBigIntegerField(default=0)),
    ]
