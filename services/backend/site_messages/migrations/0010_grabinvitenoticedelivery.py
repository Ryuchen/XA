import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('site_messages', '0009_message_recipient_business_cycle'),
        ('orders', '0248_grab_invitee_business_cycle'),
    ]
    operations = [migrations.CreateModel(
        name='GrabInviteNoticeDelivery',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                             related_name='+', to='site_messages.message')),
            ('receipt', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                             related_name='+', to='orders.ordergrabreceiptprobe')),
        ],
    )]
