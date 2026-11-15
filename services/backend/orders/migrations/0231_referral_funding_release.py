from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0230_ordinary_transfer_funding_release')]
    operations = [migrations.CreateModel(name='ReferralRefundFundingRelease', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('request_key', models.CharField(max_length=160, unique=True)), ('actor', models.CharField(max_length=64)),
        ('reason', models.CharField(max_length=200)), ('result', models.JSONField()),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('allocation', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
            related_name='release', to='orders.referralrefundfunding')),
    ])]
