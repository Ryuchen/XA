"""Import the reviewed club price book into a local test database, repeatably."""
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from announcements.models import Announcement
from orders.models import GameCategory, Gameplay, ServiceCategory, ServiceItem
from orders.catalog import catalog_labels


CATALOG = Path(__file__).resolve().parents[2] / 'data' / 'club_catalog_20260922.json'
GROUPS = {
    'experience': ('体验与每日', '新客体验与每日专享', 'experience'),
    'supplies': ('物资护航', '常规物资、嘉豪与超额累计', 'extraction'),
    'bank': ('物资银行', '固定局数与一次性追加保底', 'extraction'),
    'extraction': ('撤离挑战', '连续撤离与单局物资目标', 'extraction'),
    'fun': ('趣味玩法', '收集、学分与限时挑战', 'fun'),
    'companion': ('陪玩与教学', '按人数、档位与时间选择', 'companion'),
    'mission': ('任务与代肝', '清图、跑刀与专属任务', 'mission'),
    'custom': ('定制咨询', '按需求确认具体方案', 'custom'),
}


def validate_import_database():
    db = settings.DATABASES['default']
    if settings.DEBUG and db['ENGINE'] == 'django.db.backends.sqlite3':
        return
    if (os.environ.get('XA_LOCAL_VALIDATION') == 'true'
            and db['ENGINE'] == 'django.db.backends.mysql'
            and db.get('NAME') == 'xa_verify' and db.get('HOST') == 'db'
            and db.get('USER') == 'xa_verify'):
        return
    raise CommandError('价目测试导入只允许 DEBUG SQLite 或专用 xa_verify 本地容器数据库。')


class Command(BaseCommand):
    help = '导入经核对的兴安价目到本地测试库；不删除订单、用户或钱包。默认仅检查。'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--asset-root', type=Path)
        parser.add_argument('--media-base', default='http://127.0.0.1:8000/media/')
        parser.add_argument('--hide-legacy-demos', action='store_true')

    def handle(self, *args, **options):
        validate_import_database()
        host = urlparse(options['media_base']).hostname
        if host not in ('127.0.0.1', 'localhost'):
            raise CommandError('测试媒体地址必须指向本机。')
        book = json.loads(CATALOG.read_text(encoding='utf-8'))
        rows = book['items']
        keys = [row['catalog_key'] for row in rows]
        if len(keys) != len(set(keys)):
            raise CommandError('价目键重复，导入中止。')
        for row in rows:
            if type(row['price']) is not int or row['price'] <= 0:
                raise CommandError('价格必须是正整数账务单位。')
            try:
                # Imported reference prices are yuan; wallet balances use ledger units.
                expected = Decimal(str(row['price_yuan'])) * 100
                valid_price = expected.is_finite() and expected == row['price']
            except (KeyError, InvalidOperation, ValueError):
                valid_price = False
            if not valid_price:
                raise CommandError('价格换算不符：1元应为10兴安币（100账务单位）：' + row['name'])
            if row['purchase_mode'] == 'DIRECT' and not row['is_gift'] and (not row['platform'] or not row['security_level']):
                raise CommandError('直接下单项缺少平台或适用难度：' + row['name'])
        existing = ServiceItem.objects.filter(catalog_key__in=keys).count()
        self.stdout.write(f"核对 {len(rows)} 项价目；已存在 {existing} 项；上架 {sum(r['is_active'] for r in rows)} 项；说明 {len(book['articles'])} 篇。")
        if not options['apply']:
            self.stdout.write('仅检查；使用 --apply 写入当前本地测试库。')
            return
        media = {}
        asset_root = options['asset_root']
        if asset_root:
            asset_root = asset_root.resolve()
            for rel in sorted({r['cover'] for r in rows if r['cover']}):
                source = (asset_root / rel).resolve()
                if not source.is_relative_to(asset_root) or not source.is_file():
                    raise CommandError('素材不存在或越界：' + rel)
                content = source.read_bytes()
                name = 'club-catalog/' + hashlib.sha256(content).hexdigest()[:24] + source.suffix.lower()
                if not default_storage.exists(name):
                    name = default_storage.save(name, ContentFile(content))
                media[rel] = options['media_base'].rstrip('/') + '/' + name
        with transaction.atomic():
            plays = {}
            for order,(key,(name,subtitle,theme)) in enumerate(GROUPS.items(),1):
                # The importer owns only the xa-catalog-* groups, not admin groups.
                plays[key], _ = Gameplay.objects.update_or_create(code='xa-catalog-'+key, defaults=dict(name=name, subtitle=subtitle, theme=theme, kind='STANDARD', sort_order=order, is_active=any(r['group']==key and r['is_active'] for r in rows)))
            games = {name: GameCategory.objects.get_or_create(name=name)[0] for name in {r['game'] for r in rows}}
            category, _ = ServiceCategory.objects.get_or_create(name='俱乐部价目服务', defaults={'sort_order': 1})
            gifts, _ = ServiceCategory.objects.get_or_create(name='中秋限定礼物', defaults={'is_gift': True, 'sort_order': 2})
            created = updated = 0
            fields = ['name','description','price','platform','security_level','rules','price_unit','is_addon','purchase_mode','purchase_note','catalog_source','is_active','commission_rate','highlights']
            for order,row in enumerate(rows,1):
                defaults = {field: row[field] for field in fields}
                defaults.update(catalog_labels(row))
                defaults.update(gameplay=plays.get(row['group']), game_category=games[row['game']], service_category=gifts if row['is_gift'] else category, sort_order=order)
                if row['cover'] in media:
                    defaults['cover_url'] = media[row['cover']]
                item, was_created = ServiceItem.objects.update_or_create(catalog_key=row['catalog_key'], defaults=defaults)
                item.full_clean()
                created += was_created
                updated += not was_created
            for row in book['articles']:
                Announcement.objects.update_or_create(title=row['title'], defaults={'content': row['content'], 'is_active': True, 'sort_order': 5})
            # Only explicit demo markers from the prior local seed are hidden.
            # Existing orders continue pointing at the same rows; no deletion.
            hidden = 0
            if options['hide_legacy_demos']:
                from django.db.models import Q
                demo = Q(description__contains='本地测试方案') | Q(description__contains='全阶段参考商品') | Q(name__contains='（本地测试）')
                hidden = ServiceItem.objects.filter(demo, catalog_key__isnull=True, is_active=True).update(is_active=False)
                for play in Gameplay.objects.filter(code__in=['roulette','experience','companion','fun','mission','extraction','prison','custom']):
                    if not play.services.filter(is_active=True).exists():
                        play.is_active = False
                        play.save(update_fields=['is_active'])
            for group,play in plays.items():
                first=ServiceItem.objects.filter(gameplay=play,is_active=True).exclude(cover_url='').first()
                if first:
                    play.cover_url=first.cover_url
                    play.save(update_fields=['cover_url'])
        self.stdout.write(self.style.SUCCESS(f'已创建 {created}、更新 {updated}、隐藏旧演示 {hidden}。订单、账号、钱包和流水保持原有记录。'))
