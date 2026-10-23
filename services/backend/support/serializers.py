from rest_framework import serializers

from .models import SupportContactCard


class SupportContactCardSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        from common.catalog_images import bounded_public_image_url
        data = super().to_representation(instance)
        for name in ('avatar_url', 'qrcode_url'):
            data[name] = bounded_public_image_url(data[name])
        return data

    class Meta:
        model = SupportContactCard
        fields = [
            'id',
            'name',
            'company',
            'wechat_id',
            'wecom_corp_id',
            'wecom_service_url',
            'avatar_url',
            'qrcode_url',
            'tips',
        ]
