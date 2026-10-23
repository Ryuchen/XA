from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from club_accounts.permissions import IsClubAccountAuthenticated
from .models import SupportContactCard
from .serializers import SupportContactCardSerializer


class PublicContactCardView(APIView):
    """Business contact only, so an unbound customer can resolve login issues."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        card = SupportContactCard.objects.filter(is_active=True).order_by('sort_order', '-updated_at').first()
        return Response({'code': 0, 'data': SupportContactCardSerializer(card).data if card else None})


class ContactCardView(APIView):
    """获取当前激活的客服名片（取排序最靠前的一条）。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        cards = SupportContactCard.objects.filter(is_active=True).order_by('sort_order', '-updated_at')
        order = None
        card_id = request.query_params.get('card_id')
        order_id = request.query_params.get('order_id')
        if order_id:
            from orders.models import Order
            try:
                order = Order.objects.filter(pk=int(order_id), customer_account=request.account).first()
            except (TypeError, ValueError):
                return Response({'code': 400, 'msg': '订单标识无效'})
            if not order:
                return Response({'code': 404, 'msg': '订单不存在'})
            card_id = order.support_contact_id
        if card_id:
            try:
                card = cards.filter(pk=int(card_id)).first()
            except (TypeError, ValueError):
                return Response({'code': 400, 'msg': '客服标识无效'})
            # 原负责客服停用后由当前默认客服接待。
            card = card or cards.first()
        else:
            card = cards.first()
        if not card:
            return Response({'code': 0, 'data': None})
        data = SupportContactCardSerializer(card).data
        data['customer_reference'] = request.account.account_no
        data['order_no'] = order.order_no if order else ''
        return Response({'code': 0, 'data': data})


class ContactCardListView(APIView):
    """自助下单可选择的在岗客服列表。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        cards = SupportContactCard.objects.filter(is_active=True).order_by('sort_order', '-updated_at')
        return Response({'code': 0, 'data': SupportContactCardSerializer(cards, many=True).data})
