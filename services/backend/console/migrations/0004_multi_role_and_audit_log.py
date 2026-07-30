import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def forwards_copy_roles(apps, schema_editor):
    """将每个管理员原单角色迁入新的多角色 M2M。"""
    AdminMembership = apps.get_model('console', 'AdminMembership')
    for membership in AdminMembership.objects.exclude(role__isnull=True):
        membership.roles.add(membership.role_id)


def backwards_copy_roles(apps, schema_editor):
    """回滚：取首个角色写回单角色 FK。"""
    AdminMembership = apps.get_model('console', 'AdminMembership')
    for membership in AdminMembership.objects.all():
        first = membership.roles.first()
        if first is not None:
            membership.role = first
            membership.save(update_fields=['role'])


class Migration(migrations.Migration):

    dependencies = [
        ('console', '0003_adminmembership_remark'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 先让旧单角色 FK 让出 related_name，避免与新 M2M 冲突
        migrations.AlterField(
            model_name='adminmembership',
            name='role',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='members_legacy',
                to='console.adminrole',
                verbose_name='角色',
            ),
        ),
        migrations.AddField(
            model_name='adminmembership',
            name='roles',
            field=models.ManyToManyField(
                blank=True,
                related_name='members',
                to='console.adminrole',
                verbose_name='角色',
            ),
        ),
        migrations.RunPython(forwards_copy_roles, backwards_copy_roles),
        migrations.RemoveField(
            model_name='adminmembership',
            name='role',
        ),
        migrations.CreateModel(
            name='AdminAuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operator_name', models.CharField(blank=True, default='', max_length=150, verbose_name='操作人快照')),
                ('method', models.CharField(max_length=10, verbose_name='请求方法')),
                ('path', models.CharField(db_index=True, max_length=255, verbose_name='请求路径')),
                ('resource', models.CharField(blank=True, db_index=True, default='', max_length=100, verbose_name='资源')),
                ('object_id', models.CharField(blank=True, default='', max_length=64, verbose_name='对象ID')),
                ('request_body', models.JSONField(blank=True, default=dict, verbose_name='请求参数(脱敏)')),
                ('status_code', models.PositiveIntegerField(default=0, verbose_name='响应状态码')),
                ('ip', models.GenericIPAddressField(blank=True, null=True, verbose_name='来源IP')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('operator', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_logs', to=settings.AUTH_USER_MODEL, verbose_name='操作人')),
            ],
            options={
                'verbose_name': '操作审计日志',
                'verbose_name_plural': '操作审计日志',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='adminauditlog',
            index=models.Index(fields=['operator', '-created_at'], name='console_adm_operato_b454aa_idx'),
        ),
    ]
