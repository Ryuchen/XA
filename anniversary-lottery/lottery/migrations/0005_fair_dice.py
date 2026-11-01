from django.db import migrations, models


def bump_fair_dice_versions(apps, schema_editor):
    Pool = apps.get_model('lottery', 'Pool')
    Pool.objects.using(schema_editor.connection.alias).update(version=models.F('version') + 1)


class Migration(migrations.Migration):
    dependencies = [('lottery', '0004_ordinary_soft_pity')]
    operations = [migrations.RunPython(bump_fair_dice_versions, migrations.RunPython.noop)]
