"""Store-configured issuance rules embedded in VipTier.benefits.

``issuance`` is an optional object: companion_quantity (0–100),
companion_variant (string, at most 60 characters), birthday (boolean), safety_box (boolean),
safety_box_refresh (boolean), highlighted (boolean), birthday_discount
(1–100), birthday_max_discount and monthly_max_discount (minor units, 0 means
unlimited). An explicitly supplied
object defaults every omitted right to disabled. Tiers without it retain the
six-level initialization template; higher levels receive no implicit rights.
Monthly and safety-box rates remain the dedicated VipTier fields.
"""


def issuance_policy(tier):
    benefits = tier.benefits if isinstance(tier.benefits, dict) else {}
    custom = benefits.get('issuance')
    if isinstance(custom, dict):
        return {
            'companion_quantity': custom.get('companion_quantity', 0),
            'companion_variant': custom.get('companion_variant', ''),
            'birthday': custom.get('birthday', False),
            'birthday_discount': custom.get('birthday_discount', 90),
            'birthday_max_discount': custom.get('birthday_max_discount', 0),
            'birthday_valid_days': custom.get('birthday_valid_days', 1),
            'birthday_multiplier': custom.get('birthday_multiplier', 1),
            'birthday_reward_id': custom.get('birthday_reward_id'),
            'birthday_reward_quantity': custom.get('birthday_reward_quantity', 0),
            'monthly_max_discount': custom.get('monthly_max_discount', 0),
            'safety_box': custom.get('safety_box', False),
            'safety_box_refresh': custom.get('safety_box_refresh', False),
            'highlighted': custom.get('highlighted', False),
            'custom': True, 'template_level': None,
        }
    level = benefits.get('_issuance_template_level', tier.level)
    if type(level) is not int or not 1 <= level <= 6:
        level = 0
    legacy = 1 <= level <= 6
    return {
        'companion_quantity': [2, 2, 2, 2, 3, 4][level - 1] if legacy else 0,
        'companion_variant': ['娱乐', '技术', '王者', '传奇', '传奇', '荣耀'][level - 1] if legacy else '',
        'birthday': legacy and level >= 4, 'birthday_discount': 90,
        'birthday_max_discount': 10000, 'birthday_valid_days': 1,
        'birthday_multiplier': None if legacy else 1,
        'birthday_reward_id': None, 'birthday_reward_quantity': 1 if legacy and level >= 4 else 0,
        'monthly_max_discount': 10000 if legacy else 0,
        'safety_box': legacy,
        'safety_box_refresh': legacy and level == 6,
        'highlighted': legacy and level >= 4, 'custom': False, 'template_level': level or None,
    }


def birthday_points_multiplier(operation, tier, birthday, day):
    if not birthday or (birthday.month, birthday.day) != (day.month, day.day):
        return 1
    if tier and operation.vip_enabled:
        configured = issuance_policy(tier)['birthday_multiplier']
        if configured is not None:
            return configured
    return operation.birthday_multiplier
