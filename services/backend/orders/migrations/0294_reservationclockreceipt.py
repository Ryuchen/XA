# Generated for reservation tenant-DB clock evidence.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0293_grab_review_cancellation_request_key'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReservationClockReceipt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('seat_number', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('action', models.CharField(max_length=24)),
                ('decision_key', models.CharField(max_length=64, unique=True)),
                ('deadline', models.DateTimeField()),
                ('lower_at', models.DateTimeField()),
                ('upper_at', models.DateTimeField(blank=True, null=True)),
                ('precision_us', models.PositiveIntegerField()),
                ('database_identity', models.CharField(max_length=64)),
                ('connection_id', models.PositiveIntegerField()),
                ('process_id', models.PositiveIntegerField()),
                ('monotonic_before_ns', models.BigIntegerField()),
                ('monotonic_after_ns', models.BigIntegerField()),
                ('upper_database_identity', models.CharField(blank=True, default='', max_length=64)),
                ('upper_connection_id', models.PositiveIntegerField(blank=True, null=True)),
                ('upper_process_id', models.PositiveIntegerField(blank=True, null=True)),
                ('upper_monotonic_before_ns', models.BigIntegerField(blank=True, null=True)),
                ('upper_monotonic_after_ns', models.BigIntegerField(blank=True, null=True)),
                ('state', models.CharField(choices=[('PENDING', '待取上界'), ('BEFORE', '上界早于截止'), ('AFTER', '下界已到截止'), ('REVIEW', '需人工核对')], default='PENDING', max_length=8)),
                ('review_reason', models.CharField(blank=True, default='', max_length=40)),
                ('state_before', models.CharField(blank=True, default='', max_length=20)),
                ('state_after', models.CharField(blank=True, default='', max_length=20)),
                ('before_snapshot', models.JSONField(blank=True, default=dict)),
                ('request_fingerprint', models.CharField(blank=True, default='', max_length=64)),
                ('fingerprint', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor_account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
                ('reservation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='clock_receipts', to='orders.reservation')),
            ],
            options={
                'ordering': ['id'],
                'indexes': [
                    models.Index(fields=['reservation', 'state'], name='reservation_clock_state_idx'),
                    models.Index(fields=['state', 'created_at'], name='reservation_clock_scan_idx'),
                ],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(state__in=['PENDING', 'BEFORE', 'AFTER', 'REVIEW']), name='reservation_clock_state_valid'),
                    models.CheckConstraint(condition=(models.Q(state__in=['PENDING', 'REVIEW'], upper_at__isnull=True) | models.Q(state='REVIEW', upper_at__isnull=False) | models.Q(state__in=['BEFORE', 'AFTER'], upper_at__isnull=False)), name='reservation_clock_upper_required'),
                    models.CheckConstraint(condition=models.Q(monotonic_after_ns__gte=models.F('monotonic_before_ns')), name='reservation_clock_monotonic_valid'),
                ],
            },
        ),
    ]
