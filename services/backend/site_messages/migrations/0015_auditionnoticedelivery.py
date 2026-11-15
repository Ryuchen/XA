import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0014_reservationeventnotices'), ('audition', '0005_signup_notice_cycle')]
    operations = [migrations.CreateModel(name='AuditionNoticeDelivery', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                         related_name='+', to='site_messages.message')),
        ('signup', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
            related_name='notice_delivery', to='audition.auditionsignup')),
    ])]
