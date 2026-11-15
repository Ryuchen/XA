from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0055_customer_game_profile_account_owner')]
    operations = [
        migrations.AddField(
            model_name='escortprofile', name='avatar_source',
            field=models.CharField(max_length=12, default='UNKNOWN', editable=False,
                choices=[('UNKNOWN', '未知'), ('WECHAT', '微信'), ('UPLOAD', '本人上传'), ('STAFF_UPLOAD', '后台上传')]),
        ),
        migrations.AddField(model_name='webloginsession', name='expected_business_cycle',
                            field=models.PositiveIntegerField(null=True, blank=True, editable=False)),
    ]
