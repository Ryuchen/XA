from rest_framework import serializers

from .models import Message


class MessageListItemSerializer(serializers.ModelSerializer):
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
        ]


class MessageDetailSerializer(serializers.ModelSerializer):
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
        ]
