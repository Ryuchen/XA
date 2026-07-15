from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0016_order_source_order')]

    operations = [
        migrations.CreateModel(
            name='KookDispatchRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sequence', models.PositiveIntegerField(default=0)),
                ('trigger', models.CharField(choices=[('NEW_ORDER', '新订单待派单'), ('PROVIDER_REJECTED', '陪玩拒单后二次派单')], max_length=30)),
                ('channel_id', models.CharField(max_length=64)),
                ('status', models.CharField(choices=[('PENDING', '待发送'), ('SENT', '已发送'), ('FAILED', '发送失败'), ('SKIPPED', '已跳过')], default='PENDING', max_length=12)),
                ('message_id', models.CharField(blank=True, default='', max_length=100)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('last_error', models.CharField(blank=True, default='', max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='kook_dispatches', to='orders.order')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='kookdispatchrecord',
            constraint=models.UniqueConstraint(fields=('order', 'sequence'), name='uniq_kook_dispatch_order_sequence'),
        ),
    ]
