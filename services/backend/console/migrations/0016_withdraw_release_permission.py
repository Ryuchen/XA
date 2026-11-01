from django.db import migrations, models


def grant_owner_permission(apps, schema_editor):
    Role = apps.get_model('console', 'AdminRole')
    for role in Role.objects.using(schema_editor.connection.alias).filter(
        models.Q(code='super_admin') | models.Q(code__startswith='bootstrap_')
    ):
        permissions = set(role.permissions or [])
        if role.code == 'super_admin' or {'role:edit', 'admin:edit', 'audit:view'}.issubset(permissions):
            role.permissions = sorted(permissions | {'withdraw:release'})
            role.save(using=schema_editor.connection.alias, update_fields=['permissions'])


class Migration(migrations.Migration):
    dependencies = [('console', '0015_audition_provider_binding_permission')]
    operations = [migrations.RunPython(grant_owner_permission, migrations.RunPython.noop)]
