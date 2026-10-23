from django.db import migrations, models


KINDS = [('NAMING', '冠名播报'), ('RECHARGE', '充值播报'), ('UPGRADE', '升级播报'),
         ('REVIEW', '好评广播'), ('BIRTHDAY', '生日祝福'), ('GIFT', '礼物单播报'),
         ('LOTTERY', '转盘中奖播报'), ('LOTTERY_SERVICE', '抽中体验单'),
         ('LOTTERY_NAMING', '抽中冠名'), ('LOTTERY_TAG', '抽中 TAG')]


def preserve_published(apps, schema_editor):
    Broadcast = apps.get_model('engagement', 'Broadcast')
    Broadcast.objects.using(schema_editor.connection.alias).update(published_at=models.F('created_at'))


class Migration(migrations.Migration):
    dependencies = [('engagement', '0006_viptier_companion_service_ids')]
    operations = [
        migrations.AddField(model_name='broadcast', name='published_at', field=models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.RunPython(preserve_published, migrations.RunPython.noop),
        migrations.AlterField(model_name='broadcast', name='kind', field=models.CharField(choices=KINDS, max_length=24)),
        migrations.AlterField(model_name='broadcastrule', name='kind', field=models.CharField(choices=KINDS, max_length=24, unique=True)),
    ]
