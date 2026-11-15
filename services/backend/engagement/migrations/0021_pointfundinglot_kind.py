from django.db import migrations, models


def mark_recharge_lots(apps, schema_editor):
    PointFundingLot = apps.get_model('engagement', 'PointFundingLot')
    PointFundingLot.objects.filter(source_key__regex=r'^recharge:[0-9]+$').update(kind='RECHARGE')


def restore_prepaid_lots(apps, schema_editor):
    PointFundingLot = apps.get_model('engagement', 'PointFundingLot')
    PointFundingLot.objects.filter(kind='RECHARGE').update(kind='PREPAID')


class Migration(migrations.Migration):
    dependencies = [('engagement', '0020_rewardclaim_tag_revision_required')]
    operations = [
        migrations.AddField(
            model_name='pointfundinglot',
            name='kind',
            field=models.CharField(
                choices=[('PREPAID', '预存'), ('GIFT', '赠送'), ('COMPENSATION', '赔款'), ('RECHARGE', '充值混合批次')],
                db_index=True, default='PREPAID', max_length=16,
            ),
        ),
        migrations.RunPython(mark_recharge_lots, restore_prepaid_lots),
    ]
