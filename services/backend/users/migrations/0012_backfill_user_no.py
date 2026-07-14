import random

from django.db import migrations


def backfill(apps, schema_editor):
    """为存量数据回填编号：

    - 所有 boss_no 为空的用户生成 XA+6 位随机数字（全系统唯一）；
    - 所有 escort_no 为空的陪玩档案同步为其用户的 boss_no（一人一码）。
    """
    CustomUser = apps.get_model('users', 'CustomUser')
    EscortProfile = apps.get_model('users', 'EscortProfile')

    used = set(
        CustomUser.objects.exclude(boss_no='').values_list('boss_no', flat=True)
    )

    def gen() -> str:
        while True:
            code = 'XA' + f'{random.randint(0, 999999):06d}'
            if code not in used:
                used.add(code)
                return code

    for user in CustomUser.objects.filter(boss_no=''):
        user.boss_no = gen()
        user.save(update_fields=['boss_no'])

    for profile in EscortProfile.objects.filter(escort_no='').select_related('user'):
        profile.escort_no = profile.user.boss_no
        profile.save(update_fields=['escort_no'])


def noop(apps, schema_editor):
    # 编号为不可逆的业务数据，回滚不清除。
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0011_escortschedule'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
