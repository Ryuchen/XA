"""Order-wide commission modes; fixed amounts are charged exactly once.

Old report/gift percentage rules deliberately keep using get_commission_rate.
OrderProvider.commission_fixed remains the allocated participant deduction.
"""
from .pricing import PricingError, resolve_commission_rate_with_source
from .settlement import split_settlement_bases, build_provider_shares

TYPE_KEY = 'platform_order_commission_type'
FIXED_KEY = 'platform_order_commission_fixed'
MAX_AMOUNT = 2147483647


def integer(value, label, maximum=MAX_AMOUNT):
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is not int or not 0 <= value <= maximum:
        raise PricingError(f'{label}须为0至{maximum}的整数')
    return value


def override_from(data):
    mode = data.get('commission_type', 'AUTO')
    if mode not in ('AUTO','PERCENT','FIXED'):
        raise PricingError('抽成方式须为AUTO、PERCENT或FIXED')
    if mode == 'FIXED':
        value = data.get('commission_fixed', data.get('commission_fixed_total'))
        fixed = integer(value, '整单固定抽成金额')
        if 'commission_fixed_total' in data and integer(data['commission_fixed_total'],'整单固定抽成金额') != fixed:
            raise PricingError('整单固定抽成金额不一致')
        return {'commission_type':mode,'commission_fixed':fixed}
    result = {'commission_type':mode}
    if mode == 'PERCENT' and data.get('commission_rate') not in (None,''):
        result['commission_rate'] = integer(data['commission_rate'],'抽成率',100)
    return result


def global_config():
    from django.conf import settings
    from wallet.models import SystemConfig, COMMISSION_RATE_KEY
    values = dict(SystemConfig.objects.filter(key__in=[TYPE_KEY,FIXED_KEY,COMMISSION_RATE_KEY]).values_list('key','value'))
    mode = values.get(TYPE_KEY, 'PERCENT')
    if mode not in ('PERCENT','FIXED'):
        mode = 'PERCENT'
    try:
        fixed = integer(values.get(FIXED_KEY,'0'),'整单固定抽成金额')
    except PricingError:
        mode,fixed = 'PERCENT',0
    try:
        rate = integer(values.get(COMMISSION_RATE_KEY,getattr(settings,'PLATFORM_COMMISSION_RATE',20)),'抽成率',100)
    except PricingError:
        rate = getattr(settings,'PLATFORM_COMMISSION_RATE',20)
    return {'commission_type':mode,'commission_fixed':fixed,'commission_rate':rate}


def automatic(service, amount):
    """Existing authoritative strategies, without changing their precedence."""
    kind = getattr(service,'experience_kind','')
    policy = getattr(service,'commission_policy','LEGACY')
    if kind:
        return 'PERCENT',0 if kind.startswith('NEW') else 10,0,'club_experience'
    if policy == 'EXPERIENCE':
        return ('FIXED',0,min(300,amount),'club_experience') if amount < 10000 else ('PERCENT',10,0,'club_experience')
    from users.qualifications import service_requirement, commission_for
    requirement = service_requirement(service)
    if requirement and not requirement.get('invalid'):
        rate = requirement.get('commission_rate') if hasattr(service,'provider_requirement_snapshot') else commission_for(requirement)
        if rate is not None:
            return 'PERCENT',rate,0,'order_grade'
    if policy == 'COMPANION':
        return 'PERCENT',20,0,'club_companion'
    category = getattr(service,'service_category',None)
    if category and category.is_gift:
        from .gifts import gift_commission_rate
        return 'PERCENT',gift_commission_rate(service,amount),0,'gift'
    return None


LABELS = {'club_experience':'体验单自动规则','order_grade':'订单档位自动规则',
          'club_companion':'陪玩单自动20%','gift':'礼物独立抽成规则',
          'promotion':'活动配置','service':'服务配置','global':'全局订单配置',
          'level':'陪玩等级','manual':'手工派单','order_snapshot':'订单快照'}


def resolve(service, amount, *, profile=None, promotion=None, override=None):
    requested = override or {'commission_type':'AUTO'}
    mode = requested['commission_type']
    is_order = hasattr(service,'provider_requirement_snapshot')
    if is_order:
        # Never adopt a changed service/global fixed mode for an existing order.
        if getattr(service,'commission_type','PERCENT') == 'FIXED':
            result = ('FIXED',0,min(service.commission_fixed,amount),'order_snapshot')
        elif service.commission_override:
            result = ('PERCENT',service.commission_rate,0,'order_snapshot')
        else:
            rate,source = resolve_commission_rate_with_source(service,profile,None)
            result = ('PERCENT',rate,0,source)
        return metadata(*result,locked=service.commission_override,custom=service.commission_override)
    forced = automatic(service,amount)
    rate,source = resolve_commission_rate_with_source(service,profile,promotion)
    # Gift/standalone report rules are independent of order commission settings.
    if forced and forced[3] == 'gift':
        if mode == 'FIXED' or getattr(service,'commission_override',False):
            raise PricingError('礼物使用独立抽成规则，不支持订单固定抽成')
        return metadata(*forced,locked=True)
    if mode == 'FIXED':
        return metadata('FIXED',0,min(requested['commission_fixed'],amount),'manual',custom=True)
    if mode == 'PERCENT':
        # Explicit manual percentage retains per-person percentage inputs;
        # scoped qualification/pricing checks are still enforced separately.
        return metadata('PERCENT',requested.get('commission_rate',rate),0,'manual',custom=True)
    # Existing percentage promotions retain their old scope/priority. A new
    # explicit FIXED promotion may override commission, except experience_kind
    # purchases whose promotion eligibility remains unchanged.
    if promotion is not None and not getattr(service,'experience_kind','') and getattr(promotion,'commission_type','PERCENT') == 'FIXED':
        return metadata('FIXED',0,min(promotion.commission_fixed,amount),'promotion',custom=True)
    if getattr(service,'commission_override',False):
        if service.commission_type == 'FIXED':
            return metadata('FIXED',0,min(service.commission_fixed,amount),'service',custom=True)
        if service.commission_rate is None:
            raise PricingError('自定义百分比抽成须填写抽成率')
        return metadata('PERCENT',service.commission_rate,0,'service',locked=True,custom=True)
    if forced:
        return metadata(*forced,locked=True)
    # Preserve a previously authoritative percentage promotion ahead of a
    # service/global default; existing REGULAR/FUN percentage paths are intact.
    if source == 'promotion':
        return metadata('PERCENT',rate,0,source)
    config = global_config()
    if config['commission_type'] == 'FIXED':
        return metadata('FIXED',0,min(config['commission_fixed'],amount),'global',custom=True)
    if source == 'global':
        rate = config['commission_rate']
    return metadata('PERCENT',rate,0,source)


def metadata(mode, rate, fixed, source, *, locked=False, custom=False):
    return {'commission_type':mode,'commission_rate':rate,'commission_fixed':fixed,
        'commission_fixed_total':fixed if mode == 'FIXED' else None,
        'commission_locked':locked,'commission_override':custom,'commission_source':source,
        'commission_source_label':LABELS.get(source,source),'source':source,
        'source_label':LABELS.get(source,source),'rate':rate}


def snapshot(plan):
    return {key:plan[key] for key in ('commission_type','commission_fixed','commission_override','commission_source')}


def provider_specs(service, profiles, amount, *, promotion=None, override=None):
    plan = resolve(service,amount,profile=profiles[0] if profiles else None,promotion=promotion,override=override)
    if plan['commission_type'] == 'FIXED':
        return fixed_specs(amount,len(profiles),plan['commission_fixed'])
    if plan['commission_override']:
        return [{'commission_type':'PERCENT','commission_rate':plan['commission_rate']} for _ in profiles]
    return None


def fixed_specs(amount, count, total):
    """Allocate the one capped total proportionally to purchased bases.

    Integer floors plus largest remainder (slot order breaks ties) preserve
    exactly the charged amount without overdrawing even a zero-sized share.
    """
    bases = split_settlement_bases(amount,count)
    total = min(total,amount)
    cuts = [total*base//amount if amount else 0 for base in bases]
    remaining = total-sum(cuts)
    indices = sorted(range(len(bases)),key=lambda i:(-(total*bases[i]%amount) if amount else 0,i))
    for index in indices[:remaining]:
        cuts[index] += 1
    return [{'commission_type':'FIXED','commission_rate':0,'commission_fixed':cut} for cut in cuts]


def quote(service, amount, *, profile=None, promotion=None, override=None, count=None):
    data = resolve(service,amount,profile=profile,promotion=promotion,override=override)
    count = count or getattr(service,'billing_provider_count',1)
    if data['commission_type'] == 'FIXED':
        shares = build_provider_shares(amount,fixed_specs(amount,count,data['commission_fixed']))
        data['commission_fixed_shares'] = [share.commission_fixed for share in shares]
        data['provider_shares'] = [dict(settlement_base=share.settlement_base,
            commission_type=share.commission_type,commission_rate=share.commission_rate,
            commission_fixed=share.commission_fixed,provider_income=share.provider_income) for share in shares]
    else:
        data['commission_fixed_shares'] = None
        data['provider_shares'] = None
    return data
