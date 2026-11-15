import django.db.models.deletion
from django.db import migrations, models


def seed_salary_plans(apps, schema_editor):
    Item = apps.get_model('console', 'BusinessDictionaryItem')
    Plan = apps.get_model('console', 'SalaryPlan')
    Lock = apps.get_model('console', 'PayrollLock')
    Lock.objects.get_or_create(pk=1)
    definitions = [
        ('STORE_MANAGER', dict(base_fen=300000, performance_fen=160000, target_fen=60000000, target_scope='STORE')),
        ('CUSTOMER_SERVICE_FULLTIME', dict(base_fen=120000, performance_fen=80000, target_fen=20000000,
                                          dispatch_day_bps=100, dispatch_night_bps=150, recharge_bps=100)),
        ('CUSTOMER_SERVICE_PARTTIME', dict(dispatch_day_bps=100, dispatch_night_bps=100)),
        ('PROVIDER_MANAGER', dict(base_fen=150000)),
        ('AFTERSALE_MANAGER', dict(base_fen=150000, aftersale_fee_fen=5000)),
        ('OPERATIONS', dict(base_fen=100000)),
        ('EXAMINER_SHENGLANG', dict(exam_fee_fen=5000, exam_commission_bps=3000)),
        ('EXAMINER_XINGAN', dict(exam_fee_fen=3000)),
    ]
    descriptions = {
        'STORE_MANAGER': '月底薪3000元，绩效最高1600元；月门店净消费目标60万元，未达标按比例折算。',
        'CUSTOMER_SERVICE_FULLTIME': '每人月底薪1200元，绩效最高800元，个人月业绩目标20万元；派单日班1%、夜班1.5%，预存充值1%；仅计已核实归属的净额。',
        'CUSTOMER_SERVICE_PARTTIME': '无底薪、无绩效；已核实归属的派单净额提成1%。',
        'PROVIDER_MANAGER': '月底薪1500元；人数以当月实际人员安排为准。',
        'AFTERSALE_MANAGER': '月底薪1500元；每个已完成并核实归属的售后周期另计50元。',
        'OPERATIONS': '月底薪1000元；每日投流及其他经营成本另行登记。',
        'EXAMINER_SHENGLANG': '每次核实完成考试抽成前50元，扣率30%，考官实得35元；不以报名审核次数代替考试次数。',
        'EXAMINER_XINGAN': '每次核实完成考试30元，不另扣抽成；不以报名审核次数代替考试次数。',
    }
    for code, values in definitions:
        position = Item.objects.get(type='SALARY_POSITION', code=code)
        Plan.objects.get_or_create(position=position, name=position.name + '薪资标准', defaults={
            **values, 'remark': descriptions[code],
        })


class Migration(migrations.Migration):

    dependencies = [
        ('club_accounts', '0005_clubaccount_business_cycle'),
        ('console', '0018_business_dictionaries'),
        ('orders', '0085_subsidy_share_retained_amount'),
        ('wallet', '0034_remove_transaction_transaction_type_valid_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='PayrollLock',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
            ],
        ),
        migrations.CreateModel(
            name='PayrollConfig',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('night_start', models.TimeField(blank=True, null=True)),
                ('night_end', models.TimeField(blank=True, null=True)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(('id', 1)), name='payroll_config_singleton')],
            },
        ),
        migrations.CreateModel(
            name='PayrollRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.DateField(unique=True)),
                ('fingerprint', models.CharField(max_length=64)),
                ('snapshot', models.JSONField(default=dict)),
                ('confirmed_at', models.DateTimeField(auto_now_add=True)),
                ('confirmed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
            ],
            options={
                'ordering': ['-month'],
            },
        ),
        migrations.CreateModel(
            name='SalaryPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('base_fen', models.PositiveBigIntegerField(default=0)),
                ('performance_fen', models.PositiveBigIntegerField(default=0)),
                ('target_fen', models.PositiveBigIntegerField(default=0)),
                ('target_scope', models.CharField(choices=[('PERSON', '个人业绩'), ('STORE', '门店业绩')], default='PERSON', max_length=10)),
                ('dispatch_day_bps', models.PositiveSmallIntegerField(default=0)),
                ('dispatch_night_bps', models.PositiveSmallIntegerField(default=0)),
                ('recharge_bps', models.PositiveSmallIntegerField(default=0)),
                ('aftersale_fee_fen', models.PositiveBigIntegerField(default=0)),
                ('exam_fee_fen', models.PositiveBigIntegerField(default=0)),
                ('exam_commission_bps', models.PositiveSmallIntegerField(default=0)),
                ('remark', models.CharField(blank=True, default='', max_length=1000)),
                ('is_active', models.BooleanField(default=True)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('position', models.ForeignKey(limit_choices_to={'type': 'SALARY_POSITION'}, on_delete=django.db.models.deletion.PROTECT, related_name='salary_plans', to='console.businessdictionaryitem')),
            ],
            options={
                'ordering': ['position__sort_order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='SalaryAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.DateField(db_index=True)),
                ('plan_snapshot', models.JSONField(default=dict)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='salary_assignments', to='club_accounts.clubaccount')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assignments', to='console.salaryplan')),
            ],
            options={
                'ordering': ['-month', 'account_id'],
            },
        ),
        migrations.CreateModel(
            name='OperatingCost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.DateField(db_index=True)),
                ('amount_fen', models.PositiveBigIntegerField()),
                ('note', models.CharField(blank=True, default='', max_length=1000)),
                ('client_request_id', models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ('request_fingerprint', models.CharField(blank=True, default='', max_length=64)),
                ('is_void', models.BooleanField(default=False)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.ForeignKey(limit_choices_to={'type': 'COST_CATEGORY'}, on_delete=django.db.models.deletion.PROTECT, related_name='operating_costs', to='console.businessdictionaryitem')),
                ('channel', models.ForeignKey(blank=True, limit_choices_to={'type': 'AD_CHANNEL'}, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='advertising_costs', to='console.businessdictionaryitem')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
            ],
            options={
                'ordering': ['-day', '-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('amount_fen__gte', 0)), name='operating_cost_nonnegative')],
            },
        ),
        migrations.CreateModel(
            name='SalaryActivity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('DISPATCH', '派单归属'), ('RECHARGE', '充值归属'), ('AFTERSALE', '售后完成核实'), ('EXAM', '考试完成核实')], db_index=True, max_length=12)),
                ('source_key', models.CharField(max_length=180, unique=True)),
                ('client_request_id', models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ('request_fingerprint', models.CharField(blank=True, default='', max_length=64)),
                ('is_void', models.BooleanField(default=False)),
                ('source_snapshot', models.JSONField(default=dict)),
                ('performed_at', models.DateTimeField(db_index=True)),
                ('reason', models.CharField(max_length=1000)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='salary_activities', to='club_accounts.clubaccount')),
                ('aftersale', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='salary_activities', to='orders.aftersale')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='salary_activities', to='orders.order')),
                ('recharge', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='salary_activities', to='wallet.rechargerecord')),
            ],
            options={
                'ordering': ['-performed_at', '-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('kind__in', ['DISPATCH', 'RECHARGE', 'AFTERSALE', 'EXAM'])), name='salary_activity_kind_valid')],
            },
        ),
        migrations.AddConstraint(
            model_name='salaryplan',
            constraint=models.CheckConstraint(condition=models.Q(('target_scope__in', ['PERSON', 'STORE'])), name='salary_target_scope_valid'),
        ),
        migrations.AddConstraint(
            model_name='salaryplan',
            constraint=models.CheckConstraint(condition=models.Q(('dispatch_day_bps__lte', 10000), ('dispatch_night_bps__lte', 10000), ('exam_commission_bps__lte', 10000), ('recharge_bps__lte', 10000)), name='salary_rates_valid'),
        ),
        migrations.AddConstraint(
            model_name='salaryassignment',
            constraint=models.UniqueConstraint(fields=('month', 'account'), name='salary_one_plan_per_person_month'),
        ),
        migrations.RunPython(seed_salary_plans, migrations.RunPython.noop),
    ]
