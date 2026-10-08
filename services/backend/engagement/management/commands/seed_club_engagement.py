from django.core.management.base import BaseCommand
from django.core.files import File
from django.core.files.storage import default_storage
from pathlib import Path
from django.db import transaction
from users.models import BossType
from orders.models import ServiceCategory, ServiceItem
from engagement.catalog import (VIP_THRESHOLDS, VIP_DISCOUNTS, VIP_COMPANIONS, VIP_NAMING,
    VIP_TAGS, PREPAID, GIFT_CATALOG, POINT_REWARDS, ACCOUNT_SUBSIDIES)
from engagement.models import (BroadcastRule, ClubContent, ClubOperationConfig, LotteryCampaign,
    LotteryPrize, RechargePackage, RewardItem, VipTier, GiftBenefitRule)
from engagement.services import DEFAULT_BROADCASTS


class Command(BaseCommand):
    help = '按俱乐部海报录入会员运营测试目录，不覆盖已配置的概率、库存或现有奖品价格'
    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='确认写入目录；默认只显示规模')
    @transaction.atomic
    def handle(self, *args, **options):
        if not options['apply']:
            self.stdout.write('将录入：6 个 VIP 档位、6 个预存档位、15 款礼物、32 款积分好礼、10 个打手补贴草稿、13 个转盘奖项、8 类播报。使用 --apply 写入。')
            return
        from orders.management.commands.seed_club_catalog import validate_import_database
        validate_import_database()
        ClubOperationConfig.objects.get_or_create(pk=1)
        asset_root = Path(__file__).resolve().parents[2] / 'assets'
        for path in asset_root.rglob('*.png'):
            target = 'club-catalog/' + path.relative_to(asset_root).as_posix()
            if not default_storage.exists(target):
                with path.open('rb') as source:
                    default_storage.save(target, File(source))
        for level, amount in enumerate(VIP_THRESHOLDS, 1):
            bundles = []
            tag_count = [1,1,2,2,3,5][level-1]
            highlighted = [0,0,0,1,2,5][level-1]
            for bright, count in [(False, tag_count-highlighted), (True, highlighted)]:
                reward, _ = RewardItem.objects.get_or_create(code='vip-tag-highlight' if bright else 'vip-tag', defaults={
                    'name': 'VIP 自定义高亮 TAG' if bright else 'VIP 自定义 TAG', 'kind': 'TAG',
                    'highlighted': bright, 'is_active': True, 'in_shop': False})
                if count: bundles.append({'reward': reward.pk, 'quantity': count})
            reward, _ = RewardItem.objects.get_or_create(code='vip-naming-week' if level<=3 else 'vip-naming-month', defaults={
                'name': 'VIP 冠名 7 天' if level<=3 else 'VIP 冠名一个月', 'kind': 'NAMING',
                'duration_days': 7 if level<=3 else 0, 'duration_months': 1 if level>3 else 0,
                'is_active': True, 'in_shop': False})
            bundles.append({'reward': reward.pk, 'quantity': [3,6,6,6,10,12][level-1]})
            if level>=3:
                reward, _ = RewardItem.objects.get_or_create(code='vip-friend-slot', defaults={
                    'name': 'VIP 陪玩好友位（不含明星）', 'kind': 'SERVICE', 'is_active': True, 'in_shop': False})
                bundles.append({'reward': reward.pk, 'quantity': [1,2,5,10][level-3]})
            if level==6:
                reward, _ = RewardItem.objects.get_or_create(code='vip-safebox-permanent', defaults={
                    'name': 'VIP6 赛季安全箱永久代肝', 'kind': 'SERVICE', 'is_active': True, 'in_shop': False})
                bundles.append({'reward': reward.pk, 'quantity': 1})
            boss_type, _ = BossType.objects.get_or_create(name=f'VIP{level}', defaults={
                'discount_rate': VIP_DISCOUNTS[level - 1], 'sort_order': level,
                'remark': '按实际人民币累充自动升级', 'color': '#B83D68'})
            benefits = {'优先派单': '享有', 'KOOK 专属冠名频道': '享有', 'KOOK 专属 TAG': '享有',
                '陪玩赠送': VIP_COMPANIONS[level-1], '赛季安全箱': ['8 折券','8 折券','8 折券','7 折券','6 折券','永久代肝'][level-1],
                '每月下单券': ['无','9 折 ×1','9 折 ×2','9 折 ×4','9 折 ×10','85 折 ×10'][level-1],
                'TAG 赠送': VIP_TAGS[level-1], '打手冠名': VIP_NAMING[level-1],
                '陪玩好友位': ['无','无','1 个','2 个','5 个','10 个'][level-1]}
            if level >= 2: benefits.update({'专属 VIP 客服': '享有', '定制专属趣味玩法': '享有'})
            if level >= 3: benefits['专属群打手冠名'] = '享有'
            if level >= 4: benefits.update({'生日下单': '9 折，与 VIP 折扣取优', '生日定制礼物': '享有'})
            if level >= 5: benefits.update({'派单群置顶': '7 天', '店长微信直接对接': '享有'})
            VipTier.objects.get_or_create(level=level, defaults={'boss_type': boss_type,
                'recharge_threshold': amount * 100, 'benefits': benefits, 'upgrade_rewards': bundles,
                'monthly_discount': 85 if level == 6 else 90, 'monthly_coupon_count': [0,1,2,4,10,10][level-1],
                'season_discount': [80,80,80,70,60,100][level-1]})
        for amount, gift, rate in PREPAID:
            RechargePackage.objects.get_or_create(amount=amount*100, defaults={'gift_amount': gift*100, 'points_rate': rate})
        for index, (code, name, yuan, category_name) in enumerate(GIFT_CATALOG):
            category, _ = ServiceCategory.objects.get_or_create(name=category_name, defaults={'is_gift': True, 'is_active': True})
            if not category.is_gift:
                raise ValueError(f'{category_name} 已被非礼物分类占用，请先核对')
            old = ServiceItem.objects.filter(catalog_key__startswith='xa-price-', service_category__is_gift=True,
                name=f'中秋限定礼物 · {name}', price=yuan*100).first()
            if old:
                if category_name == '中秋限定' and not old.gift_festival:
                    old.gift_festival = '中秋限定'
                    old.save(update_fields=['gift_festival'])
                # 复用原价目导入的六款中秋礼物，订单引用保持不变。
                if not old.cover_url or '/gifts/' not in old.cover_url:
                    old.cover_url = f'/media/club-catalog/gifts/{code}.png'
                    old.service_category = category
                    old.save(update_fields=['cover_url', 'service_category'])
                continue
            ServiceItem.objects.get_or_create(catalog_key=f'club-gift-{code}', defaults={
                'name': name, 'price': yuan*100, 'service_category': category, 'price_unit': '份',
                'gift_festival': '中秋限定' if category_name == '中秋限定' else '',
                'purchase_mode': 'DIRECT', 'commission_rate': None, 'is_active': True, 'sort_order': index,
                'cover_url': f'/media/club-catalog/gifts/{code}.png',
                'description': '俱乐部' + category_name + '，送给欣赏的陪玩',
                'highlights': ['即时送达', '赠送记录可查']})
        for index, (code, name, cost, kind) in enumerate(POINT_REWARDS):
            RewardItem.objects.get_or_create(code=f'points-{code}', defaults={
                'name': name, 'point_cost': cost, 'kind': kind, 'in_shop': True, 'is_active': True,
                'sort_order': index, 'description': '按俱乐部积分礼物兑换表配置；兑换后由客服安排交付。'})
        permanent, _ = RewardItem.objects.get_or_create(code='gift-permanent-naming', defaults={
            'name': '永久打手冠名', 'kind': 'NAMING', 'is_active': True, 'in_shop': False})
        GiftBenefitRule.objects.get_or_create(name='赠礼永久冠名', defaults={
            'reward': permanent, 'threshold': 131400, 'inclusive': False, 'enabled': False})
        holiday_tag, _ = RewardItem.objects.get_or_create(code='gift-midautumn-tag', defaults={
            'name': '中秋限定 TAG 一年', 'kind': 'TAG', 'duration_months': 12, 'is_active': True, 'in_shop': False})
        GiftBenefitRule.objects.get_or_create(name='中秋限定 TAG', defaults={
            'reward': holiday_tag, 'category': ServiceCategory.objects.get(name='中秋限定'),
            'threshold': 21000, 'inclusive': False, 'once_per_account': True, 'enabled': False})
        # 30 元外卖支持兑换等值店铺余额，单独陈列选择，避免把兑物强行换成余额。
        RewardItem.objects.get_or_create(code='points-takeout-balance', defaults={'name': '30 元外卖兑换为余额',
            'point_cost': 3600, 'kind': 'BALANCE', 'balance_amount': 3000, 'is_active': True,
            'description': '到账 300 兴安币，可用于后续服务，不计入累充。', 'sort_order': 33})
        for index, (band, cost) in enumerate(ACCOUNT_SUBSIDIES):
            RewardItem.objects.get_or_create(code=f'provider-account-{index}', defaults={
                'name': f'{band} 纯币账号补贴（仅打手）', 'point_cost': cost, 'kind': 'SERVICE',
                'is_active': False, 'in_shop': False, 'sort_order': 100+index,
                'description': '海报注明仅限店内打手，不在老板积分商城开放。500M–600M 档积分需确认。'})
        campaign, _ = LotteryCampaign.objects.get_or_create(name='兴安幸运转盘', defaults={
            'point_cost': 100, 'enabled': False, 'rules': '每次消耗 100 积分。奖品以中奖记录为准，积分奖励自动到账，其余奖品由俱乐部安排交付。奖项概率由后台配置，总计 100%。'})
        prizes = [('experience-68', '68 体验单', 'SERVICE', 0, 0)] + [
            (f'points-{value}', f'{value} 积分', 'POINTS', value, 0) for value in [200,150,80,60,40,20,10,5]] + [
            (f'naming-{days}', f'任选冠名 {days} 天', 'NAMING', 0, days) for days in [3,5,7]] + [('custom-tag', '自定义 TAG', 'TAG', 0, 0)]
        for index, (code, name, kind, point_value, days) in enumerate(prizes):
            reward, _ = RewardItem.objects.get_or_create(code=f'lottery-{code}', defaults={
                'name': name, 'kind': kind, 'point_value': point_value, 'duration_days': days,
                'in_shop': False, 'is_active': True, 'sort_order': 200+index})
            LotteryPrize.objects.get_or_create(campaign=campaign, reward=reward, defaults={'probability': 0, 'sort_order': index})
        for kind, template in DEFAULT_BROADCASTS.items():
            BroadcastRule.objects.get_or_create(kind=kind, defaults={'template': template})
        for code, kind, title, file in [
            ('vip-rights','RIGHTS','VIP 预存福利','vip-benefits.png'),
            ('point-catalog','POINT_RULES','积分礼物兑换表','point-catalog.png'),
            ('point-delivery','POINT_RULES','礼物兑换须知','point-delivery.png'),
        ]:
            ClubContent.objects.get_or_create(code=code, defaults={'kind': kind, 'title': title,
                'image_url': f'/media/club-catalog/operations/{file}'})
        ClubContent.objects.filter(code='welcome-rights', kind='POPUP').update(is_active=False)
        # 原有已核对文案迁入独立权益/热点区，不再混入普通公告列表。
        import json
        from html import escape
        from orders.management.commands.seed_club_catalog import CATALOG
        articles = json.loads(CATALOG.read_text(encoding='utf-8'))['articles']
        article = next(row for row in articles if row['title'].startswith('双节大促'))
        ClubContent.objects.get_or_create(code='festival-current', defaults={
            'kind': 'HOT', 'title': '中秋国庆双节活动', 'sort_order': 100,
            'image_url': '/media/club-catalog/operations/festival-mobile.png',
            'content': '<p>' + escape(article['content']).replace('\n', '</p><p>') + '</p>',
            'starts_at': '2026-09-19T00:00:00+08:00', 'ends_at': '2026-10-12T00:00:00+08:00'})
        ClubContent.objects.filter(code='festival-current', image_url='/media/club-catalog/operations/festival.png').update(image_url='/media/club-catalog/operations/festival-mobile.png')
        ClubContent.objects.get_or_create(code='gift-catalog', defaults={'kind': 'RIGHTS',
            'title': '一份心意 · 俱乐部礼物单', 'image_url': '/media/club-catalog/operations/regular-gifts.png'})
        for code, kind, title, text in [
            ('order-guide','ORDER_RULES','下单须知','下单前核对游戏、平台、套餐和服务规则。不同价格档位在服务详情选择；无法直接下单的定制项目，请联系客服确认方案。'),
            ('provider-guide','PROVIDER_RULES','打手保障','订单派发、接单与服务进度均以系统记录为准。请通过订单客服协调时间和服务要求，遇到争议保留相关对局记录。'),
            ('aftersale-guide','AFTERSALE_RULES','售后标准','售后按所购套餐的履约规则核对。请从订单详情联系专属客服，提供订单号与相关证据，由俱乐部核实处理。'),
        ]:
            poster = {'ORDER_RULES': 'order-rules', 'PROVIDER_RULES': 'provider-rules', 'AFTERSALE_RULES': 'aftersale-rules'}[kind]
            ClubContent.objects.get_or_create(code=code, defaults={'kind': kind, 'title': title, 'content': '<p>'+text+'</p>',
                'image_url': f'/media/club-catalog/operations/{poster}.png'})
        from announcements.models import Announcement
        Announcement.objects.filter(title__in=[row['title'] for row in articles if row['title'].startswith(('预存福利','双节大促','中秋限定礼物'))]).update(is_active=False)
        self.stdout.write(self.style.SUCCESS('俱乐部目录已录入。转盘保持未启用，待后台配置概率。'))
