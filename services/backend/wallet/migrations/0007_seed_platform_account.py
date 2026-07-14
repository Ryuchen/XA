from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations


def create_platform_account(apps, schema_editor):
    """创建平台系统账户（禁止登录）及其钱包，用于归集 shop_income。"""
    User = apps.get_model(settings.AUTH_USER_MODEL.split('.')[0], settings.AUTH_USER_MODEL.split('.')[1])
    Wallet = apps.get_model('wallet', 'Wallet')

    username = getattr(settings, 'PLATFORM_SYSTEM_USERNAME', '__platform__')
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={
            'password': make_password(None),  # 不可用密码，禁止登录
            'is_active': False,
            'is_staff': False,
            'is_superuser': False,
            'role': 'ADMIN',
            'nickname': '平台账户',
        },
    )
    # 历史模型不触发 post_save 建钱包信号，需显式创建
    Wallet.objects.get_or_create(user=user)


def remove_platform_account(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL.split('.')[0], settings.AUTH_USER_MODEL.split('.')[1])
    username = getattr(settings, 'PLATFORM_SYSTEM_USERNAME', '__platform__')
    User.objects.filter(username=username).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('wallet', '0006_alter_transaction_tx_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(create_platform_account, remove_platform_account),
    ]
