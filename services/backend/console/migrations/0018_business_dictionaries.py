from django.core.validators import RegexValidator
from django.db import migrations, models


POSITIONS = [
    ('STORE_MANAGER', '店长'),
    ('CUSTOMER_SERVICE_FULLTIME', '客服（全职）'),
    ('CUSTOMER_SERVICE_PARTTIME', '客服（兼职）'),
    ('PROVIDER_MANAGER', '打手管理'),
    ('AFTERSALE_MANAGER', '售后管理'),
    ('OPERATIONS', '运营'),
    ('EXAMINER_SHENGLANG', '考官（声浪）'),
    ('EXAMINER_XINGAN', '考官（兴安）'),
]


def seed(apps, schema_editor):
    Item = apps.get_model('console', 'BusinessDictionaryItem')
    for index, (code, name) in enumerate(POSITIONS):
        Item.objects.get_or_create(type='SALARY_POSITION', code=code, defaults={
            'name': name, 'sort_order': index * 10,
            'remark': '仅用于薪资配置；后台权限仍由角色权限独立管理',
        })
    for index, (code, name) in enumerate([('UNKNOWN', '未知 / 未归因'), ('OWNED', '自有渠道')]):
        Item.objects.get_or_create(type='AD_CHANNEL', code=code, defaults={
            'name': name, 'sort_order': index * 10,
            'remark': '渠道字典不代表已发生投流，不预置成本或收入归因',
        })
    for index, (code, name) in enumerate([('AD_SPEND', '投流成本'), ('OTHER', '其他运营成本')]):
        Item.objects.get_or_create(type='COST_CATEGORY', code=code, defaults={
            'name': name, 'sort_order': index * 10,
        })


class Migration(migrations.Migration):
    dependencies = [('console', '0017_update_default_dispatch_settlement_text')]
    operations = [
        migrations.CreateModel(
            name='BusinessDictionaryItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.CharField(choices=[('SALARY_POSITION', '薪资岗位'), ('AD_CHANNEL', '投流渠道'), ('COST_CATEGORY', '运营成本类别')], db_index=True, max_length=24)),
                ('code', models.CharField(max_length=64, validators=[RegexValidator(r'^[A-Z][A-Z0-9_]{0,63}$', '编码须以大写字母开头，仅含大写字母、数字和下划线')])),
                ('name', models.CharField(max_length=80)),
                ('remark', models.CharField(blank=True, default='', max_length=500)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['sort_order', 'id'], 'constraints': [
                models.UniqueConstraint(fields=('type', 'code'), name='business_dict_type_code_unique'),
                models.CheckConstraint(condition=models.Q(type__in=['SALARY_POSITION', 'AD_CHANNEL', 'COST_CATEGORY']), name='business_dict_type_valid'),
            ]},
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
