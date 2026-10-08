from rest_framework import serializers

from common.media import build_media_url
from .models import ProviderReport, WithdrawRequest


class ReportSerializer(serializers.ModelSerializer):
    """C 端报单：陪玩提交与查询自己的报单。"""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    proof_image_url = serializers.SerializerMethodField()
    order_id = serializers.IntegerField(read_only=True)
    order_no = serializers.CharField(source='order.order_no', read_only=True, default='')
    fulfillment = serializers.CharField(source='order.fulfillment', read_only=True, default='')
    service_name = serializers.CharField(source='order.service_name_snapshot', read_only=True, default='')
    entry_image_url = serializers.SerializerMethodField()
    completion_image_url = serializers.SerializerMethodField()
    result_image_urls = serializers.SerializerMethodField()
    result_images = serializers.SerializerMethodField()
    order_status = serializers.CharField(source='order.status', read_only=True, default='')
    settled_at = serializers.DateTimeField(source='order.settled_at', read_only=True, default=None)
    expected_income = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_submit = serializers.SerializerMethodField()
    edit_block_reason = serializers.SerializerMethodField()

    class Meta:
        model = ProviderReport
        fields = [
            'id', 'order_id', 'order_no', 'service_name', 'fulfillment', 'game_name', 'description',
            'amount', 'proof_image', 'proof_image_url', 'entry_image_url',
            'completion_image_url', 'result_image_urls', 'result_images',
            'status', 'status_display', 'commission_rate', 'payout_amount',
            'remark', 'audit_remark', 'created_at', 'audited_at', 'submitted_at',
            'order_status', 'settled_at', 'expected_income', 'can_edit', 'can_submit', 'edit_block_reason',
        ]
        read_only_fields = [
            'status', 'commission_rate', 'payout_amount', 'audit_remark', 'audited_at', 'submitted_at',
        ]
        extra_kwargs = {'proof_image': {'write_only': True, 'required': False}}

    def get_proof_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.proof_image)

    def _file_url(self, field):
        return build_media_url(self.context.get('request'), field)

    def get_entry_image_url(self, obj):
        return self._file_url(obj.entry_image)

    def get_completion_image_url(self, obj):
        return self._file_url(obj.completion_image)

    def get_result_image_urls(self, obj):
        return [self._file_url(item.image) for item in obj.result_images.all()]

    def get_result_images(self, obj):
        return [{'id': item.pk, 'url': self._file_url(item.image)} for item in obj.result_images.all()]

    def get_expected_income(self, obj):
        if not obj.order_id:
            return obj.payout_amount
        share = obj.order.providers.filter(provider_id=obj.provider_id).first()
        return share.provider_income if share else obj.order.provider_income

    def get_edit_block_reason(self, obj):
        from orders.services import report_edit_error
        return report_edit_error(obj)

    def get_can_edit(self, obj):
        return not self.get_edit_block_reason(obj)

    def get_can_submit(self, obj):
        return self.get_can_edit(obj) and (not obj.order_id or obj.order.status == 'COMPLETED')

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
            'id', 'amount', 'tax_rate', 'tax_amount', 'actual_amount',
            'payee_method', 'payee_method_display', 'payee_account',
            'payee_name', 'status', 'status_display', 'remark', 'audit_remark',
            'payout_reference', 'paid_at', 'created_at', 'audited_at',
        ]
        read_only_fields = [
            'tax_rate', 'tax_amount', 'actual_amount',
            'status', 'audit_remark', 'payout_reference', 'paid_at', 'audited_at',
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('提现金额必须大于 0')
        return value
