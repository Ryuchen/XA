import json
from django.db import transaction
from rest_framework import serializers
from orders.models import GameCategory
from .models import ProviderQualification
from .qualifications import grade_options, serialize_qualification

class QualificationSerializer(serializers.ModelSerializer):
    game_category=serializers.PrimaryKeyRelatedField(queryset=GameCategory.objects.all())
    class Meta:
        model=ProviderQualification
        fields=['game_category','provider_kind','platform','security_level','grade','price_per_hour','is_active']
        validators=[]
    def validate(self,attrs):
        if not attrs['game_category'].is_active and attrs.get('is_active',True):
            raise serializers.ValidationError('停用游戏不能启用接单资格')
        if attrs['grade'] not in grade_options(attrs['provider_kind'],attrs['platform'],attrs['security_level']):
            raise serializers.ValidationError('该端手游及难度不支持此档位')
        return attrs

class QualificationsField(serializers.Field):
    def to_representation(self,value):return [serialize_qualification(row) for row in value.all()]
    def to_internal_value(self,value):
        if isinstance(value,str):
            try:value=json.loads(value)
            except (ValueError,TypeError):raise serializers.ValidationError('资格配置必须为数组')
        if not isinstance(value,list) or len(value)>80:raise serializers.ValidationError('每个打手最多配置80项资格')
        serializer=QualificationSerializer(data=value,many=True);serializer.is_valid(raise_exception=True)
        rows=serializer.validated_data;keys=[(row['game_category'].pk,row['provider_kind'],row['platform'],row['security_level']) for row in rows]
        if len(keys)!=len(set(keys)):raise serializers.ValidationError('同一游戏、方向、端手游和难度只能配置一个最高档位')
        return rows

@transaction.atomic
def save_qualifications(profile,rows):
    ids=[]
    for row in rows:
        scope={k:row[k] for k in ['game_category','provider_kind','platform','security_level']}
        obj,_=ProviderQualification.objects.update_or_create(profile=profile,**scope,defaults={k:row.get(k) for k in ['grade','price_per_hour']}|{'is_active':row.get('is_active',True)})
        ids.append(obj.pk)
    profile.qualifications.exclude(pk__in=ids).delete()
    profile.game_categories.add(*{row['game_category'].pk for row in rows})
