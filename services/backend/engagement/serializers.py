from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from django.utils import timezone
from rest_framework import serializers
from .models import (Broadcast, BroadcastRule, ClubContent, ClubOperationConfig, LotteryCampaign,
    LotteryPrize, MemberProfile, MemberTag, PointLedger, RechargePackage, RewardClaim, RewardItem, VipTier, GiftBenefitRule, ProviderNaming)
from .services import validate_template


def image_url(value):
    if not value:
        return value
    if value.startswith('/media/club-catalog/') and '..' not in value and '\\' not in value:
        return value
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password:
        raise serializers.ValidationError('图片请使用已上传的俱乐部素材地址或 HTTPS 地址')
    return value


class RichText(HTMLParser):
    tags = {'p', 'br', 'strong', 'b', 'em', 'i', 'u', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'blockquote', 'img', 'span', 'div'}
    def __init__(self, display_images=False):
        self.display_images = display_images
        super().__init__(convert_charrefs=True)
        self.output = []
        self.blocked = 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'iframe', 'object'}:
            self.blocked += 1
        if self.blocked or tag not in self.tags:
            return
        if tag == 'img':
            attr = dict(attrs)
            if self.display_images:
                from common.catalog_images import catalog_display_url
                src = catalog_display_url(attr.get('src', ''))
            else:
                src = image_url(attr.get('src', ''))
            if src:
                self.output.append(f'<img src="{escape(src, quote=True)}" style="max-width:100%;height:auto" />')
        else:
            self.output.append(f'<{tag}>')
    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'iframe', 'object'}:
            self.blocked = max(0, self.blocked - 1)
        elif not self.blocked and tag in self.tags and tag not in {'img', 'br'}:
            self.output.append(f'</{tag}>')
    def handle_data(self, data):
        if not self.blocked:
            self.output.append(escape(data))


def rich_text(value):
    if len(value) > 200000:
        raise serializers.ValidationError('内容过长')
    parser = RichText()
    parser.feed(value)
    return ''.join(parser.output)


class RewardSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        if self.context.get('display_images'):
            from common.catalog_images import catalog_display_url
            data['image_url'] = catalog_display_url(data['image_url'])
        return data

    kind_label = serializers.CharField(source='get_kind_display', read_only=True)
    class Meta:
        model = RewardItem
        fields = '__all__'
        read_only_fields = ['redeemed_count']
    validate_image_url = staticmethod(image_url)
    def validate(self, attrs):
        kind = attrs.get('kind', getattr(self.instance, 'kind', ''))
        if self.instance and 'stock' in attrs and self.initial_data.get('redeemed_count') != self.instance.redeemed_count:
            raise serializers.ValidationError('商品库存或兑换记录已变化，请刷新后再保存')
        active = attrs.get('is_active', getattr(self.instance, 'is_active', False))
        shop = attrs.get('in_shop', getattr(self.instance, 'in_shop', True))
        cost = attrs.get('point_cost', getattr(self.instance, 'point_cost', 0))
        if active and shop and cost < 1:
            raise serializers.ValidationError('上架兑换商品前请设置积分价格')
        if active and kind == 'BALANCE' and attrs.get('balance_amount', getattr(self.instance, 'balance_amount', 0)) < 1:
            raise serializers.ValidationError('请设置到账余额')
        if active and kind == 'POINTS' and attrs.get('point_value', getattr(self.instance, 'point_value', 0)) < 1:
            raise serializers.ValidationError('请设置奖励积分')
        if self.instance and self.instance.lotteryprize_set.filter(campaign__enabled=True).exists():
            changed = any(getattr(self.instance, key) != value for key, value in attrs.items())
            if changed:
                raise serializers.ValidationError('请先暂停使用此奖品的抽奖活动，再修改奖品')
        return attrs


class ClaimSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    account_name = serializers.CharField(source='account.nickname', read_only=True)
    class Meta:
        model = RewardClaim
        fields = ['id', 'account', 'account_name', 'reward', 'source', 'source_order', 'name_snapshot', 'kind_snapshot',
            'duration_days', 'duration_months', 'highlighted', 'expires_at', 'points_spent', 'balance_amount', 'status', 'status_label', 'details',
            'delivery_note', 'created_at', 'completed_at']
        read_only_fields = fields


class ClaimDetailsSerializer(serializers.Serializer):
    recipient = serializers.CharField(max_length=50, required=False, allow_blank=True)
    phone = serializers.RegexField(r'^\+?[0-9 -]{6,20}$', required=False, allow_blank=True)
    address = serializers.CharField(max_length=255, required=False, allow_blank=True)
    game_uid = serializers.CharField(max_length=80, required=False, allow_blank=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)
    tag = serializers.CharField(max_length=30, required=False, allow_blank=True)
    provider_name = serializers.CharField(max_length=50, required=False, allow_blank=True)


class MemberEditSerializer(serializers.Serializer):
    birthday = serializers.DateField(required=False)
    birthday_public = serializers.BooleanField(required=False)
    selected_tags = serializers.ListField(child=serializers.CharField(max_length=60), max_length=5, required=False)
    def validate_birthday(self, value):
        if value > timezone.localdate() or value.year < 1900:
            raise serializers.ValidationError('请填写有效生日')
        return value


class BroadcastRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = BroadcastRule
        fields = '__all__'
    def validate_template(self, value):
        validate_template(value)
        return value


class BroadcastSerializer(serializers.ModelSerializer):
    class Meta:
        model = Broadcast
        fields = ['id', 'kind', 'content', 'is_visible', 'starts_at', 'expires_at', 'sort_order', 'created_at', 'published_at']
        read_only_fields = ['id', 'kind', 'content', 'starts_at', 'created_at', 'published_at']


class ProviderNamingSerializer(serializers.ModelSerializer):
    provider_name = serializers.CharField(source='provider.nickname', read_only=True)
    sponsor_name = serializers.CharField(source='sponsor.nickname', read_only=True)
    class Meta:
        model = ProviderNaming
        fields = ['id', 'provider', 'provider_name', 'sponsor', 'sponsor_name', 'title', 'expires_at', 'is_visible', 'updated_at']
        read_only_fields = ['id', 'provider', 'sponsor', 'expires_at', 'updated_at']
    def validate_title(self, value):
        from orders.gifts import naming_title
        return naming_title(value, required=True)


class ContentSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        if self.context.get('display_images'):
            from common.catalog_images import catalog_display_url
            data['image_url'] = catalog_display_url(data['image_url'])
            parser = RichText(display_images=True)
            parser.feed(data['content'])
            parser.close()
            data['content'] = ''.join(parser.output)
        return data

    class Meta:
        model = ClubContent
        fields = '__all__'
    validate_image_url = staticmethod(image_url)
    validate_content = staticmethod(rich_text)
    def validate(self, attrs):
        start = attrs.get('starts_at', getattr(self.instance, 'starts_at', None))
        end = attrs.get('ends_at', getattr(self.instance, 'ends_at', None))
        if start and end and start >= end:
            raise serializers.ValidationError('结束时间必须晚于开始时间')
        return attrs


class ConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClubOperationConfig
        fields = '__all__'
        read_only_fields = ['id', 'updated_at', 'vip_min_single_recharge', 'coupon_stacking']
    def validate(self, attrs):
        season = attrs.get('season_code', getattr(self.instance, 'season_code', ''))
        end = attrs.get('season_ends_at', getattr(self.instance, 'season_ends_at', None))
        if season and not end:
            raise serializers.ValidationError('配置赛季编号时，请同时设置赛季结束时间')
        return attrs


class PackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = RechargePackage
        fields = '__all__'


class TierSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='boss_type.name', read_only=True)
    discount_rate = serializers.IntegerField(source='boss_type.discount_rate', read_only=True)
    consumption_threshold = serializers.IntegerField(source='recharge_threshold', min_value=1, required=False)
    upgrade_basis = serializers.CharField(read_only=True, default='CONFIRMED_CONSUMPTION')
    class Meta:
        model = VipTier
        fields = '__all__'
    def _binding(self, value, kind):
        if value is None or value == []:
            return []
        if not isinstance(value, list) or len(value) > 100 or any(type(pk) is not int or pk < 1 for pk in value) or len(set(value)) != len(value):
            raise serializers.ValidationError('请选择不重复的服务编号')
        from orders.models import ServiceItem
        valid = ServiceItem.objects.filter(pk__in=value, is_active=True, is_addon=False,
            purchase_mode='DIRECT', billing_provider_count=1, platform__in=['PC','MOBILE'], benefit_kind=kind).exclude(service_category__is_gift=True)
        if kind == 'COMPANION_HOUR':
            valid = valid.filter(price_unit__in=['小时','时','hour'])
        if valid.count() != len(value):
            raise serializers.ValidationError('绑定须为已确认权益分类的端游/手游单陪主套餐；陪玩按小时计价')
        return sorted(value)
    def validate_companion_service_ids(self, value):
        return self._binding(value, 'COMPANION_HOUR')
    def validate_safe_box_service_ids(self, value):
        return self._binding(value, 'SAFEBOX_33')
    def validate(self, attrs):
        data = getattr(self, 'initial_data', {})
        if 'consumption_threshold' in data and 'recharge_threshold' in data:
            if data['consumption_threshold'] != data['recharge_threshold']:
                raise serializers.ValidationError('累计消费门槛参数不一致，请刷新后再保存')
        for key in ('monthly_discount', 'season_discount'):
            if key in attrs and not (0 if key == 'season_discount' else 1) <= attrs[key] <= 100:
                raise serializers.ValidationError('折扣比例必须为 1–100')
        level = attrs.get('level', getattr(self.instance, 'level', None))
        if level is not None:
            if not 1 <= level <= 6:
                raise serializers.ValidationError('VIP等级须为1至6')
            attrs['monthly_discount'] = 85 if level == 6 else 90
            attrs['monthly_coupon_count'] = [0,1,2,4,10,10][level-1]
            attrs['season_discount'] = [80,80,80,70,60,0][level-1]
        bundles = attrs.get('upgrade_rewards', [])
        if not isinstance(bundles, list) or len(bundles) > 30:
            raise serializers.ValidationError('升级权益配置格式错误')
        for item in bundles:
            if not isinstance(item, dict) or set(item) != {'reward', 'quantity'}:
                raise serializers.ValidationError('升级权益须指定 reward 和 quantity')
            if type(item['quantity']) is not int or not 1 <= item['quantity'] <= 100:
                raise serializers.ValidationError('权益份数应为 1–100')
            if not RewardItem.objects.filter(pk=item['reward'], kind__in=['SERVICE', 'NAMING', 'TAG'], in_shop=False).exists():
                raise serializers.ValidationError('VIP 权益请选择不在积分商城上架的服务、冠名或 TAG')
        return attrs


def lottery_daily_remaining(account, campaign):
    if not account or not campaign.daily_limit:
        return None
    from django.utils import timezone
    from .models import LotteryDraw
    used = LotteryDraw.objects.filter(account=account, campaign=campaign, business_cycle=account.business_cycle,
        created_at__date=timezone.localdate()).count()
    return max(0, campaign.daily_limit - used)


class CampaignSerializer(serializers.ModelSerializer):
    prizes = serializers.SerializerMethodField()
    pity = serializers.SerializerMethodField()
    draw_counts = serializers.SerializerMethodField()
    ten_draw_cost = serializers.SerializerMethodField()
    daily_remaining = serializers.SerializerMethodField()
    ten_guarantee = serializers.SerializerMethodField()
    class Meta:
        model = LotteryCampaign
        fields = '__all__'
        read_only_fields = ['version']
    def get_prizes(self, obj):
        from common.catalog_images import catalog_display_url
        from .services import lottery_rarity
        return [{'id': row.pk, 'reward': row.reward_id, 'name': row.reward.name,
            'reward_code': row.reward.code, 'rarity': lottery_rarity(row.reward),
            'kind': row.reward.kind, 'image_url': catalog_display_url(row.reward.image_url) if self.context.get('display_images') else row.reward.image_url,
            'probability': row.probability, 'sort_order': row.sort_order}
            for row in obj.prizes.select_related('reward').all()]

    def get_pity(self, obj):
        from .services import lottery_pity
        account = self.context.get('account')
        return lottery_pity(account, obj, count=0 if account is None else None)

    def get_draw_counts(self, obj):
        return [1, 10]

    def get_ten_draw_cost(self, obj):
        return obj.point_cost * 10

    def get_daily_remaining(self, obj):
        return lottery_daily_remaining(self.context.get('account'), obj)

    def get_ten_guarantee(self, obj):
        from .services import lottery_ten_guarantee
        return lottery_ten_guarantee(obj)


class GiftBenefitSerializer(serializers.ModelSerializer):
    class Meta:
        model = GiftBenefitRule
        fields = '__all__'
    def validate(self, attrs):
        get = lambda key: attrs.get(key, getattr(self.instance, key, None))
        if get('category') and not get('category').is_gift:
            raise serializers.ValidationError('请选择礼物分类')
        if get('reward') and get('reward').kind not in ['SERVICE', 'NAMING', 'TAG']:
            raise serializers.ValidationError('赠礼附送权益仅支持服务、冠名和 TAG')
        start, end = get('starts_at'), get('ends_at')
        if start and end and start >= end:
            raise serializers.ValidationError('结束时间须晚于开始时间')
        if get('cumulative') and not start:
            raise serializers.ValidationError('累计活动须设置起始时间')
        return attrs
