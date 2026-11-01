from django.db import migrations, models


def bump_affected_pool_versions(apps, schema_editor):
    Pool = apps.get_model('lottery', 'Pool')
    Pool.objects.using(schema_editor.connection.alias).filter(
        slug__in=('red', 'grand'),
    ).update(version=models.F('version') + 1)


class Migration(migrations.Migration):
    dependencies = [('lottery', '0006_draw_batches')]
    operations = [
        migrations.AlterField(
            model_name='cell', name='probability',
            field=models.PositiveIntegerField(
                help_text='旧版联合权重，仅供历史兼容；现行概率见每次抽奖快照。',
            ),
        ),
        migrations.RunPython(bump_affected_pool_versions, migrations.RunPython.noop),
    ]
