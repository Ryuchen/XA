import json
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from orders.models import ServiceItem
from orders.companion_catalog import corrected_service_fields, corrected_text
from coupons.models import Coupon
from engagement.models import RewardItem, VipTier

class Command(BaseCommand):
    help = '更正技术单陪10元加价和娱乐档命名；保留套餐ID和历史订单。默认预览。'
    def add_arguments(self, parser): parser.add_argument('--apply', action='store_true')
    def handle(self, *args, **options):
        changes = []
        with transaction.atomic():
            for service in ServiceItem.objects.select_for_update().filter(service_type='COMPANION', catalog_family='常规陪玩'):
                fields = corrected_service_fields(service)
                if not fields: continue
                changes.append({'id':service.pk, 'name':fields.get('name',service.name), 'fields':list(fields), 'price':fields.get('price',service.price)})
                if options['apply']:
                    for key,value in fields.items(): setattr(service,key,value)
                    service.save(update_fields=[*fields,'updated_at'])
            for model in (Coupon, RewardItem):
                for row in model.objects.select_for_update().filter(Q(name__contains='娱乐女陪') | (Q(scope_variant__contains='娱乐女陪') if model is Coupon else Q(pk__in=[]))):
                    if options['apply']:
                        row.name=corrected_text(row.name)
                        fields=['name']
                        if model is Coupon:
                            row.scope_variant=corrected_text(row.scope_variant);fields.append('scope_variant')
                        row.save(update_fields=fields)
            for tier in VipTier.objects.select_for_update().all():
                old=json.dumps(tier.benefits,ensure_ascii=False)
                new=corrected_text(old)
                if options['apply'] and old != new:
                    tier.benefits=json.loads(new);tier.save(update_fields=['benefits'])
        self.stdout.write(json.dumps({'applied':options['apply'], 'services':changes},ensure_ascii=False))
