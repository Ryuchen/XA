from django.db import migrations, models


def grant_owner_permission(apps, schema_editor):
    """Existing store owners retain configuration access; reviewers do not inherit it."""
    Role = apps.get_model('console', 'AdminRole')
    alias = schema_editor.connection.alias
    for role in Role.objects.using(alias).filter(
        models.Q(code='super_admin') | models.Q(code__startswith='bootstrap_')
    ):
        permissions = set(role.permissions or [])
        if role.code == 'super_admin' or {'role:edit', 'admin:edit', 'audit:view'}.issubset(permissions):
            role.permissions = sorted(permissions | {'withdraw:config'})
            role.save(using=alias, update_fields=['permissions'])


class Migration(migrations.Migration):
    dependencies = [('console', '0020_operations_owner_permissions')]
    operations = [migrations.RunPython(grant_owner_permission, migrations.RunPython.noop)]
