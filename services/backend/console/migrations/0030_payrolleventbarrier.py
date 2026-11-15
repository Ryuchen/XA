from django.db import migrations, models


def seed_barrier(apps, schema_editor):
    apps.get_model('console', 'PayrollEventBarrier').objects.using(
        schema_editor.connection.alias).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [('console', '0029_payrollrefunddeduction_addon_refund_and_more')]

    operations = [
        migrations.CreateModel(
            name='PayrollEventBarrier',
            fields=[('id', models.PositiveSmallIntegerField(default=1, editable=False,
                                                             primary_key=True, serialize=False))],
            options={'constraints': [models.CheckConstraint(condition=models.Q(id=1),
                                                              name='payroll_event_barrier_singleton')]},
        ),
        migrations.RunPython(seed_barrier, migrations.RunPython.noop),
    ]
