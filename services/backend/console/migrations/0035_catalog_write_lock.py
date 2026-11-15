from django.db import migrations, models


def seed_lock(apps, schema_editor):
    lock = apps.get_model('console', 'CatalogWriteLock')
    lock.objects.using(schema_editor.connection.alias).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [('console', '0034_payroll_performance_writeoff')]

    operations = [
        migrations.CreateModel(name='CatalogWriteLock', fields=[
            ('id', models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False, serialize=False)),
        ], options={'constraints': [models.CheckConstraint(
            condition=models.Q(id=1), name='catalog_write_lock_singleton')]}),
        migrations.RunPython(seed_lock, migrations.RunPython.noop),
    ]
