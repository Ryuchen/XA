from django.db import migrations

# 预置角色及其权限点。超级管理员通过 is_superuser 自动获得全部权限，
# 此处的「超级管理员」角色仅作为分配载体，权限留空亦不影响超管。
ROLE_SEEDS = [
    {
        'code': 'super_admin',
        'name': '超级管理员',
        'description': '系统最高权限',
        'sort_order': 0,
        'all_permissions': True,
    },
    {
        'code': 'operator',
        'name': '运营',
        'description': '内容与营销运营',
        'sort_order': 1,
        'permissions': [
            'dashboard:view',
            'user:view',
            'escort:view', 'escort:edit', 'escort:verify',
            'order:view',
            'coupon:view', 'coupon:edit', 'coupon:delete',
            'announcement:view', 'announcement:edit', 'announcement:delete',
            'banner:view', 'banner:edit', 'banner:delete',
            'achievement:view', 'achievement:edit', 'achievement:delete',
            'service:view', 'service:edit', 'service:delete',
            'message:view', 'message:push',
        ],
    },
    {
        'code': 'finance',
        'name': '财务',
        'description': '钱包与订单结算',
        'sort_order': 2,
        'permissions': [
            'dashboard:view',
            'user:view',
            'order:view', 'order:refund', 'order:cancel',
            'wallet:view', 'wallet:adjust', 'transaction:view',
            'coupon:view',
        ],
    },
    {
        'code': 'support',
        'name': '客服',
        'description': '用户支持与消息',
        'sort_order': 3,
        'permissions': [
            'dashboard:view',
            'user:view',
            'order:view',
            'message:view', 'message:push',
            'support:view', 'support:edit', 'support:delete',
            'announcement:view',
        ],
    },
]


def seed_roles(apps, schema_editor):
    AdminRole = apps.get_model('console', 'AdminRole')
    AdminMembership = apps.get_model('console', 'AdminMembership')
    User = apps.get_model('users', 'CustomUser')

    # 重新导入权限清单（迁移中不能直接 import 上层模块会引环，故内联引用）
    from console.permissions import ALL_PERMISSIONS

    super_role = None
    for seed in ROLE_SEEDS:
        perms = ALL_PERMISSIONS if seed.get('all_permissions') else seed.get('permissions', [])
        role, _ = AdminRole.objects.update_or_create(
            code=seed['code'],
            defaults={
                'name': seed['name'],
                'description': seed['description'],
                'permissions': perms,
                'sort_order': seed['sort_order'],
                'is_active': True,
            },
        )
        if seed['code'] == 'super_admin':
            super_role = role

    # 将现有超级管理员关联到「超级管理员」角色，便于后台登录。
    if super_role:
        for user in User.objects.filter(is_superuser=True):
            AdminMembership.objects.get_or_create(
                user=user,
                defaults={'role': super_role, 'is_active': True},
            )


def unseed_roles(apps, schema_editor):
    AdminRole = apps.get_model('console', 'AdminRole')
    AdminRole.objects.filter(code__in=[s['code'] for s in ROLE_SEEDS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('console', '0001_initial'),
        ('users', '0005_seed_achievements'),
    ]

    operations = [
        migrations.RunPython(seed_roles, unseed_roles),
    ]
