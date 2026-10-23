"""Scoped provider eligibility and order-tier commission; one rule source for all clients."""
from django.db.models import Q

GRADES = {'ENTERTAINMENT':'娱乐','TECHNICAL':'技术','MASTER':'大师','KING':'王者','LEGEND':'传奇','GLORY':'荣耀','STAR':'明星','OPEN':'不分档'}
KINDS = {'GUARD':'护航','COMPANION':'陪玩','GRIND':'代肝','RANK':'上分'}
PLATFORMS = {'PC':'端游','MOBILE':'手游'}
SECURITY = {'CONFIDENTIAL':'机密','TOP_SECRET':'绝密','ANY':'不限难度'}
COMPANION_GRADES = ['ENTERTAINMENT','TECHNICAL','MASTER','KING','LEGEND','GLORY','STAR']
GUARD_TOP_GRADES = ['KING','MASTER','LEGEND','GLORY','STAR']
DEFAULT_RATES = {'KING':24,'MASTER':22,'LEGEND':20,'GLORY':18,'STAR':15}

def grade_options(kind, platform, security):
    if kind == 'COMPANION' and platform in PLATFORMS and security == 'ANY':
        return COMPANION_GRADES
    if kind == 'GUARD' and platform in PLATFORMS:
        if security == 'CONFIDENTIAL' or (platform == 'MOBILE' and security == 'TOP_SECRET'):
            return ['MASTER','LEGEND']
        if platform == 'PC' and security == 'TOP_SECRET':
            return GUARD_TOP_GRADES
    if kind in ('GRIND','RANK') and platform in PLATFORMS and security == 'ANY':
        return ['OPEN']
    return []

def commission_for(requirement):
    kind, platform, security, grade = (requirement.get(k) for k in ('provider_kind','platform','security_level','grade'))
    if kind == 'COMPANION': return 20
    if kind == 'GUARD':
        if platform == 'MOBILE' and security == 'CONFIDENTIAL': return {'MASTER':30,'LEGEND':25}.get(grade)
        if security == 'CONFIDENTIAL' or (platform == 'MOBILE' and security == 'TOP_SECRET'): return {'MASTER':25,'LEGEND':20}.get(grade)
        if platform == 'PC' and security == 'TOP_SECRET': return DEFAULT_RATES.get(grade)
    return None

def context_label(row):
    return ' · '.join(filter(None,[row.get('game_category_name',''),PLATFORMS.get(row.get('platform'),''),
        SECURITY.get(row.get('security_level'),'') if row.get('provider_kind') == 'GUARD' else '',
        KINDS.get(row.get('provider_kind'),''),GRADES.get(row.get('grade'),'')]))

def service_requirement(service_or_order):
    # Existing orders have an empty snapshot: do not adopt newly changed catalog requirements.
    if hasattr(service_or_order, 'provider_requirement_snapshot'):
        return service_or_order.provider_requirement_snapshot or None
    grade = getattr(service_or_order,'provider_grade','')
    if not grade: return None
    item = service_or_order
    row = {'game_category_id':item.game_category_id,'game_category_name':item.game_category.name if item.game_category_id else '',
        'provider_kind':item.provider_kind,'platform':item.platform,'security_level':item.security_level,'grade':grade}
    choices = grade_options(row['provider_kind'],row['platform'],row['security_level'])
    if grade not in choices: return dict(row, invalid=True, label='订单资格配置无效')
    return dict(row, rank=choices.index(grade), grade_name=GRADES[grade], label=context_label(row))

def public_requirement(item):
    row=service_requirement(item)
    return {k:v for k,v in (row or {}).items() if k not in ('commission_rate','rank')}

def qualification_rows(profile):
    rows=getattr(profile,'_prefetched_objects_cache',{}).get('qualifications')
    return list(rows) if rows is not None else list(profile.qualifications.select_related('game_category').all())

def serialize_qualification(row):
    data={'id':row.pk,'game_category':row.game_category_id,'game_category_id':row.game_category_id,
        'game_category_name':row.game_category.name,'provider_kind':row.provider_kind,'platform':row.platform,
        'security_level':row.security_level,'grade':row.grade,'grade_name':GRADES.get(row.grade,''),
        'price_per_hour':row.price_per_hour,'is_active':row.is_active}
    data['label']=context_label(data);return data

def scope_matches(row, requirement):
    return row.is_active and all(getattr(row,key)==requirement.get(key) for key in ('game_category_id','provider_kind','platform','security_level'))

def matching_qualification(profile, requirement):
    if not requirement or requirement.get('invalid'): return None
    choices=grade_options(requirement['provider_kind'],requirement['platform'],requirement['security_level'])
    required=choices.index(requirement['grade']) if requirement['grade'] in choices else len(choices)
    return next((row for row in qualification_rows(profile) if scope_matches(row,requirement) and row.grade in choices and choices.index(row.grade)>=required),None)

def service_skill(profile, item):
    service=getattr(item,'service',item)
    if service.game_category_id is None:return True
    items=getattr(profile,'_prefetched_objects_cache',{}).get('service_items')
    if items is None: items=list(profile.service_items.select_related('game_category').all())
    if any(row.pk==service.pk for row in items):return True
    requirement=service_requirement(item)
    if not requirement or requirement.get('invalid'):return False
    # Higher packages grant lower packages only within the same explicitly configured gameplay family.
    for skill in items:
        sibling=(bool(service.catalog_family) and skill.catalog_family==service.catalog_family) or (service.gameplay_id and skill.gameplay_id==service.gameplay_id)
        other=service_requirement(skill)
        if sibling and other and not other.get('invalid') and all(other.get(k)==requirement.get(k) for k in ('game_category_id','provider_kind','platform','security_level')) and other['rank']>=requirement['rank']:
            return True
    return False

def qualified_order_q(profile):
    legacy=Q(provider_requirement_snapshot={}) & (Q(service__required_level__isnull=True)|Q(service__required_level__sort_order__lte=profile.level_rank))
    query=legacy
    for row in qualification_rows(profile):
        choices=grade_options(row.provider_kind,row.platform,row.security_level)
        if row.is_active and row.grade in choices:
            query |= Q(provider_requirement_snapshot__game_category_id=row.game_category_id,
                provider_requirement_snapshot__provider_kind=row.provider_kind,provider_requirement_snapshot__platform=row.platform,
                provider_requirement_snapshot__security_level=row.security_level,provider_requirement_snapshot__grade__in=choices[:choices.index(row.grade)+1])
    return query

def filtered_qualifications(params, *, minimum=True):
    """One relational predicate so different qualifications cannot accidentally satisfy separate filters."""
    from rest_framework.exceptions import ValidationError
    values={k:str(params.get(k,'')).strip() for k in ('provider_kind','platform','security_level','grade')}
    for key,choices in [('provider_kind',KINDS),('platform',PLATFORMS),('security_level',SECURITY),('grade',GRADES)]:
        if values[key] and values[key] not in choices:raise ValidationError('资格筛选参数无效：'+key)
    game=str(params.get('game_category','')).strip()
    if game and (not game.isascii() or not game.isdigit() or len(game)>18):raise ValidationError('游戏类目参数无效')
    active=any(values.values())
    if not active:return None
    query=Q(qualifications__is_active=True)
    if game:query &= Q(qualifications__game_category_id=int(game))
    for key in ('provider_kind','platform','security_level'):
        if values[key]:query &= Q(**{'qualifications__'+key:values[key]})
    if values['grade']:
        scope=grade_options(values['provider_kind'],values['platform'],values['security_level'])
        if minimum and not scope:raise ValidationError('请先选择服务方向、端手游及难度，再筛选最低档位')
        if minimum and values['grade'] not in scope:raise ValidationError('该范围没有这个档位')
        query &= Q(qualifications__grade__in=scope[scope.index(values['grade']):] if minimum else [values['grade']])
    return query

def options_payload():
    return {'kinds':[{'value':v,'label':n} for v,n in KINDS.items()],
        'platforms':[{'value':v,'label':n} for v,n in PLATFORMS.items()],
        'scopes':[{'provider_kind':kind,'platform':platform,'security_level':security,'grades':[{'value':g,'label':GRADES[g],'commission_rate':commission_for({'provider_kind':kind,'platform':platform,'security_level':security,'grade':g})} for g in grade_options(kind,platform,security)]}
          for kind in KINDS for platform in PLATFORMS for security in SECURITY if grade_options(kind,platform,security)]}


def authorized_skill_q(profile):
    skills=list(profile.service_items.all())
    query=Q(service_id__in=[row.pk for row in skills])|Q(service__game_category__isnull=True)
    for skill in skills:
        requirement=service_requirement(skill)
        if requirement and not requirement.get('invalid') and skill.catalog_family:
            query |= Q(service__catalog_family=skill.catalog_family,service__game_category_id=skill.game_category_id,
                service__platform=skill.platform,service__security_level=skill.security_level,service__provider_kind=skill.provider_kind,
                service__provider_grade__in=grade_options(skill.provider_kind,skill.platform,skill.security_level)[:requirement['rank']+1])
    return query


NOTIFICATION_SCOPES = [('PC','CONFIDENTIAL','端游机密'),('PC','TOP_SECRET','端游绝密'),('MOBILE','CONFIDENTIAL','手游机密'),('MOBILE','TOP_SECRET','手游绝密')]

def notification_options(profile):
    rows=qualification_rows(profile)
    return [{'key':platform+'_'+security,'label':label,
             'eligible':any(q.is_active and q.provider_kind=='GUARD' and q.platform==platform and q.security_level==security and q.game_category.is_active for q in rows),
             'enabled':profile.dispatch_notification_preferences.get(platform+'_'+security,True)} for platform,security,label in NOTIFICATION_SCOPES]

def dispatch_notification_enabled(profile,order):
    requirement=service_requirement(order)
    if not profile:return False
    if requirement and requirement.get('provider_kind')=='GUARD':
        return bool(matching_qualification(profile,requirement)) and profile.dispatch_notification_preferences.get(requirement['platform']+'_'+requirement['security_level'],True)
    # Previously issued untyped guard orders still respect platform/difficulty switches.
    if not requirement and order.service.platform and order.service.security_level in ('CONFIDENTIAL','TOP_SECRET'):
        return profile.dispatch_notification_preferences.get(order.service.platform+'_'+order.service.security_level,True)
    return True
