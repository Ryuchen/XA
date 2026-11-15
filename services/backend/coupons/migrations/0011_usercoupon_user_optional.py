from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('coupons', '0010_remove_usercoupon_uniq_account_coupon_and_more'),
                    migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [migrations.AlterField(
        model_name='usercoupon', name='user',
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                related_name='coupons', to=settings.AUTH_USER_MODEL),
    )]
