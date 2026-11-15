from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer

from .models import Announcement


class AnnouncementSerializer(EntityNumberModelSerializer):
    class Meta:
        model = Announcement
        fields = ['id', 'title', 'content', 'is_pinned', 'created_at']


class AnnouncementDetailSerializer(EntityNumberModelSerializer):
    class Meta:
        model = Announcement
        fields = ['id', 'title', 'content', 'is_pinned', 'created_at', 'updated_at']
