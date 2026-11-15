from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('coupons', '0011_usercoupon_user_optional'),
    ]

    operations = [
        migrations.AlterField(
            model_name='coupon',
            name='threshold',
            field=models.IntegerField(default=0, verbose_name='使用门槛（浪花账务值）'),
        ),
        migrations.AlterField(
            model_name='coupon',
            name='amount',
            field=models.IntegerField(verbose_name='面额（浪花账务值）'),
        ),
    ]
