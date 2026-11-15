from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer

from common.media import build_media_url
from .models import ChatMessage, ChatSession


def chat_sender_name(message, *, staff_view=False):
    """Display the author of this message, not the session's current receiver."""
    sender = message.sender_account
    if sender:
        if sender.nickname:
            return sender.nickname
        if staff_view:
            return sender.username
    return '客服' if message.is_from_support else '咨询人'


class ChatReadSerializer(serializers.Serializer):
    through_id = serializers.IntegerField(min_value=0, required=False)
    message_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=200, required=False)

    def validate(self, attrs):
        if 'through_id' in attrs and 'message_ids' in attrs:
            raise serializers.ValidationError('请选择一种已读范围')
        if 'through_id' not in attrs and 'message_ids' not in attrs:
            raise serializers.ValidationError('请选择已查看的消息范围')
        return attrs


class ChatMessageSerializer(EntityNumberModelSerializer):
    """C 端聊天消息序列化器。"""

    image_url = serializers.SerializerMethodField()
    content_type_display = serializers.CharField(source='get_content_type_display', read_only=True)
    order_card = serializers.SerializerMethodField()
    sender_name = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = [
            'id', 'is_from_support', 'content_type', 'content_type_display',
            'content', 'image_url', 'is_read', 'created_at', 'client_message_id', 'order_card', 'sender_name',
        ]

    def get_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.image)

    def get_order_card(self, obj):
        from .order_context import serialized_order_card
        return serialized_order_card(self.context.get('request'), obj, self.context)

    def get_sender_name(self, obj):
        from club_accounts.models import ClubAccount
        account = getattr(self.context.get('request'), 'account', None)
        return chat_sender_name(obj, staff_view=bool(account and account.account_type == ClubAccount.AccountType.STAFF))

    def to_representation(self, instance):
        from club_accounts.models import ClubAccount
        data = super().to_representation(instance)
        account = getattr(self.context.get('request'), 'account', None)
        if account and account.account_type == ClubAccount.AccountType.STAFF:
            data['sender_account'] = instance.sender_account_id
        return data


class ChatSessionSerializer(EntityNumberModelSerializer):
    """C 端会话概览序列化器。"""

    class Meta:
        model = ChatSession
        fields = [
            'id', 'last_message', 'last_message_at', 'unread_user', 'created_at',
        ]
