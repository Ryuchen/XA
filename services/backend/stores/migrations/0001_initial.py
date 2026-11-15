from django.core.validators import RegexValidator
from django.db import migrations, models
from django.utils import timezone


def seed_default_store(apps, schema_editor):
    from django.conf import settings
    if schema_editor.connection.alias != 'default' or getattr(settings, 'ISOLATED_CONTROL_PLANE', False):
        return
    apps.get_model('stores', 'Store').objects.using('default').get_or_create(
        slug='shenglang', defaults={
            'name': '声浪电竞', 'database_alias': 'default',
            'status': 'ACTIVE', 'initialized_at': timezone.now(),
        },
    )


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(name='Store', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('slug', models.SlugField(max_length=48, unique=True, validators=[RegexValidator(r'^[a-z][a-z0-9-]{0,47}$', '门店标识须以小写字母开头，仅含小写字母、数字和短横线')])),
            ('name', models.CharField(max_length=80)),
            ('database_alias', models.CharField(max_length=64, unique=True, editable=False)),
            ('status', models.CharField(choices=[('PROVISIONING', '待初始化'), ('ACTIVE', '营业中'), ('DISABLED', '已停用')], db_index=True, default='PROVISIONING', max_length=16)),
            ('initialized_at', models.DateTimeField(blank=True, null=True)),
            ('boss_url', models.URLField(blank=True, default='', max_length=500)),
            ('provider_url', models.URLField(blank=True, default='', max_length=500)),
            ('admin_url', models.URLField(blank=True, default='', max_length=500)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
        ], options={'ordering': ['id'], 'verbose_name': '门店', 'verbose_name_plural': '门店',
                    'constraints': [models.CheckConstraint(condition=models.Q(status__in=['PROVISIONING', 'ACTIVE', 'DISABLED']), name='store_status_valid')]}),
        migrations.CreateModel(name='StorePlatformGrant', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('account_id', models.PositiveBigIntegerField(unique=True)),
            ('is_active', models.BooleanField(default=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
        ], options={'verbose_name': '平台门店管理授权', 'verbose_name_plural': '平台门店管理授权'}),
        migrations.RunPython(seed_default_store, migrations.RunPython.noop),
    ]
