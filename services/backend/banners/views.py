from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from .models import Banner
from .serializers import BannerSerializer
from .availability import prepare_banner_availability


class BannerListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        rows = prepare_banner_availability(Banner.objects.filter(is_active=True), account=request.account)
        serializer = BannerSerializer(rows, many=True, context={'request': request})
        return Response({'code': 0, 'data': serializer.data})
