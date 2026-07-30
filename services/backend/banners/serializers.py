from rest_framework import serializers

from common.media import build_media_url
from .models import Banner


class BannerSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Banner
        fields = ['id', 'image_url', 'title', 'link_type', 'link_value']

    def get_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.image)
