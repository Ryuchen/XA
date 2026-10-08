from django.db import migrations

SUPPORT = {'wallet:view','wallet:recharge','recharge:view','transaction:view',
    'order:dispatch','escort:view','service:view','chat:view','chat:reply',
    'registration:view','registration:audit'}
FINANCE = {'wallet:recharge','recharge:view','withdraw:view','withdraw:audit',
    'report:view','report:audit','order:settle','audit:view','deposit_rule:view'}

def grant_staff_permissions(apps, schema_editor):
    Role = apps.get_model('console','AdminRole')
    for role in Role.objects.filter(code__in=['support','finance','super_admin']):
        if role.code == 'super_admin':
            from console.permissions import ALL_PERMISSION_SET
            extra = ALL_PERMISSION_SET
        else:
            extra = SUPPORT if role.code == 'support' else FINANCE
        role.permissions = sorted(set(role.permissions) | extra)
        role.save(update_fields=['permissions'])

class Migration(migrations.Migration):
    dependencies = [('console','0010_registration_deposit_permissions')]
    operations = [migrations.RunPython(grant_staff_permissions,migrations.RunPython.noop)]
