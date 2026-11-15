from django.db import migrations, models


PERMISSIONS = {
    'dictionary:view', 'dictionary:edit',
    'salary:view', 'salary:edit', 'salary:confirm',
    'cost:view', 'cost:edit', 'roi:view',
}


def grant_owner_permissions(apps, schema_editor):
    """Keep existing owner access; salary privacy is not granted by finance name."""
    Role = apps.get_model('console', 'AdminRole')
    alias = schema_editor.connection.alias
    for role in Role.objects.using(alias).filter(
        models.Q(code='super_admin') | models.Q(code__startswith='bootstrap_')
    ):
        previous = set(role.permissions or [])
        if role.code == 'super_admin' or {'role:edit', 'admin:edit', 'audit:view'}.issubset(previous):
            role.permissions = sorted(previous | PERMISSIONS)
            role.save(using=alias, update_fields=['permissions'])


class Migration(migrations.Migration):
    dependencies = [('console', '0019_payroll')]
    operations = [migrations.RunPython(grant_owner_permissions, migrations.RunPython.noop)]
