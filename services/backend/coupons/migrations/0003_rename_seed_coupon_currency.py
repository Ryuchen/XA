from django.db import migrations


def rename_seed_coupons(apps, schema_editor):
    Coupon = apps.get_model('coupons', 'Coupon')
    # 旧联调数据升级为统一的兴安币命名。
    old_names = [
        ('联调无门槛' + '10' + '元券', '联调无门槛100兴安币券'),
        ('联调满50减8' + '元券', '联调满500减80兴安币券'),
    ]
    for old_name, new_name in old_names:
        Coupon.objects.filter(name=old_name).update(name=new_name)


class Migration(migrations.Migration):
    dependencies = [('coupons', '0002_alter_coupon_amount_alter_coupon_threshold')]

    operations = [migrations.RunPython(rename_seed_coupons, migrations.RunPython.noop)]
