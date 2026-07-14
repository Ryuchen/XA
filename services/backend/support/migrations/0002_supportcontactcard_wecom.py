from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('support', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='supportcontactcard',
            name='wecom_corp_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='企业微信「我的企业」中的企业ID，用于小程序打开微信客服',
                max_length=64,
                verbose_name='企业微信企业ID',
            ),
        ),
        migrations.AddField(
            model_name='supportcontactcard',
            name='wecom_service_url',
            field=models.URLField(
                blank=True,
                default='',
                help_text='企业微信微信客服后台生成的 https://work.weixin.qq.com/kfid/... 链接',
                verbose_name='微信客服接入链接',
            ),
        ),
    ]
