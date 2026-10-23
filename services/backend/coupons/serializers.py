from rest_framework import serializers

from .models import Coupon, UserCoupon


class CouponSerializer(serializers.ModelSerializer):
    is_claimed = serializers.SerializerMethodField()
    claimable = serializers.SerializerMethodField()

    class Meta:
        model = Coupon
        fields = [
            'id',
            'name',
            'discount_type',
            'discount_rate', 'scope', 'scope_variant', 'limit_units', 'service_ids', 'max_discount', 'experience_kind', 'platform',
            'threshold',
            'amount',
            'valid_to',
            'is_claimed',
            'claimable',
        ]

    def get_is_claimed(self, obj):
        claimed_ids = self.context.get('claimed_ids', set())
        return obj.id in claimed_ids

    def get_claimable(self, obj):
        return obj.is_claimable


class UserCouponSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='coupon.name', read_only=True)
    discount_type = serializers.CharField(source='coupon.discount_type', read_only=True)
    discount_rate = serializers.IntegerField(source='coupon.discount_rate', read_only=True)
    scope = serializers.CharField(source='coupon.scope', read_only=True)
    scope_variant = serializers.CharField(source='coupon.scope_variant', read_only=True)
    service_ids = serializers.JSONField(source='coupon.service_ids', read_only=True)
    limit_units = serializers.IntegerField(source='coupon.limit_units', read_only=True)
    threshold = serializers.IntegerField(source='coupon.threshold', read_only=True)
    amount = serializers.IntegerField(source='coupon.amount', read_only=True)
    max_discount = serializers.IntegerField(source='coupon.max_discount', read_only=True)
    experience_kind = serializers.CharField(source='coupon.experience_kind', read_only=True)
    platform = serializers.CharField(source='coupon.platform', read_only=True)
    valid_to = serializers.DateTimeField(read_only=True)

    class Meta:
        model = UserCoupon
        fields = [
            'id',
            'name',
            'discount_type',
            'discount_rate', 'scope', 'scope_variant', 'limit_units', 'service_ids', 'max_discount', 'experience_kind', 'platform',
            'threshold',
            'amount',
            'valid_to',
            'status',
            'claimed_at',
            'used_at',
        ]
