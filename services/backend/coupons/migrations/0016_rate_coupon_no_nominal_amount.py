from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('coupons', '0015_vipmonthlycutover')]

    operations = [
        migrations.RemoveConstraint(model_name='coupon', name='coupon_amount_positive'),
        migrations.AddConstraint(
            model_name='coupon',
            constraint=models.CheckConstraint(
                condition=(models.Q(discount_type='QUALIFICATION', amount=0)
                           | models.Q(discount_type='RATE', amount__gte=0)
                           | models.Q(discount_type__in=['THRESHOLD', 'DIRECT'], amount__gt=0)),
                name='coupon_amount_positive',
            ),
        ),
    ]
