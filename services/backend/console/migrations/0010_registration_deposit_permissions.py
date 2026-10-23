from django.db import migrations, models

FEATURE_PERMISSIONS = {'registration:view', 'registration:audit', 'deposit_rule:view', 'deposit_rule:edit'}


def grant_owner_permissions(apps, schema_editor):
    Role = apps.get_model('console', 'AdminRole')
    for role in Role.objects.filter(models.Q(code='super_admin') | models.Q(code__startswith='bootstrap_')):
        current = set(role.permissions)
        # Bootstrap roles may have been narrowed later; do not expand a restricted staff role.
        if role.code == 'super_admin' or {'role:edit', 'admin:edit', 'audit:view'}.issubset(current):
            role.permissions = sorted(current | FEATURE_PERMISSIONS)
            role.save(update_fields=['permissions'])


class Migration(migrations.Migration):
    dependencies = [('console', '0009_kookdispatchconfig_kookdispatchprobe')]
    operations = [migrations.RunPython(grant_owner_permissions, migrations.RunPython.noop)]
