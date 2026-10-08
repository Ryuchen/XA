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
        from django.db.models import Q
        from .claims import already_claimed
        coupons = list(Coupon.objects.filter(is_active=True, is_public=True).filter(
            Q(valid_to__isnull=True) | Q(valid_to__gt=timezone.now())))
        claimed_ids = {row.pk for row in coupons if already_claimed(request.account, row)}
        serializer = CouponSerializer(coupons, many=True, context={'claimed_ids': claimed_ids})
        return Response({'code': 0, 'data': serializer.data})


class CouponClaimView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, coupon_id):
        from .claims import claim
        try:
            claim(request.account, request.legacy_user, coupon_id)
        except Coupon.DoesNotExist:
            return Response({'code': 404, 'msg': '优惠券不存在'})
        except IntegrityError:
            return Response({'code': 400, 'msg': '本周期或今日已领取该项资格券'})

        return Response({'code': 0, 'msg': '领取成功'})


class MyCouponListView(APIView):
    """我的优惠券。?usable=1&amount=<分> 仅返回可用于该金额的券。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        from .benefits import grant_periodic, applies_to
        grant_periodic(request.account)
        qs = UserCoupon.objects.filter(account=request.account, business_cycle=request.account.business_cycle).select_related('coupon')

        usable = request.query_params.get('usable')
        if usable in ('1', 'true', 'True'):
            from orders.purchase_benefits import usable_coupons
            qs = usable_coupons(request.account)
            amount = request.query_params.get('amount')
            if amount is not None:
                try:
                    amount_value = int(amount)
                    qs = qs.filter(coupon__threshold__lte=amount_value)
                except (TypeError, ValueError):
                    pass

        service_id = request.query_params.get('service_id')
        if service_id and service_id.isdigit():
            from orders.models import ServiceItem
            service = ServiceItem.objects.filter(pk=int(service_id)).select_related('service_category','required_level').first()
            qs = [row for row in qs if service and applies_to(row.coupon, service)]
        serializer = UserCouponSerializer(qs, many=True)
        return Response({'code': 0, 'data': serializer.data})
