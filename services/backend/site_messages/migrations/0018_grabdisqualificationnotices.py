import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0017_grabteamresultnotices'),
                    ('orders', '0255_grab_disqualification_notice_intents')]
    operations = [
        migrations.CreateModel(name='GrabDisqualificationNoticeBatch', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('completed_at', models.DateTimeField(blank=True, null=True)),
            ('admission', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='disqualification_notice_batch', to='orders.ordergrabsingleadmission')),
            ('team', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='disqualification_notice_batch', to='orders.ordergrabteamdraft')),
        ], options={'constraints': [models.CheckConstraint(
            condition=(models.Q(admission__isnull=False, team__isnull=True) |
                       models.Q(admission__isnull=True, team__isnull=False)), name='grab_disqual_one_source')]}),
        migrations.CreateModel(name='GrabDisqualificationNoticeDelivery', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('position', models.PositiveIntegerField()),
            ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='deliveries', to='site_messages.grabdisqualificationnoticebatch')),
            ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='+', to='site_messages.message')),
        ], options={'constraints': [models.UniqueConstraint(fields=('batch', 'position'),
                                                            name='grab_disqual_notice_once')]}),
    ]
