from rest_framework import serializers

from common.media import build_media_url
from .models import Banner


class BannerSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    link_available = serializers.BooleanField(read_only=True, default=True)
    link_unavailable_reason = serializers.CharField(read_only=True, default='')

    class Meta:
        model = Banner
        fields = ['id', 'image_url', 'title', 'link_type', 'link_value', 'link_available', 'link_unavailable_reason']

    def get_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.image)
