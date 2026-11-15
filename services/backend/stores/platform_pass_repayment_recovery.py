"""Recover directory outcomes only from matching, committed tenant evidence."""
import json

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from stores.context import store_context
from stores.db import transaction as tenant_transaction
from users.models import PassRefundRepaymentAction, PassRefundRepaymentAllocation, PassRefundRepaymentReceipt
from .hosts import is_admin_host
from .models import PlatformAuditEvent, PlatformPassRepaymentClaim, PlatformPassRepaymentOperation
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_pass_repayment import RepaymentError, _checked_action, _claimed_receipt, _locked_finance, _sha


def recover(token, slug, target_type, target_id):
    try:
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            if target_type == 'claim':
                claim = PlatformPassRepaymentClaim.objects.select_for_update().filter(pk=target_id, store=store).first()
                if not claim:
                    raise RepaymentError('登记记录不存在或无权查看', 404, 'REPAYMENT_NOT_FOUND')
                if claim.voided_at:
                    raise RepaymentError('该登记已作废，不能恢复', 409, 'REPAYMENT_VOIDED')
                with store_context(store), tenant_transaction.atomic():
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().filter(
                        request_key=f'platform:{claim.pk.hex}').first()
                    if not receipt:
                        raise RepaymentError('未找到已提交的门店到账记录，请原操作人员使用原请求重试', 409, 'RECOVERY_EVIDENCE_MISSING')
                    previous_id = claim.tenant_receipt_id
                    if previous_id is not None and previous_id != receipt.pk:
                        raise RepaymentError('到账记录不一致，请人工核对', 409, 'REPAYMENT_REVIEW')
                    # Compare every saved fact against the immutable directory
                    # fingerprint before changing the directory result.
                    claim.tenant_receipt_id = receipt.pk
                    _claimed_receipt(claim, slug, receipt)
                if previous_id is None:
                    claim.recorded_at = timezone.now()
                    claim.save(update_fields=['tenant_receipt_id', 'recorded_at'])
                    PlatformAuditEvent.objects.create(account=actor, action='pass_repayment.register_recovered',
                        target_store_slug=slug, target_type='pass_repayment_claim', target_id=str(claim.pk),
                        detail={'receipt_id': receipt.pk, 'original_actor_id': str(claim.actor_id)})
                return {'status': 'RECORDED', 'claim_id': str(claim.pk), 'receipt_id': receipt.pk}
            if target_type != 'operation':
                raise RepaymentError('恢复类型无效')
            operation = (PlatformPassRepaymentOperation.objects.select_for_update().select_related('claim')
                         .filter(pk=target_id, claim__store=store).first())
            if not operation:
                raise RepaymentError('操作不存在或无权查看', 404, 'REPAYMENT_NOT_FOUND')
            if operation.voided_at:
                raise RepaymentError('该操作已作废，不能恢复', 409, 'REPAYMENT_VOIDED')
            if operation.rejected_at:
                raise RepaymentError('该操作已拒绝，不能恢复为成功', 409, 'REPAYMENT_REJECTED')
            with store_context(store), tenant_transaction.atomic():
                receipt = _claimed_receipt(operation.claim, slug)
                action = (PassRefundRepaymentAction.objects.select_for_update().select_related('allocation')
                          .filter(request_key=f'platform:{operation.pk.hex}').first())
                if not action:
                    raise RepaymentError('未找到已提交的门店操作，请原操作人员使用原请求重试', 409, 'RECOVERY_EVIDENCE_MISSING')
                refund_id = action.allocation.refund_id if action.allocation_id else None
                amount = action.allocation.amount if action.allocation_id else None
                digest = _sha(json.dumps([str(operation.claim_id), operation.kind, refund_id, amount], separators=(',', ':')))
                _checked_action(operation, receipt, operation.kind, refund_id, amount, action)
                if (digest != operation.payload_digest
                        or (operation.tenant_action_id is not None and operation.tenant_action_id != action.pk)):
                    raise RepaymentError('操作记录不一致，请人工核对', 409, 'REPAYMENT_REVIEW')
            if operation.tenant_action_id is None:
                operation.tenant_action_id, operation.completed_at = action.pk, timezone.now()
                operation.save(update_fields=['tenant_action_id', 'completed_at'])
                PlatformAuditEvent.objects.create(account=actor, action=f'pass_repayment.{operation.kind.lower()}_recovered',
                    target_store_slug=slug, target_type='pass_repayment_operation', target_id=str(operation.pk),
                    detail={'tenant_action_id': action.pk, 'original_actor_id': str(operation.actor_id)})
            return {'status': 'COMPLETED', 'operation_id': str(operation.pk), **action.result}
    except DatabaseError:
        raise RepaymentError('核对结果暂不明确，请稍后重新核对', 503, 'REPAYMENT_RESULT_UNCERTAIN') from None


class PlatformPassRepaymentRecoveryView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, slug, target_type, target_id):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not getattr(settings, 'PLATFORM_PASS_REPAYMENT_ENABLED', False):
            return Response({'code': 503, 'msg': '补款核销暂未开放'}, status=503)
        if request.data not in ({}, None):
            return Response({'code': 400, 'msg': '核对请求不接受其他参数'}, status=400)
        try:
            result = recover(request.auth, slug, target_type, target_id)
        except RepaymentError as error:
            return Response({'code': error.status, 'errorCode': error.code, 'msg': str(error)}, status=error.status)
        response = Response({'code': 0, 'data': result})
        response['Cache-Control'] = 'no-store'
        return response


def void_pending(token, slug, target_type, target_id, reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
        raise RepaymentError('请填写不超过 200 字的核实及作废原因')
    reason = reason.strip()
    try:
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            if target_type == 'claim':
                claim = PlatformPassRepaymentClaim.objects.select_for_update().filter(pk=target_id, store=store).first()
                if not claim:
                    raise RepaymentError('登记记录不存在或无权查看', 404, 'REPAYMENT_NOT_FOUND')
                if claim.voided_at:
                    if claim.void_reason != reason:
                        raise RepaymentError('该登记已按其他原因作废，请查看审计', 409, 'REQUEST_CONFLICT')
                    return {'status': 'VOIDED', 'claim_id': str(claim.pk)}
                if claim.tenant_receipt_id is not None or claim.recorded_at:
                    raise RepaymentError('登记已有结果，不能作废', 409, 'REPAYMENT_REVIEW')
                if PlatformPassRepaymentOperation.objects.filter(claim=claim).exists():
                    raise RepaymentError('登记有关联操作，请人工核对', 409, 'REPAYMENT_REVIEW')
                with store_context(store), tenant_transaction.atomic():
                    if PassRefundRepaymentReceipt.objects.select_for_update().filter(request_key=f'platform:{claim.pk.hex}').exists():
                        raise RepaymentError('已找到门店到账记录，请先核对恢复', 409, 'RECOVERY_EVIDENCE_EXISTS')
                claim.voided_reference_digest, claim.external_reference_digest = claim.external_reference_digest, None
                claim.voided_at, claim.voided_by, claim.void_reason = timezone.now(), actor, reason
                claim.save(update_fields=['voided_reference_digest', 'external_reference_digest', 'voided_at', 'voided_by', 'void_reason'])
                PlatformAuditEvent.objects.create(account=actor, action='pass_repayment.register_voided',
                    target_store_slug=slug, target_type='pass_repayment_claim', target_id=str(claim.pk),
                    detail={'reason': reason, 'original_actor_id': str(claim.actor_id),
                            'reference_digest': claim.voided_reference_digest})
                return {'status': 'VOIDED', 'claim_id': str(claim.pk)}
            if target_type != 'operation':
                raise RepaymentError('作废类型无效')
            operation = (PlatformPassRepaymentOperation.objects.select_for_update().select_related('claim')
                         .filter(pk=target_id, claim__store=store).first())
            if not operation:
                raise RepaymentError('操作不存在或无权查看', 404, 'REPAYMENT_NOT_FOUND')
            if operation.voided_at:
                if operation.void_reason != reason:
                    raise RepaymentError('该操作已按其他原因作废，请查看审计', 409, 'REQUEST_CONFLICT')
                return {'status': 'VOIDED', 'operation_id': str(operation.pk)}
            if operation.tenant_action_id is not None or operation.completed_at or operation.rejected_at:
                raise RepaymentError('操作已有结果，不能作废', 409, 'REPAYMENT_REVIEW')
            key = f'platform:{operation.pk.hex}'
            with store_context(store), tenant_transaction.atomic():
                _claimed_receipt(operation.claim, slug)
                if (PassRefundRepaymentAction.objects.select_for_update().filter(request_key=key).exists()
                        or PassRefundRepaymentAllocation.objects.select_for_update().filter(request_key=key).exists()):
                    raise RepaymentError('已找到门店操作记录，请先核对恢复', 409, 'RECOVERY_EVIDENCE_EXISTS')
            operation.voided_at, operation.voided_by, operation.void_reason = timezone.now(), actor, reason
            operation.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
            PlatformAuditEvent.objects.create(account=actor, action=f'pass_repayment.{operation.kind.lower()}_voided',
                target_store_slug=slug, target_type='pass_repayment_operation', target_id=str(operation.pk),
                detail={'reason': reason, 'original_actor_id': str(operation.actor_id)})
            return {'status': 'VOIDED', 'operation_id': str(operation.pk)}
    except DatabaseError:
        raise RepaymentError('核对结果暂不明确，不能作废，请稍后重试', 503, 'REPAYMENT_RESULT_UNCERTAIN') from None


class PlatformPassRepaymentVoidView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, slug, target_type, target_id):
        from collections.abc import Mapping
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not getattr(settings, 'PLATFORM_PASS_REPAYMENT_ENABLED', False):
            return Response({'code': 503, 'msg': '补款核销暂未开放'}, status=503)
        if not isinstance(request.data, Mapping) or set(request.data) != {'reason'}:
            return Response({'code': 400, 'msg': '请填写核实及作废原因'}, status=400)
        try:
            result = void_pending(request.auth, slug, target_type, target_id, request.data['reason'])
        except RepaymentError as error:
            return Response({'code': error.status, 'errorCode': error.code, 'msg': str(error)}, status=error.status)
        response = Response({'code': 0, 'data': result})
        response['Cache-Control'] = 'no-store'
        return response
