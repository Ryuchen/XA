from .models import BossType


def default_miniapp_boss_type():
    """The entry tier for newly registered mini-program customers."""
    boss_type, _ = BossType.objects.get_or_create(
        name='散客',
        defaults={'discount_rate':100, 'is_active':True, 'sort_order':0,
                  'remark':'小程序新注册老板默认类型；原价下单，按实际累计消费升级VIP。'},
    )
    return boss_type
