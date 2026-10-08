from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0026_alter_customuser_options_alter_customuser_boss_no_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='WechatPhoneLock',
            fields=[('phone', models.CharField(max_length=20, primary_key=True, serialize=False))],
            options={'verbose_name': '微信手机号绑定锁', 'verbose_name_plural': '微信手机号绑定锁'},
        ),
    ]
