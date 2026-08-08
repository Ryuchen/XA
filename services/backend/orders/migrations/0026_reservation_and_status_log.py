"""预约（ORD-1/2）建表：Reservation + ReservationStatusLog。

纯新增，不触碰 ``Order`` 的任何列与约束 —— 预约与订单是两套独立的
ID 空间，本期只把「时间占用」这层能力单独立起来。

时间重叠检测刻意不使用 Postgres 的 ``tstzrange`` + ``ExclusionConstraint``：
生产库是 MySQL、测试库是 sqlite，两者都不支持排他约束。重叠判定统一走
「陪玩档案行锁 + 区间查询」（见 ``orders/reservation_services.py``），
这样三种数据库上的行为完全一致。
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('club_accounts', '0002_clubaccount_club_account_type_active_idx_and_more'),
        ('orders', '0025_backfill_gift_order_accounts'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Reservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reservation_no', models.CharField(blank=True, db_index=True, default='', max_length=32, unique=True)),
                ('start_time', models.DateTimeField(db_index=True)),
                ('end_time', models.DateTimeField(db_index=True)),
                ('duration_minutes', models.PositiveIntegerField(default=0)),
                ('game_rounds', models.PositiveIntegerField(default=1)),
                ('estimated_amount', models.PositiveIntegerField(default=0)),
                ('service_name_snapshot', models.CharField(blank=True, default='', max_length=100)),
                ('provider_name_snapshot', models.CharField(blank=True, default='', max_length=50)),
                ('game_region', models.CharField(blank=True, default='', max_length=50)),
                ('game_nickname', models.CharField(blank=True, default='', max_length=50)),
                ('game_uid', models.CharField(blank=True, default='', max_length=50)),
                ('remark', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(choices=[('PENDING', '待确认'), ('CONFIRMED', '已确认'), ('REJECTED', '已拒绝'), ('CANCELLED', '已取消'), ('CONVERTED', '已转订单'), ('EXPIRED', '已过期')], db_index=True, default='PENDING', max_length=20)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('rejected_at', models.DateTimeField(blank=True, null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('converted_at', models.DateTimeField(blank=True, null=True)),
                ('expired_at', models.DateTimeField(blank=True, null=True)),
                ('cancel_reason', models.CharField(blank=True, default='', max_length=255)),
                ('reject_reason', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='placed_reservations', to=settings.AUTH_USER_MODEL)),
                ('customer_account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='placed_reservations', to='club_accounts.clubaccount')),
                ('order', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reservation', to='orders.order')),
                ('provider', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='received_reservations', to=settings.AUTH_USER_MODEL)),
                ('provider_account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='received_reservations', to='club_accounts.clubaccount')),
                ('service', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='orders.serviceitem')),
            ],
            options={
                'ordering': ['-start_time', '-id'],
            },
        ),
        migrations.CreateModel(
            name='ReservationStatusLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('CREATE', '创建预约'), ('CONFIRM', '确认预约'), ('REJECT', '拒绝预约'), ('CANCEL', '取消预约'), ('CONVERT', '转为订单'), ('EXPIRE', '过期失效')], db_index=True, max_length=20)),
                ('from_status', models.CharField(blank=True, default='', max_length=20)),
                ('to_status', models.CharField(blank=True, default='', max_length=20)),
                ('reason', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('operator', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reservation_status_logs', to=settings.AUTH_USER_MODEL)),
                ('operator_account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reservation_status_logs', to='club_accounts.clubaccount')),
                ('reservation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='status_logs', to='orders.reservation')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['provider_account', 'status', 'start_time'], name='reservation_provider_idx'),
        ),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['customer_account', 'status', '-start_time'], name='reservation_customer_idx'),
        ),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['status', 'end_time'], name='reservation_expire_scan_idx'),
        ),
        migrations.AddConstraint(
            model_name='reservation',
            constraint=models.CheckConstraint(condition=models.Q(('end_time__gt', models.F('start_time'))), name='reservation_time_window_valid'),
        ),
        migrations.AddConstraint(
            model_name='reservation',
            constraint=models.CheckConstraint(condition=models.Q(('duration_minutes__gte', 1)), name='reservation_duration_positive'),
        ),
        migrations.AddConstraint(
            model_name='reservation',
            constraint=models.CheckConstraint(condition=models.Q(('game_rounds__gte', 1)), name='reservation_rounds_positive'),
        ),
        migrations.AddConstraint(
            model_name='reservation',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['PENDING', 'CONFIRMED', 'REJECTED', 'CANCELLED', 'CONVERTED', 'EXPIRED'])), name='reservation_status_valid'),
        ),
        migrations.AddIndex(
            model_name='reservationstatuslog',
            index=models.Index(fields=['reservation', 'created_at'], name='orders_rese_reserva_8ebdb6_idx'),
        ),
    ]
