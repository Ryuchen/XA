from rest_framework import serializers

from .models import SupportContactCard


class SupportContactCardSerializer(serializers.ModelSerializer):
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
