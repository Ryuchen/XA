from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Banner
from .serializers import BannerSerializer


class BannerListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Banner.objects.filter(is_active=True)
        serializer = BannerSerializer(qs, many=True, context={'request': request})
        return Response({'code': 0, 'data': serializer.data})
