from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SupportContactCard
from .serializers import SupportContactCardSerializer


class ContactCardView(APIView):
    """获取当前激活的客服名片（取排序最靠前的一条）。"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        card = SupportContactCard.objects.filter(is_active=True).order_by('sort_order', '-updated_at').first()
        if not card:
            return Response({'code': 0, 'data': None})
        return Response({'code': 0, 'data': SupportContactCardSerializer(card).data})


class ContactCardListView(APIView):
    """自助下单可选择的在岗客服列表。"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cards = SupportContactCard.objects.filter(is_active=True).order_by('sort_order', '-updated_at')
        return Response({'code': 0, 'data': SupportContactCardSerializer(cards, many=True).data})
