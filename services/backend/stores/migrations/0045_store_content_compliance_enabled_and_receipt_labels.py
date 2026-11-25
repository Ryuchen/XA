from django.db import migrations, models


ACTOR_LABEL_PREFIXES = {'SYSTEM': 'USER', 'STORE_STAFF': 'ACCOUNT', 'PLATFORM_ACCOUNT': 'PLATFORM_ACCOUNT'}
ACTOR_TYPES = frozenset({'SYSTEM', 'STORE_STAFF', 'PLATFORM_ACCOUNT'})
FIXED_PUBLIC_LABELS = frozenset({'SYSTEM', 'PUBLIC_REGISTRATION'})


def normalized_actor_label(actor_type, actor_id, actor_label):
    actor_type = str(actor_type or 'SYSTEM')
    actor_id = '' if actor_id in (None, '') else str(actor_id)
    if actor_id:
        prefix = ACTOR_LABEL_PREFIXES.get(actor_type, 'ACTOR')
        return f'{prefix}:{actor_id}'[:150]
    if actor_label in FIXED_PUBLIC_LABELS:
        return actor_label
    return actor_type if actor_type in ACTOR_TYPES else 'SYSTEM'


def scrub_store_receipt_actor_labels(apps, schema_editor):
    alias = schema_editor.connection.alias
    receipt = apps.get_model('stores', 'StoreContentComplianceReceipt')
    rows = receipt.objects.using(alias).only('pk', 'actor_type', 'actor_id', 'actor_label').iterator()
    for row in rows:
        label = normalized_actor_label(row.actor_type, row.actor_id, row.actor_label)
        if row.actor_label != label:
            receipt.objects.using(alias).filter(pk=row.pk).update(actor_label=label)


class Migration(migrations.Migration):
    dependencies = [
        ('stores', '0044_remove_platformrolegrant_platform_role_valid_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='content_compliance_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(scrub_store_receipt_actor_labels, migrations.RunPython.noop),
    ]
