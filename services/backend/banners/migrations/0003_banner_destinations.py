from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('banners', '0002_remove_banner_image_url_banner_image')]
    operations = [migrations.AlterField(
        model_name='banner', name='link_type',
        field=models.CharField(max_length=20, default='NONE', verbose_name='跳转类型', choices=[
            ('NONE', '无跳转'), ('PRODUCT', '商品详情'), ('ANNOUNCEMENT', '公告详情'),
            ('GAMEPLAY', '玩法服务'), ('CONTENT', '活动与权益'), ('PAGE', '业务入口'), ('URL', '外部链接'),
        ]),
    )]
