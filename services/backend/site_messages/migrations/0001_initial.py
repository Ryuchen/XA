import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Message',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.CharField(choices=[('SYSTEM', '系统通知'), ('ORDER', '订单消息'), ('SUPPORT', '客服消息'), ('PROMOTION', '活动通知')], db_index=True, default='SYSTEM', max_length=20)),
                ('title', models.CharField(max_length=100)),
                ('preview', models.CharField(blank=True, default='', max_length=255)),
                ('detail', models.TextField(blank=True, default='')),
                ('action_url', models.CharField(blank=True, default='', max_length=255)),
                ('is_read', models.BooleanField(db_index=True, default=False)),
                ('related_order_id', models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': '站内消息',
                'verbose_name_plural': '站内消息',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['recipient', 'is_read', '-created_at'], name='site_messag_recipie_idx')],
            },
        ),
    ]
