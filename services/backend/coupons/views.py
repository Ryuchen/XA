from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from .models import Coupon, UserCoupon
from .serializers import CouponSerializer, UserCouponSerializer


class CouponListView(APIView):
    """可领取的优惠券列表（带当前用户是否已领标记）。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        coupons = Coupon.objects.filter(is_active=True, valid_to__gt=timezone.now())
        claimed_ids = set(
            UserCoupon.objects.filter(
                account=request.account,
                coupon__in=coupons,
            ).values_list('coupon_id', flat=True)
        )
        serializer = CouponSerializer(
            coupons,
            many=True,
            context={'claimed_ids': claimed_ids},
        )
        return Response({'code': 0, 'data': serializer.data})


class CouponClaimView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, coupon_id):
        try:
            with transaction.atomic():
                try:
                    coupon = Coupon.objects.select_for_update().get(id=coupon_id)
                except Coupon.DoesNotExist:
                    return Response({'code': 404, 'msg': '优惠券不存在'})

                if not coupon.is_claimable:
                    return Response({'code': 400, 'msg': '该券已抢光或已过期'})

                UserCoupon.objects.create(
                    user=request.legacy_user,
                    account=request.account,
                    coupon=coupon,
                )
                Coupon.objects.filter(id=coupon.id).update(claimed_qty=F('claimed_qty') + 1)
        except IntegrityError:
            return Response({'code': 400, 'msg': '您已领取过该券'})

        return Response({'code': 0, 'msg': '领取成功'})


class MyCouponListView(APIView):
    """我的优惠券。?usable=1&amount=<分> 仅返回可用于该金额的券。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        qs = UserCoupon.objects.filter(account=request.account).select_related('coupon')

        usable = request.query_params.get('usable')
        if usable in ('1', 'true', 'True'):
            qs = qs.filter(status=UserCoupon.Status.UNUSED, coupon__valid_to__gt=timezone.now())
            amount = request.query_params.get('amount')
            if amount is not None:
                try:
                    amount_value = int(amount)
                    qs = qs.filter(coupon__threshold__lte=amount_value)
                except (TypeError, ValueError):
                    pass

        serializer = UserCouponSerializer(qs, many=True)
        return Response({'code': 0, 'data': serializer.data})
