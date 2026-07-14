from rest_framework import serializers

from .models import Banner


class BannerSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Banner
        fields = ['id', 'image_url', 'title', 'link_type', 'link_value']

    def get_image_url(self, obj):
        if not obj.image:
            return ''
        url = obj.image.url
        request = self.context.get('request')
        if request is not None:
            return request.build_absolute_uri(url)
        return url
