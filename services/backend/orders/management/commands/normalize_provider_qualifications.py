"""Enrich confirmed companion catalog; update only the ten explicitly requested UI test providers."""
import json
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from orders.models import ServiceItem
from users.models import EscortProfile,ProviderQualification
from users.qualifications import GRADES,grade_options,service_requirement,matching_qualification

class Command(BaseCommand):
    def add_arguments(self,parser):
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--fixtures',action='store_true')
    def handle(self,*args,**opts):
        services=list(ServiceItem.objects.filter(is_active=True,service_type='COMPANION').select_related('game_category').order_by('id'))
        plan=[]
        for item in services:
            if item.catalog_family!='常规陪玩':continue
            grade=next((g for g,label in GRADES.items() if item.catalog_variant.startswith(label+' · ')),None)
            if not grade or grade=='OPEN' or item.platform not in ('PC','MOBILE'):raise CommandError('陪玩目录存在无法识别的规格，停止写入')
            plan.append((item,grade))
        profiles=list(EscortProfile.objects.filter(account__username__startswith='xa_ui_test_20261007_').select_related('account').order_by('account__username')) if opts['fixtures'] else []
        expected=['xa_ui_test_20261007_%02d'%i for i in range(1,11)]
        if opts['fixtures'] and [p.account.username for p in profiles]!=expected:raise CommandError('必须找到原有10个测试账号，禁止创建重复账号或修改其他账号')
        game_ids={item.game_category_id for item,_ in plan}
        if opts['fixtures'] and len(game_ids)!=1:raise CommandError('测试目录游戏范围需要人工核对')
        pc=['ENTERTAINMENT','TECHNICAL','MASTER','KING','LEGEND','GLORY','STAR','ENTERTAINMENT','TECHNICAL','STAR']
        mobile=['TECHNICAL','ENTERTAINMENT','LEGEND','MASTER','TECHNICAL','LEGEND','ENTERTAINMENT','MASTER','LEGEND','TECHNICAL']
        guard_sets=[[],[('PC','CONFIDENTIAL','MASTER')],[('MOBILE','TOP_SECRET','LEGEND')],[('PC','TOP_SECRET','MASTER'),('MOBILE','CONFIDENTIAL','MASTER')],[('PC','CONFIDENTIAL','LEGEND'),('PC','TOP_SECRET','LEGEND')],[('MOBILE','CONFIDENTIAL','LEGEND'),('MOBILE','TOP_SECRET','MASTER')],[('PC','CONFIDENTIAL','LEGEND'),('PC','TOP_SECRET','STAR'),('MOBILE','CONFIDENTIAL','LEGEND'),('MOBILE','TOP_SECRET','LEGEND')],[('MOBILE','CONFIDENTIAL','MASTER')],[('PC','TOP_SECRET','KING')],[('PC','CONFIDENTIAL','MASTER'),('PC','TOP_SECRET','GLORY'),('MOBILE','CONFIDENTIAL','MASTER'),('MOBILE','TOP_SECRET','LEGEND')]]
        summary={'apply':opts['apply'],'catalog_items':len(plan),'fixtures':len(profiles),'recommendation_slots':4 if profiles else 0}
        if not opts['apply']:
            self.stdout.write(json.dumps(summary));return
        with transaction.atomic():
            for item,grade in plan:
                item.provider_kind='COMPANION';item.provider_grade=grade;item.security_level='ANY'
                item.save(update_fields=['provider_kind','provider_grade','security_level'])
            game_id=next(iter(game_ids)) if profiles else None
            for index,profile in enumerate(profiles):
                for platform,grade in [('PC',pc[index]),('MOBILE',mobile[index])]:
                    ProviderQualification.objects.update_or_create(profile=profile,game_category_id=game_id,provider_kind='COMPANION',platform=platform,security_level='ANY',defaults={'grade':grade,'is_active':True,'price_per_hour':profile.price_per_hour})
                for platform,security,grade in guard_sets[index]:
                    ProviderQualification.objects.update_or_create(profile=profile,game_category_id=game_id,provider_kind='GUARD',platform=platform,security_level=security,defaults={'grade':grade,'is_active':True,'price_per_hour':None})
                # Only companion services are enabled for these non-login test providers.
                permitted=[item.pk for item,_ in plan if matching_qualification(profile,service_requirement(item))]
                profile.service_items.set(permitted);profile.game_categories.add(game_id)
                profile.save(update_fields=['updated_at'])
            summary['qualifications']=ProviderQualification.objects.filter(profile__in=profiles).count() if profiles else 0
        self.stdout.write(json.dumps(summary))
