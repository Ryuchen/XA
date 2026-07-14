from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Announcement
from .serializers import AnnouncementDetailSerializer, AnnouncementSerializer

DEFAULT_LIMIT = 5
MAX_LIMIT = 20


class AnnouncementListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = int(request.query_params.get('limit', DEFAULT_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        qs = Announcement.objects.filter(is_active=True)[:limit]
        serializer = AnnouncementSerializer(qs, many=True)
        return Response({'code': 0, 'data': serializer.data})


class AnnouncementDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            announcement = Announcement.objects.get(id=pk, is_active=True)
        except Announcement.DoesNotExist:
            return Response({'code': 404, 'msg': '公告不存在'})
        return Response({'code': 0, 'data': AnnouncementDetailSerializer(announcement).data})
