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
    threshold = serializers.IntegerField(source='coupon.threshold', read_only=True)
    amount = serializers.IntegerField(source='coupon.amount', read_only=True)
    valid_to = serializers.DateTimeField(source='coupon.valid_to', read_only=True)

    class Meta:
        model = UserCoupon
        fields = [
            'id',
            'name',
            'discount_type',
            'threshold',
            'amount',
            'valid_to',
            'status',
            'claimed_at',
            'used_at',
        ]
