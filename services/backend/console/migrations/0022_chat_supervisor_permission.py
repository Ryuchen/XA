from django.db import migrations, models


def grant_existing_owners(apps, schema_editor):
    roles = apps.get_model('console', 'AdminRole').objects.using(schema_editor.connection.alias)
    for role in roles.filter(models.Q(code='super_admin') | models.Q(code__startswith='bootstrap_')):
        permissions = set(role.permissions or [])
        if role.code == 'super_admin' or {'role:edit', 'admin:edit', 'audit:view'}.issubset(permissions):
            role.permissions = sorted(permissions | {'chat:supervise'})
            role.save(using=schema_editor.connection.alias, update_fields=['permissions'])


class Migration(migrations.Migration):
    dependencies = [('console', '0021_withdraw_config_permission')]
    operations = [migrations.RunPython(grant_existing_owners, migrations.RunPython.noop)]
