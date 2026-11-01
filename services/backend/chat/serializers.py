from rest_framework import serializers

from common.media import build_media_url
from .models import ChatMessage, ChatSession


class ChatReadSerializer(serializers.Serializer):
    through_id = serializers.IntegerField(min_value=0, required=False)
    message_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=200, required=False)

    def validate(self, attrs):
        if 'through_id' in attrs and 'message_ids' in attrs:
            raise serializers.ValidationError('请选择一种已读范围')
        return attrs


class ChatMessageSerializer(serializers.ModelSerializer):
    """C 端聊天消息序列化器。"""

    image_url = serializers.SerializerMethodField()
    content_type_display = serializers.CharField(source='get_content_type_display', read_only=True)

    class Meta:
        model = ChatMessage
        fields = [
            'id', 'is_from_support', 'content_type', 'content_type_display',
            'content', 'image_url', 'is_read', 'created_at', 'client_message_id',
        ]

    def get_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.image)


class ChatSessionSerializer(serializers.ModelSerializer):
    """C 端会话概览序列化器。"""

    class Meta:
        model = ChatSession
        fields = [
            'id', 'last_message', 'last_message_at', 'unread_user', 'created_at',
        ]
