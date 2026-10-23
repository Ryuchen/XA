from rest_framework import serializers
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response

from orders.models import Gameplay
from .mixins import EnvelopeViewSetMixin
from engagement.console import ContentImageUploadView


class GameplaySerializer(serializers.ModelSerializer):
    rules = serializers.CharField(max_length=5000, allow_blank=True, required=False)
    details_html = serializers.CharField(max_length=100000, allow_blank=True, required=False)

    def validate_details_html(self, value):
        from common.rich_text import sanitize_catalog_html
        return sanitize_catalog_html(value)
    service_count = serializers.IntegerField(source='services.count', read_only=True)

    class Meta:
        model = Gameplay
        fields = ['id', 'name', 'code', 'kind', 'subtitle', 'cover_url', 'theme', 'rules', 'details_html', 'sort_order', 'is_active', 'is_archived', 'service_count']
        read_only_fields = ['is_archived']

    def validate(self, attrs):
        if self.instance and self.instance.is_archived and attrs.get('is_active'):
            raise serializers.ValidationError('请先恢复已归档玩法，再启用')
        if self.instance and attrs.get('kind', self.instance.kind) != self.instance.kind and self.instance.services.exists():
            raise serializers.ValidationError('已有商品的玩法不能更改类型，请新建玩法')
        return attrs


class GameplayViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = Gameplay.objects.all()
    serializer_class = GameplaySerializer
    default_perm = 'service:view'
    required_perms = {'create': 'service:edit', 'update': 'service:edit', 'partial_update': 'service:edit', 'destroy': 'service:delete', 'restore': 'service:edit'}

    def get_queryset(self):
        archived = self.action == 'restore' or self.request.query_params.get('is_archived') == 'true'
        return Gameplay.objects.filter(is_archived=archived)

    def perform_destroy(self, instance):
        # Historical dispatches retain their product and gameplay references.
        instance.is_active = False
        instance.is_archived = True
        instance.save(update_fields=['is_active', 'is_archived'])

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        instance = self.get_object()
        instance.is_archived = False
        instance.save(update_fields=['is_archived'])
        return Response({'code': 0, 'data': self.get_serializer(instance).data})


class CatalogImageUploadView(ContentImageUploadView):
    default_perm = 'service:edit'

    def post(self, request):
        from common.catalog_images import save_catalog_upload
        from django.core.exceptions import ValidationError as ImageValidationError
        try:
            upload = serializers.ImageField().run_validation(request.FILES.get('image'))
        except ImageValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        if upload.size > 10 * 1024 * 1024:
            raise serializers.ValidationError('图片大小不能超过 10MB')
        try:
            data = save_catalog_upload(upload, 'club-catalog/price-details')
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from error
        return Response({'code': 0, 'data': data})
