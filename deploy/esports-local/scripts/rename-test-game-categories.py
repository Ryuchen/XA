"""Normalize the two isolated stores to five game-category labels.

Audit by default; use --apply after review. Category/service IDs and all business
relationships stay unchanged. Extra test categories are only deactivated.
"""
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django
django.setup()
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from orders.models import GameCategory, ServiceItem, Order, Reservation
from users.qualifications import context_label

EXPECTED = {'default': 'xa_test_fronted_acceptance_20261104', 'store_b': 'xa_test_store_preview_b_20261104'}
TARGETS = ['三角洲行动', '无畏契约', '英雄联盟', '永劫无间', 'STEAM']
for alias, database in EXPECTED.items():
    config = settings.DATABASES[alias]
    assert config['NAME'] == database and config['HOST'] == '127.0.0.1' and str(config['PORT']) == '13306', alias


def plan_store(alias):
    categories = list(GameCategory.objects.using(alias).all().order_by('sort_order', 'id'))
    assignments = {}
    used = set()
    markers = {
        '三角洲行动': ['三角洲'],
        '无畏契约': ['无畏', '瓦罗兰特'],
        '英雄联盟': ['英雄联盟'],
        '永劫无间': ['永劫无间', '王者荣耀'],
        'STEAM': ['STEAM'],
    }
    for target in TARGETS:
        exact = next((row for row in categories if row.name == target and row.id not in used), None)
        match = exact or next((row for row in categories if row.id not in used and any(marker.lower() in row.name.lower() for marker in markers[target])), None)
        if match is None and target == 'STEAM':
            match = next((row for row in categories if row.is_active and row.id not in used), None)
        assert match is not None, f'{alias}: no category available for {target}'
        assignments[match.id] = target
        used.add(match.id)
    deactivate = [row.id for row in categories if row.id not in used and row.is_active]
    services = list(ServiceItem.objects.using(alias).filter(game_category_id__in=used).order_by('id'))
    service_changes = []
    for service in services:
        target = assignments[service.game_category_id]
        new_name = service.name.replace('王者荣耀', '永劫无间') if target == '永劫无间' else service.name
        if new_name != service.name:
            service_changes.append((service, new_name))
    changed_service_ids = {service.id for service, _ in service_changes}
    order_changes = []
    requirement_changes = []
    for order in Order.objects.using(alias).select_related('service').all():
        if order.service_id in changed_service_ids:
            new_name = order.service.name.replace('王者荣耀', '永劫无间')
            if order.service_name_snapshot != new_name:
                order_changes.append((order, new_name))
        if order.service and order.service.game_category_id in used and order.provider_requirement_snapshot:
            target = assignments[order.service.game_category_id]
            snapshot = dict(order.provider_requirement_snapshot)
            if snapshot.get('game_category_name') != target:
                snapshot['game_category_name'] = target
                snapshot['label'] = context_label(snapshot)
                requirement_changes.append((order, snapshot))
    reservation_changes = []
    for reservation in Reservation.objects.using(alias).select_related('service').all():
        if reservation.service_id in changed_service_ids:
            new_name = reservation.service.name.replace('王者荣耀', '永劫无间')
            if reservation.service_name_snapshot != new_name:
                reservation_changes.append((reservation, new_name))
    return {
        'alias': alias,
        'categories': categories,
        'assignments': assignments,
        'deactivate': deactivate,
        'services': services,
        'service_changes': service_changes,
        'order_changes': order_changes,
        'requirement_changes': requirement_changes,
        'reservation_changes': reservation_changes,
    }


def apply_store(plan):
    alias = plan['alias']
    now = timezone.now()
    backup = {
        'alias': alias,
        'categories': [{'id': row.id, 'name': row.name, 'sort_order': row.sort_order, 'is_active': row.is_active} for row in plan['categories']],
        'service_changes': [{'id': row.id, 'name': row.name} for row, _ in plan['service_changes']],
        'order_changes': [{'id': row.id, 'service_name_snapshot': row.service_name_snapshot} for row, _ in plan['order_changes']],
        'requirement_changes': [{'id': row.id, 'snapshot': row.provider_requirement_snapshot} for row, _ in plan['requirement_changes']],
        'reservation_changes': [{'id': row.id, 'service_name_snapshot': row.service_name_snapshot} for row, _ in plan['reservation_changes']],
    }
    with transaction.atomic(using=alias):
        for category_id, target in plan['assignments'].items():
            GameCategory.objects.using(alias).filter(pk=category_id).update(name=target, sort_order=TARGETS.index(target), is_active=True, updated_at=now)
        if plan['deactivate']:
            GameCategory.objects.using(alias).filter(pk__in=plan['deactivate']).update(is_active=False, updated_at=now)
        for service, new_name in plan['service_changes']:
            ServiceItem.objects.using(alias).filter(pk=service.pk).update(name=new_name, updated_at=now)
        for order, new_name in plan['order_changes']:
            Order.objects.using(alias).filter(pk=order.pk).update(service_name_snapshot=new_name)
        for order, snapshot in plan['requirement_changes']:
            Order.objects.using(alias).filter(pk=order.pk).update(provider_requirement_snapshot=snapshot)
        for reservation, new_name in plan['reservation_changes']:
            Reservation.objects.using(alias).filter(pk=reservation.pk).update(service_name_snapshot=new_name)
    return backup


def main():
    plans = [plan_store(alias) for alias in EXPECTED]
    report = [{
        'alias': plan['alias'],
        'assignments': plan['assignments'],
        'deactivate': plan['deactivate'],
        'service_name_changes': len(plan['service_changes']),
        'order_name_changes': len(plan['order_changes']),
        'order_requirement_changes': len(plan['requirement_changes']),
        'reservation_name_changes': len(plan['reservation_changes']),
    } for plan in plans]
    if '--apply' not in sys.argv[1:]:
        print(json.dumps({'mode': 'audit', 'stores': report}, ensure_ascii=False, indent=2))
        return
    backups = [apply_store(plan) for plan in plans]
    backup_path = ROOT / '.tools' / 'game-category-rename-backup.json'
    backup_path.write_text(json.dumps(backups, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'mode': 'applied', 'backup': str(backup_path), 'stores': report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
