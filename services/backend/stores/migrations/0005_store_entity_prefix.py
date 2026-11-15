import re

from django.core.validators import RegexValidator
from django.db import migrations, models


def populate_prefixes(apps, schema_editor):
    Store = apps.get_model('stores', 'Store')
    used = set()
    for store in Store.objects.all().order_by('id'):
        base = {'shenglang': 'SL', 'xinan': 'XA'}.get(store.slug)
        if not base:
            base = re.sub(r'[^A-Z0-9]', '', store.slug.upper())[:2].ljust(2, 'X')
        prefix = base
        suffix = 1
        while prefix in used:
            suffix += 1
            prefix = f'{base[:1]}{suffix}'[:4]
        used.add(prefix)
        store.entity_prefix = prefix
        store.save(update_fields=['entity_prefix'])


class Migration(migrations.Migration):
    dependencies = [('stores', '0004_store_logo_url')]
    operations = [
        migrations.AddField(
            model_name='store',
            name='entity_prefix',
            field=models.CharField(blank=True, default=None, max_length=4, null=True, unique=True, validators=[RegexValidator(r'^[A-Z0-9]{2,4}$', '编号前缀须为2至4位大写字母或数字')]),
        ),
        migrations.RunPython(populate_prefixes, migrations.RunPython.noop),
    ]
