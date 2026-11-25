from django.db import migrations, transaction
from django.utils import timezone

from common.compliance import BUILTIN_RULES_SHA256, BUILTIN_RULES_V1, BUILTIN_VERSION


def publish_builtin_v1(apps, schema_editor):
    alias = schema_editor.connection.alias
    version_model = apps.get_model('stores', 'ComplianceRuleVersion')
    state_model = apps.get_model('stores', 'ComplianceRuleState')
    with transaction.atomic(using=alias):
        row, _created = version_model.objects.using(alias).get_or_create(
            version=BUILTIN_VERSION,
            defaults={
                'status': 'PUBLISHED',
                'rules': list(BUILTIN_RULES_V1),
                'rules_sha256': BUILTIN_RULES_SHA256,
                'published_at': timezone.now(),
            },
        )
        state_model.objects.using(alias).update_or_create(
            pk=1, defaults={'current_version': row, 'updated_at': timezone.now()},
        )


class Migration(migrations.Migration):
    dependencies = [('stores', '0040_complianceruleversion_compliancerulestate_and_more')]
    operations = [migrations.RunPython(publish_builtin_v1, migrations.RunPython.noop)]
