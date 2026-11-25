import hashlib
import json

from django.db import migrations


CONTENT_KEYS = frozenset({'name', 'display_name', 'introduction', 'rules', 'details_html'})
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


def scrub_content_receipt_actor_labels(apps, schema_editor):
    alias = schema_editor.connection.alias
    receipt = apps.get_model('console', 'ContentComplianceReceipt')
    rows = receipt.objects.using(alias).only(
        'pk', 'actor_type', 'actor_id', 'actor_account_id', 'actor_label',
    ).iterator()
    for row in rows:
        label = normalized_actor_label(row.actor_type, row.actor_id or row.actor_account_id, row.actor_label)
        if row.actor_label != label:
            receipt.objects.using(alias).filter(pk=row.pk).update(actor_label=label)


def mask_audit_content(value):
    if isinstance(value, dict):
        masked = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in CONTENT_KEYS:
                digest = item.get('sha256') if isinstance(item, dict) and set(item) == {'sha256'} else None
                if (isinstance(digest, str) and len(digest) == 64
                        and all(char in '0123456789abcdef' for char in digest)):
                    masked[key] = item
                else:
                    content = item if isinstance(item, str) else json.dumps(
                        item, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                    )
                    masked[key] = {'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}
            else:
                masked[key] = mask_audit_content(item)
        return masked
    if isinstance(value, list):
        return [mask_audit_content(item) for item in value]
    return value


def scrub_audit_content(apps, schema_editor):
    alias = schema_editor.connection.alias
    audit = apps.get_model('console', 'AdminAuditLog')
    rows = audit.objects.using(alias).only('pk', 'request_body').iterator()
    for row in rows:
        masked = mask_audit_content(row.request_body)
        if row.request_body != masked:
            audit.objects.using(alias).filter(pk=row.pk).update(request_body=masked)


class Migration(migrations.Migration):
    dependencies = [
        ('console', '0042_remove_contentcompliancereceipt_content_compliance_receipt_unique_and_more'),
    ]

    operations = [
        migrations.RunPython(scrub_content_receipt_actor_labels, migrations.RunPython.noop),
        migrations.RunPython(scrub_audit_content, migrations.RunPython.noop),
    ]
