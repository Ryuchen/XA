from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer

from common.media import build_media_url
from .models import ProviderReport, WithdrawRequest
from .settlement_display import ReportSettlementListSerializer


class ReportSerializer(EntityNumberModelSerializer):
    """C 端报单：陪玩提交与查询自己的报单。"""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    amount = serializers.SerializerMethodField()
    commission_rate = serializers.SerializerMethodField()
    proof_image_url = serializers.SerializerMethodField()
    order_id = serializers.IntegerField(read_only=True)
    order_no = serializers.CharField(source='order.order_no', read_only=True, default='')
    order_entity_no = serializers.SerializerMethodField()

    def get_order_entity_no(self, obj):
        if not obj.order_id:
            return ''
        from identifiers.services import get_entity_number
        return get_entity_number(obj.order)

    def get_amount(self, obj):
        from orders.segment_financial_capture import transfer_income_pending
        from orders.points_service_payment import display_has_points
        if obj.order_id and display_has_points(obj.order):
            from .reporting import points_report_snapshot
            return obj.amount if points_report_snapshot(obj) else None
        return None if obj.order_id and transfer_income_pending(obj.order, obj.provider_account_id) else obj.amount

    def get_commission_rate(self, obj):
        from orders.segment_financial_capture import transfer_income_pending
        from orders.points_service_payment import display_has_points
        if obj.order_id and display_has_points(obj.order):
            from .reporting import points_report_snapshot
            return obj.commission_rate if points_report_snapshot(obj) else None
        return None if obj.order_id and transfer_income_pending(obj.order, obj.provider_account_id) else obj.commission_rate

    fulfillment = serializers.CharField(source='order.fulfillment', read_only=True, default='')
    service_name = serializers.CharField(source='order.service_name_snapshot', read_only=True, default='')
    entry_image_url = serializers.SerializerMethodField()
    completion_image_url = serializers.SerializerMethodField()
    result_image_urls = serializers.SerializerMethodField()
    result_images = serializers.SerializerMethodField()
    order_status = serializers.CharField(source='order.status', read_only=True, default='')
    payment_status = serializers.CharField(source='order.payment_status', read_only=True, default='')
    aftersale_status = serializers.SerializerMethodField()
    refund_summary = serializers.SerializerMethodField()
    settled_at = serializers.SerializerMethodField()
    settlement_status = serializers.SerializerMethodField()
    negotiated_settlement = serializers.SerializerMethodField()
    expected_income = serializers.SerializerMethodField()
    platform_funding_insufficient = serializers.SerializerMethodField()
    payout_amount = serializers.SerializerMethodField()
    commission_type = serializers.SerializerMethodField()
    commission_fixed = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_submit = serializers.SerializerMethodField()
    can_withdraw = serializers.SerializerMethodField()
    edit_block_reason = serializers.SerializerMethodField()
    evidence_upload_block_reasons = serializers.SerializerMethodField()

    class Meta:
        model = ProviderReport
        list_serializer_class = ReportSettlementListSerializer
        fields = [
            'id', 'order_id', 'order_no', 'order_entity_no', 'service_name', 'fulfillment', 'game_name', 'description',
            'amount', 'proof_image', 'proof_image_url', 'entry_image_url',
            'completion_image_url', 'result_image_urls', 'result_images',
            'status', 'status_display', 'commission_rate', 'payout_amount',
            'remark', 'audit_remark', 'created_at', 'audited_at', 'submitted_at',
            'order_status', 'payment_status', 'aftersale_status', 'refund_summary', 'settled_at', 'settlement_status', 'negotiated_settlement', 'expected_income', 'platform_funding_insufficient', 'commission_type', 'commission_fixed', 'can_edit', 'can_submit', 'can_withdraw', 'edit_block_reason', 'evidence_upload_block_reasons',
        ]
        read_only_fields = [
            'status', 'commission_rate', 'payout_amount', 'audit_remark', 'audited_at', 'submitted_at',
        ]
        extra_kwargs = {'proof_image': {'write_only': True, 'required': False}}

    def get_proof_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.proof_image)

    def get_settlement_status(self, obj):
        from .settlement_display import report_settlement_status
        return report_settlement_status(obj)

    def get_settled_at(self, obj):
        if not obj.order_id:
            return None
        from .reporting import report_subsidy_snapshot
        has_subsidy, amount, paid_at = report_subsidy_snapshot(obj)
        if has_subsidy:
            from .settlement_display import SETTLED, report_settlement_status
            return paid_at if amount is not None and report_settlement_status(obj) == SETTLED else None
        from orders.points_service_payment import display_has_points
        from orders.points_checkout_test import ordinary_settlement_order
        if display_has_points(obj.order) or ordinary_settlement_order(obj.order):
            from orders.points_settlement_test import verified_person_settlement_display
            return verified_person_settlement_display(obj)[1]
        from orders.negotiation_settlement import report_settlement_snapshot
        negotiated = report_settlement_snapshot(obj)
        return negotiated[1] if negotiated is not None else obj.order.settled_at

    def get_negotiated_settlement(self, obj):
        from orders.negotiation_settlement import report_income
        return report_income(obj, display_only=True)[0]

    def get_aftersale_status(self, obj):
        if not obj.order_id:
            return ''
        from orders.aftersale_scope import visible_status
        return visible_status(obj.order, obj.provider_account_id)

    def get_refund_summary(self,obj):
        from orders.refunds import refund_summary,authorized_refund
        account = getattr(self.context.get('request'),'account',None)
        return refund_summary(obj.order,account=account,detailed=authorized_refund(account)) if obj.order_id else None

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
        return self._financial_snapshot(obj)['expected_income']

    def get_platform_funding_insufficient(self, obj):
        from .reporting import report_platform_funding_insufficient
        return report_platform_funding_insufficient(obj, self.context)

    def get_payout_amount(self, obj):
        from .reporting import report_has_valid_order_share
        from orders.segment_financial_capture import transfer_income_pending
        from orders.points_service_payment import display_has_points
        if obj.order_id and display_has_points(obj.order):
            from .reporting import points_report_snapshot
            if obj.status != ProviderReport.Status.APPROVED or not points_report_snapshot(obj):
                return None
            if obj.order.refund_records.exists():
                from orders.negotiation_settlement import report_income
                negotiated, income = report_income(obj, display_only=True)
                return income if negotiated else None
            return obj.payout_amount
        if obj.order_id and transfer_income_pending(obj.order, obj.provider_account_id):
            return None
        from orders.negotiation_settlement import report_income
        negotiated, income = report_income(obj, display_only=True)
        if negotiated:
            return income if obj.status == ProviderReport.Status.APPROVED else None
        if not report_has_valid_order_share(obj):
            return None
        from .reporting import report_subsidy_snapshot
        has_subsidy, amount, _ = report_subsidy_snapshot(obj)
        if has_subsidy:
            return amount if obj.status == ProviderReport.Status.APPROVED else None
        return obj.payout_amount

    def _financial_snapshot(self, obj):
        from .reporting import report_financial_snapshot
        cache = getattr(self, '_report_financial_snapshots', None)
        if cache is None:
            cache = self._report_financial_snapshots = {}
        if obj.pk not in cache:
            cache[obj.pk] = report_financial_snapshot(obj)
        return cache[obj.pk]

    def get_commission_type(self, obj):
        return self._financial_snapshot(obj)['commission_type']

    def get_commission_fixed(self, obj):
        return self._financial_snapshot(obj)['commission_fixed']

    def get_edit_block_reason(self, obj):
        from orders.services import report_edit_error
        from orders.points_service_payment import display_has_points
        return report_edit_error(obj, points_evidence=display_has_points(obj.order) if obj.order_id else False)

    def get_can_edit(self, obj):
        return not self.get_edit_block_reason(obj)

    def get_can_submit(self, obj):
        return self.get_can_edit(obj) and (not obj.order_id or obj.order.status == 'COMPLETED')

    def get_can_withdraw(self, obj):
        return obj.status == ProviderReport.Status.PENDING

    def get_evidence_upload_block_reasons(self, obj):
        from orders.team_fulfillment import team_report_evidence_error
        return {kind: reason for kind in ('ENTRY', 'COMPLETION')
                if (reason := team_report_evidence_error(obj, kind))}

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('报单金额必须大于 0')
        return value


class WithdrawRequestSerializer(EntityNumberModelSerializer):
    """C 端提现：陪玩提交与查询自己的提现申请。"""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payee_method_display = serializers.CharField(source='get_payee_method_display', read_only=True)
    processing = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    release = serializers.SerializerMethodField()

    def get_processing(self, obj):
        return obj.status == WithdrawRequest.Status.PENDING and bool(obj.claimed_by_account_id)

    def get_can_cancel(self, obj):
        return obj.workflow_version == 2 and obj.status == WithdrawRequest.Status.PENDING

    def get_release(self, obj):
        from .models import WithdrawalRelease
        row = WithdrawalRelease.objects.filter(withdrawal=obj).first()
        if row is None:
            return None
        return {'amount': row.amount, 'actor_kind': row.actor_kind,
                'reason': row.reason, 'created_at': row.created_at}

    class Meta:
        model = WithdrawRequest
        fields = [
            'id', 'amount', 'tax_rate', 'tax_amount', 'actual_amount',
            'payee_method', 'payee_method_display', 'payee_account',
            'payee_name', 'status', 'status_display', 'remark', 'audit_remark',
            'payout_reference', 'paid_at', 'created_at', 'audited_at', 'processing', 'can_cancel', 'workflow_version', 'release',
        ]
        read_only_fields = [
            'tax_rate', 'tax_amount', 'actual_amount',
            'status', 'audit_remark', 'payout_reference', 'paid_at', 'audited_at',
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('提现金额必须大于 0')
        return value
