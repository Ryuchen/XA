from rest_framework import serializers

from .models import ProviderReport, WithdrawRequest


class ReportSerializer(serializers.ModelSerializer):
    """C 端报单：陪玩提交与查询自己的报单。"""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    proof_image_url = serializers.SerializerMethodField()
    order_id = serializers.IntegerField(read_only=True)
    order_no = serializers.CharField(source='order.order_no', read_only=True, default='')
    service_name = serializers.CharField(source='order.service_name_snapshot', read_only=True, default='')
    entry_image_url = serializers.SerializerMethodField()
    completion_image_url = serializers.SerializerMethodField()
    result_image_urls = serializers.SerializerMethodField()

    class Meta:
        model = ProviderReport
        fields = [
            'id', 'order_id', 'order_no', 'service_name', 'game_name', 'description',
            'amount', 'proof_image', 'proof_image_url', 'entry_image_url',
            'completion_image_url', 'result_image_urls',
            'status', 'status_display', 'commission_rate', 'payout_amount',
            'remark', 'audit_remark', 'created_at', 'audited_at',
        ]
        read_only_fields = [
            'status', 'commission_rate', 'payout_amount', 'audit_remark', 'audited_at',
        ]
        extra_kwargs = {'proof_image': {'write_only': True, 'required': False}}

    def get_proof_image_url(self, obj):
        if not obj.proof_image:
            return ''
        request = self.context.get('request')
        url = obj.proof_image.url
        return request.build_absolute_uri(url) if request else url

    def _file_url(self, field):
        if not field:
            return ''
        request = self.context.get('request')
        return request.build_absolute_uri(field.url) if request else field.url

    def get_entry_image_url(self, obj):
        return self._file_url(obj.entry_image)

    def get_completion_image_url(self, obj):
        return self._file_url(obj.completion_image)

    def get_result_image_urls(self, obj):
        return [self._file_url(item.image) for item in obj.result_images.all()]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('报单金额必须大于 0')
        return value


class WithdrawRequestSerializer(serializers.ModelSerializer):
    """C 端提现：陪玩提交与查询自己的提现申请。"""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payee_method_display = serializers.CharField(source='get_payee_method_display', read_only=True)

    class Meta:
        model = WithdrawRequest
        fields = [
            'id', 'amount', 'payee_method', 'payee_method_display', 'payee_account',
            'payee_name', 'status', 'status_display', 'remark', 'audit_remark',
            'payout_reference', 'paid_at', 'created_at', 'audited_at',
        ]
        read_only_fields = [
            'status', 'audit_remark', 'payout_reference', 'paid_at', 'audited_at',
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('提现金额必须大于 0')
        return value
