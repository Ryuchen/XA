"""Repair identifiable unsent default KOOK cards without altering custom cards."""
import json

from django.db import migrations


OLD_SENTENCE = '以上为本单打手实得，报单审核通过后结算。'
NEW_SENTENCE = '以上为本单打手分账金额，实际入账以订单结算和钱包流水为准。'
DEFAULT_TEMPLATE = (
    '兴安电竞 · {trigger}\n打手单钱：{provider_income} 兴安币（¥{provider_income_yuan}）\n'
    '订单号：{order_no}\n服务：{service_name}\n模式：{escort_mode} · {game_rounds} {price_unit}\n'
    '区服：{game_region}\n玩法要求：{gameplay_requirements}\n'
    + NEW_SENTENCE + '\n请到打手端确认订单状态并接单。'
)


def default_output(text):
    lines = text.split('\n')
    if len(lines) == 10 and lines[-1].startswith('陪玩选择：'):
        lines = lines[:-1]
    return (len(lines) == 9 and lines[0] in ('兴安电竞 · 待接单', '兴安电竞 · 拒单回池')
            and all(lines[index].startswith(prefix) for index, prefix in enumerate(
                ('兴安电竞 · ', '打手单钱：', '订单号：', '服务：', '模式：', '区服：', '玩法要求：')))
            and lines[7] == OLD_SENTENCE and lines[8] == '请到打手端确认订单状态并接单。')


def repair(apps, schema_editor):
    Config = apps.get_model('console', 'KookDispatchConfig')
    Record = apps.get_model('orders', 'KookDispatchRecord')
    config = Config.objects.filter(pk=1).first()
    if not config:
        return
    default_routes = [route.get('id') for route in config.routes if isinstance(route, dict)
                      and route.get('template') == DEFAULT_TEMPLATE and route.get('id')]
    for record in Record.objects.select_for_update().filter(status='PENDING', route_key__in=default_routes):
        if not default_output(record.content):
            continue
        try:
            cards = json.loads(record.card_content)
            modules = cards[0]['modules'] if len(cards) == 1 and cards[0]['type'] == 'card' else []
            text_modules = [module['text'] for module in modules if module.get('type') == 'section'
                            and module.get('text', {}).get('type') == 'plain-text']
            if len(text_modules) != 1 or text_modules[0].get('content') != record.content:
                continue
            text_modules[0]['content'] = record.content.replace(OLD_SENTENCE, NEW_SENTENCE)
            new_card = json.dumps(cards, ensure_ascii=False)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            continue
        record.content = text_modules[0]['content']
        record.card_content = new_card
        record.save(update_fields=['content', 'card_content'])


class Migration(migrations.Migration):
    dependencies = [('orders', '0079_fix_pending_wecom_settlement_text'),
                    ('console', '0017_update_default_dispatch_settlement_text')]
    operations = [migrations.RunPython(repair, migrations.RunPython.noop)]
