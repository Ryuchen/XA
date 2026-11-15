from rest_framework import serializers
from rest_framework.serializers import LIST_SERIALIZER_KWARGS, LIST_SERIALIZER_KWARGS_REMOVE

from .services import get_entity_number, prime_entity_numbers


class EntityNumberListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        rows = list(data.all() if hasattr(data, 'all') else data)
        prime_entity_numbers(rows)
        return super().to_representation(rows)


class EntityNumberModelSerializer(serializers.ModelSerializer):
    @classmethod
    def many_init(cls, *args, **kwargs):
        list_kwargs = {}
        for key in LIST_SERIALIZER_KWARGS_REMOVE:
            value = kwargs.pop(key, None)
            if value is not None:
                list_kwargs[key] = value
        list_kwargs['child'] = cls(*args, **kwargs)
        list_kwargs.update({key: value for key, value in kwargs.items() if key in LIST_SERIALIZER_KWARGS})
        meta = getattr(cls, 'Meta', None)
        list_class = getattr(meta, 'list_serializer_class', EntityNumberListSerializer)
        return list_class(*args, **list_kwargs)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance is not None and getattr(instance, 'pk', None) is not None:
            data['entity_no'] = get_entity_number(instance)
        return data
