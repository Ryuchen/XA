from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0217_grab_receipt_clock_evidence'),
        ('club_accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderGrabReviewCancellation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(choices=[('PENDING', '待处理'), ('CANCELLED', '已退款取消')], default='PENDING', max_length=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='grab_review_cancellation', to='orders.order')),
            ],
        ),
    ]
