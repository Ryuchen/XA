from django.db import migrations, models


def grant_content_permissions(apps, schema_editor):
    role_model = apps.get_model('console', 'AdminRole')
    alias = schema_editor.connection.alias
    for role in role_model.objects.using(alias).filter(
        models.Q(code='super_admin') | models.Q(code__startswith='bootstrap_')
    ):
        permissions = set(role.permissions or [])
        if role.code == 'super_admin' or {'role:edit', 'admin:edit', 'audit:view'}.issubset(permissions):
            role.permissions = sorted(permissions | {'content:view', 'content:official_name'})
            role.save(using=alias, update_fields=['permissions'])


class Migration(migrations.Migration):
    dependencies = [('console', '0039_contentcompliancereceipt')]
    operations = [migrations.RunPython(grant_content_permissions, migrations.RunPython.noop)]
