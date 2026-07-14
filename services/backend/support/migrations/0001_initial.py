from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='SupportContactCard',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50, verbose_name='客服名')),
                ('company', models.CharField(blank=True, default='', max_length=100, verbose_name='公司')),
                ('wechat_id', models.CharField(blank=True, default='', max_length=50, verbose_name='微信号')),
                ('avatar_url', models.URLField(blank=True, default='', verbose_name='头像')),
                ('qrcode_url', models.URLField(blank=True, default='', verbose_name='二维码')),
                ('tips', models.TextField(blank=True, default='', verbose_name='提示语')),
                ('is_active', models.BooleanField(db_index=True, default=True, verbose_name='是否启用')),
                ('sort_order', models.PositiveIntegerField(default=0, verbose_name='排序')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': '客服名片',
                'verbose_name_plural': '客服名片',
                'ordering': ['sort_order', '-updated_at'],
            },
        ),
    ]
