from django.db import migrations


OLD = (
    '兴安电竞 · {trigger}\n'
    '打手单钱：{provider_income} 兴安币（¥{provider_income_yuan}）\n'
    '订单号：{order_no}\n'
    '服务：{service_name}\n'
    '模式：{escort_mode} · {game_rounds} {price_unit}\n'
    '区服：{game_region}\n'
    '玩法要求：{gameplay_requirements}\n'
    '以上为本单打手实得，报单审核通过后结算。\n'
    '请到打手端确认订单状态并接单。'
)
NEW = OLD.replace('以上为本单打手实得，报单审核通过后结算。',
                  '以上为本单打手分账金额，实际入账以订单结算和钱包流水为准。')


def update_unchanged_defaults(apps, schema_editor):
    Config = apps.get_model('console', 'KookDispatchConfig')
    for row in Config.objects.all():
        changed = False
        routes = []
        for route in row.routes:
            if isinstance(route, dict) and route.get('template') == OLD:
                route = {**route, 'template': NEW}
                changed = True
            routes.append(route)
        if changed:
            row.routes = routes
            row.revision += 1
            row.save(update_fields=['routes', 'revision', 'updated_at'])


class Migration(migrations.Migration):
    dependencies = [('console', '0016_withdraw_release_permission')]
    operations = [migrations.RunPython(update_unchanged_defaults, migrations.RunPython.noop)]
