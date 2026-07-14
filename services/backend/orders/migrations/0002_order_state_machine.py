# Generated for order state machine enhancement

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('orders', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', '待接单'),
                    ('GRABBED', '已接单'),
                    ('IN_SERVICE', '服务中'),
                    ('COMPLETED', '已完成'),
                    ('CANCELLED', '已取消'),
                ],
                db_index=True,
                default='PENDING',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='cancelled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='refunded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='cancel_reason',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='order',
            name='auto_cancel_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='reject_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='OrderStatusLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(
                    choices=[
                        ('CREATE', '创建'),
                        ('GRAB', '接单'),
                        ('ASSIGN', '派单'),
                        ('START', '开始服务'),
                        ('COMPLETE', '完成'),
                        ('REJECT', '拒单'),
                        ('CANCEL', '取消'),
                        ('REFUND', '退款'),
                        ('AUTO_CANCEL', '超时自动取消'),
                    ],
                    db_index=True,
                    max_length=20,
                )),
                ('from_status', models.CharField(blank=True, default='', max_length=20)),
                ('to_status', models.CharField(blank=True, default='', max_length=20)),
                ('reason', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='status_logs',
                    to='orders.order',
                )),
                ('operator', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='order_status_logs',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['order', 'created_at'], name='orders_orde_order_i_idx'),
                ],
            },
        ),
    ]
