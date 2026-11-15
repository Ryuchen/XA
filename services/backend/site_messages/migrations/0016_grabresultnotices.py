import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0015_auditionnoticedelivery'),
                    ('orders', '0253_single_grab_notice_intents')]
    operations = [
        migrations.CreateModel(name='GrabResultNoticeBatch', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('completed_at', models.DateTimeField(blank=True, null=True)),
            ('state', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='notice_batch', to='orders.ordergrabsingleroundstate')),
        ]),
        migrations.CreateModel(name='GrabResultNoticeDelivery', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('position', models.PositiveIntegerField()),
            ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                             related_name='+', to='site_messages.message')),
            ('state', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='notice_deliveries', to='orders.ordergrabsingleroundstate')),
        ], options={'constraints': [models.UniqueConstraint(fields=('state', 'position'), name='grab_result_notice_once')]}),
    ]
