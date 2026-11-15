import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0048_passreceiptusageledger')]

    operations = [
        migrations.CreateModel(
            name='ProviderWechatUnionOwner',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('unionid', models.CharField(max_length=100, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='provider_wechat_union_owners', to='club_accounts.clubaccount')),
            ],
        ),
        migrations.AddField(
            model_name='webloginsession',
            name='wechat_unionid',
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
