from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer

from .models import Message


class MessageListItemSerializer(EntityNumberModelSerializer):
    cycle_relation = serializers.SerializerMethodField()

    def get_cycle_relation(self, message):
        account = getattr(self.context.get('request'), 'account', None)
        cycle = message.recipient_business_cycle
        current = getattr(account, 'business_cycle', None)
        if (not account or message.recipient_account_id != account.pk or type(cycle) is not int or cycle <= 0
                or type(current) is not int or current <= 0):
            return 'UNKNOWN'
        if cycle == current:
            return 'CURRENT'
        return 'PREVIOUS' if cycle < current else 'UNKNOWN'

    class Meta:
        model = Message
        fields = [
            'id',
            'type',
            'title',
            'preview',
            'action_url',
            'is_read',
            'related_order_id',
            'created_at',
            'cycle_relation',
        ]


class MessageDetailSerializer(MessageListItemSerializer):
    class Meta:
        model = Message
        fields = [
            'id',
            'type',
            'title',
            'preview',
            'detail',
            'action_url',
            'is_read',
            'related_order_id',
            'created_at',
            'cycle_relation',
        ]
