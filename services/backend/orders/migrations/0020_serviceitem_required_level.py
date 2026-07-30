import django.db.models.deletion
from django.db import migrations, models


def backfill_empty_level(apps, schema_editor):
    """把未设置档位的陪玩统一回填为最低档位（sort_order 最小的启用档位）。"""
    EscortLevel = apps.get_model('users', 'EscortLevel')
    EscortProfile = apps.get_model('users', 'EscortProfile')
    lowest = EscortLevel.objects.filter(is_active=True).order_by('sort_order', 'id').first()
    if lowest is None:
        return
    EscortProfile.objects.filter(level__isnull=True).update(level=lowest)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0019_normalize_service_category'),
        ('users', '0021_alter_customuser_openid_unique'),
    ]

    operations = [
        migrations.AddField(
            model_name='serviceitem',
            name='required_level',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='required_service_items', to='users.escortlevel',
            ),
        ),
        migrations.RunPython(backfill_empty_level, noop_reverse),
    ]
