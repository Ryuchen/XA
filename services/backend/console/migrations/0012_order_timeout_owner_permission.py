from django.db import migrations, models


def grant_owner_permissions(apps, schema_editor):
    Role = apps.get_model('console', 'AdminRole')
    for role in Role.objects.using(schema_editor.connection.alias).filter(
        models.Q(code='super_admin') | models.Q(code__startswith='bootstrap_')
    ):
        current = set(role.permissions)
        if role.code == 'super_admin' or {'role:edit', 'admin:edit', 'audit:view'}.issubset(current):
            role.permissions = sorted(current | {'order:timeout'})
            role.save(using=schema_editor.connection.alias, update_fields=['permissions'])


class Migration(migrations.Migration):
    dependencies = [('console', '0011_staff_recharge_permissions')]
    operations = [migrations.RunPython(grant_owner_permissions, migrations.RunPython.noop)]
