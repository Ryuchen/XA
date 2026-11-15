import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('stores', '0025_storeroutingrevision')]
    operations = [
        migrations.AddField(model_name='adminloginhandoff', name='platform_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='stores.platformaccount')),
        migrations.CreateModel(name='PlatformWechatIdentity', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('appid', models.CharField(max_length=64)), ('openid', models.CharField(max_length=100)),
            ('version', models.UUIDField(default=uuid.uuid4, editable=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('account', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='wechat_identity', to='stores.platformaccount')),
        ]),
        migrations.CreateModel(name='PlatformWechatBindingIntent', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('security_version', models.UUIDField()), ('created_at', models.DateTimeField(auto_now_add=True)),
            ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='+', to='stores.platformaccount')),
            ('handoff', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='platform_binding', to='stores.adminloginhandoff')),
        ]),
        migrations.AddConstraint(model_name='platformwechatidentity', constraint=models.UniqueConstraint(
            fields=('appid', 'openid'), name='platform_wechat_identity_unique')),
    ]
