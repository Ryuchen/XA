"""新增出账幂等键表 + 通行证定价配置种子。

手写迁移（本机无 Django 运行环境，无法 makemigrations），字段定义与
``wallet/models.py`` 中的 ``IdempotencyKey`` 逐项对齐。
"""

from django.db import migrations, models
import django.db.models.deletion

# 通行证档位出厂价（内部账务单位）与公共单池可见延迟（秒）。
# 与 wallet.models.DEFAULT_PASS_DAILY_PRICES / DEFAULT_PASS_DELAY_SECONDS
# 完全一致 —— 此处只是把历史硬编码「显式化」到配置表，行为不变。
PASS_DAILY_PRICES = {
    'BLACK': 5000,
    'GOLD': 3000,
    'SILVER': 1000,
    'BRONZE': 200,
}
PASS_DELAY_SECONDS = {
    'BLACK': 0,
    'GOLD': 30,
    'SILVER': 60,
    'BRONZE': 120,
    'NONE': 300,
}

PASS_DAILY_PRICE_KEY_PREFIX = 'provider_pass_daily_price_'
PASS_DELAY_SECONDS_KEY_PREFIX = 'provider_pass_delay_seconds_'


def seed_pass_config(apps, schema_editor):
    """写入通行证定价/延迟的默认配置。

    用 get_or_create：已经手工调过价的部署不会被这次迁移覆盖回默认值。
    """
    SystemConfig = apps.get_model('wallet', 'SystemConfig')
    for tier, price in PASS_DAILY_PRICES.items():
        SystemConfig.objects.get_or_create(
            key=f'{PASS_DAILY_PRICE_KEY_PREFIX}{tier}',
            defaults={
                'value': str(price),
                'remark': f'{tier} 通行证日价（内部账务单位，10=1兴安币）',
            },
        )
    for tier, seconds in PASS_DELAY_SECONDS.items():
        SystemConfig.objects.get_or_create(
            key=f'{PASS_DELAY_SECONDS_KEY_PREFIX}{tier}',
            defaults={
                'value': str(seconds),
                'remark': f'{tier} 档位公共单池可见延迟（秒）',
            },
        )


def unseed_pass_config(apps, schema_editor):
    """回滚时移除本迁移写入的配置键。"""
    SystemConfig = apps.get_model('wallet', 'SystemConfig')
    keys = [
        f'{PASS_DAILY_PRICE_KEY_PREFIX}{tier}' for tier in PASS_DAILY_PRICES
    ] + [
        f'{PASS_DELAY_SECONDS_KEY_PREFIX}{tier}' for tier in PASS_DELAY_SECONDS
    ]
    SystemConfig.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('club_accounts', '0002_clubaccount_club_account_type_active_idx_and_more'),
        ('wallet', '0020_add_withdraw_tax_and_adjust_tx_types'),
    ]

    operations = [
        migrations.CreateModel(
            name='IdempotencyKey',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'request_id',
                    models.CharField(
                        help_text='客户端生成的幂等键（client_request_id），建议 UUID hex',
                        max_length=64,
                    ),
                ),
                (
                    'scope',
                    models.CharField(
                        blank=True,
                        default='',
                        help_text='业务标注，仅用于排障，不参与唯一约束',
                        max_length=32,
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    'account',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='idempotency_keys',
                        to='club_accounts.clubaccount',
                    ),
                ),
            ],
            options={
                'verbose_name': 'IdempotencyKey',
                'verbose_name_plural': 'IdempotencyKeys',
            },
        ),
        migrations.AddIndex(
            model_name='idempotencykey',
            index=models.Index(
                fields=['account', '-created_at'],
                name='idempotency_account_time_idx',
            ),
        ),
        migrations.AddConstraint(
            model_name='idempotencykey',
            constraint=models.UniqueConstraint(
                fields=('account', 'request_id'),
                name='uniq_idempotency_account_request',
            ),
        ),
        migrations.RunPython(seed_pass_config, unseed_pass_config),
    ]
