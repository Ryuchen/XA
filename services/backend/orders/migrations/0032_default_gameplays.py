from django.db import migrations


def seed(apps, schema_editor):
    Gameplay = apps.get_model('orders', 'Gameplay')
    entries = [
        ('roulette', '转盘单', 'ROULETTE', '转出本单目标，开启惊喜护航'),
        ('experience', '体验单', 'STANDARD', '轻松体验，初次同行'),
        ('companion', '陪玩单', 'STANDARD', '默契搭档，认真陪你每一局'),
        ('fun', '趣味单', 'STANDARD', '挑战不同玩法，发现游戏乐趣'),
        ('mission', '任务单', 'STANDARD', '明确任务目标，逐项完成'),
        ('extraction', '单局带出单', 'STANDARD', '选择带出目标，护航安全撤离'),
        ('prison', '监狱单', 'STANDARD', '专项地图，配合突围'),
        ('custom', '定制单', 'STANDARD', '按约定需求，安排专属服务'),
    ]
    for index, (code, name, kind, subtitle) in enumerate(entries):
        Gameplay.objects.get_or_create(code=code, defaults={
            'name': name, 'kind': kind, 'subtitle': subtitle, 'theme': code, 'sort_order': index,
            'rules': '下单前请核对平台、难度与服务目标。余额不足请联系客服人工充值。服务结束后由打手提交凭证，后台审核结算；服务异常请联系客服处理。',
        })


class Migration(migrations.Migration):
    dependencies = [('orders', '0031_gameplay_order_gameplay_snapshot_and_more')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
