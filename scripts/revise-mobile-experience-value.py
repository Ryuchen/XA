"""2026-10-08 confirmed catalog correction; run inside the Django backend.

Default is a read-only preview. --apply changes four fields on the existing
mobile new-value service. Prices, IDs, coupons, images and orders are untouched.
"""
import argparse
import json
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.db import transaction
from console.serializers import AdminServiceItemSerializer
from coupons.models import Coupon
from orders.models import ServiceItem

CATALOG_KEY = 'xa-confirmed-experience-new_value-mobile-confidential'
OLD_NAME = '新客超值券 · 手游机密 · 保底1188w'
NEW_NAME = '新客超值券 · 手游绝密 · 保底1314w'
OLD_VARIANT = '新客超值券 · 机密 · 保底1188w'
NEW_VARIANT = '新客超值券 · 绝密 · 保底1314w'
OLD_HEADING = '手游／机密；新客超值券；98元／份，基础保底1188w。'
NEW_HEADING = '手游／绝密；新客超值券；98元／份，基础保底1314w。'


def revise(apply=False):
    with transaction.atomic():
        service = ServiceItem.objects.select_for_update().get(catalog_key=CATALOG_KEY)
        assert service.platform == 'MOBILE' and service.experience_kind == 'NEW_VALUE'
        assert service.service_type == 'EXPERIENCE' and service.price == 9800
        assert service.billing_provider_count == 1 and not service.is_addon
        before = ServiceItem.objects.values().get(pk=service.pk)
        coupons = list(Coupon.objects.order_by('pk').values())
        other_services = list(ServiceItem.objects.exclude(pk=service.pk).order_by('pk').values())
        if (service.name, service.catalog_variant, service.security_level) == (
            NEW_NAME, NEW_VARIANT, 'TOP_SECRET'
        ) and service.rules.startswith(NEW_HEADING + '\n'):
            return {'applied': apply, 'changed': False, 'service_id': service.pk}
        assert (service.name, service.catalog_variant, service.security_level) == (
            OLD_NAME, OLD_VARIANT, 'CONFIDENTIAL'
        ), 'Unexpected catalog revision; stop without writing'
        assert service.rules.startswith(OLD_HEADING + '\n'), 'Unexpected rules heading'
        updates = {
            'name': NEW_NAME,
            'catalog_variant': NEW_VARIANT,
            'security_level': 'TOP_SECRET',
            'rules': NEW_HEADING + service.rules[len(OLD_HEADING):],
        }
        serializer = AdminServiceItemSerializer(service, data=updates, partial=True)
        serializer.is_valid(raise_exception=True)
        if apply:
            serializer.save()
            after = ServiceItem.objects.values().get(pk=service.pk)
            for field, old_value in before.items():
                assert after[field] == (
                    updates[field] if field in updates else
                    after[field] if field == 'updated_at' else old_value
                ), field
            assert list(Coupon.objects.order_by('pk').values()) == coupons
            assert list(ServiceItem.objects.exclude(pk=service.pk).order_by('pk').values()) == other_services
        return {
            'applied': apply, 'changed': bool(apply), 'service_id': service.pk,
            'price_yuan': 98, 'security_level': 'TOP_SECRET', 'guarantee_w': 1314,
            'changes': {field: {'before': before[field], 'after': value}
                        for field, value in updates.items()},
            'coupon_bindings_preserved': True, 'images_preserved': True,
            'other_service_configuration_preserved': True,
        }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(revise(args.apply), ensure_ascii=False, default=str))
