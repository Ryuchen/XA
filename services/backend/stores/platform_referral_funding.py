"""Platform finance reserves a verified receipt for referral refunds."""
import json
from collections.abc import Mapping
from uuid import UUID
from django.db import DatabaseError, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.context import store_context
from stores.db import transaction as tenant_transaction
from .hosts import is_admin_host
from .models import (PlatformAuditEvent, PlatformPassRepaymentClaim, PlatformPassRepaymentOperation,
                     PlatformPointsFundingOperation, PlatformReferralFundingOperation,
                     PlatformReferralFundingReleaseOperation)
from .platform_auth import PlatformAuthentication, IsPlatformAuthenticated
from .platform_pass_repayment import RepaymentError, _claimed_receipt, _has_pending, _locked_finance, _sha
from .platform_points_funding import _verified_receipt


def reserve(token, slug, claim_id, data):
    if not isinstance(data, Mapping) or set(data) != {'request_id', 'order_id', 'amount', 'refund_amount'}:
        raise RepaymentError('推荐佣金补资参数不完整')
    try:
        request_id, claim_id = UUID(str(data['request_id'])), UUID(str(claim_id))
    except (ValueError, TypeError, AttributeError):
        raise RepaymentError('请求编号无效') from None
    order_id, amount, refund_amount = data['order_id'], data['amount'], data['refund_amount']
    if (type(order_id) is not int or order_id <= 0 or type(amount) is not int or not 0 < amount <= 2147483647
            or type(refund_amount) is not int or not 0 < refund_amount <= 2147483647):
        raise RepaymentError('订单或补资金额无效')
    digest = _sha(json.dumps([str(claim_id), order_id, amount, refund_amount], separators=(',', ':')))
    try:
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            claim = PlatformPassRepaymentClaim.objects.filter(pk=claim_id, store=store,
                tenant_receipt_id__isnull=False, voided_at__isnull=True).first()
            if not claim:
                raise RepaymentError('到账不存在或尚未完成', 404, 'REPAYMENT_NOT_FOUND')
            if (PlatformPassRepaymentClaim.objects.filter(request_id=request_id).exists()
                    or PlatformPassRepaymentOperation.objects.filter(request_id=request_id).exists()
                    or PlatformPointsFundingOperation.objects.filter(request_id=request_id).exists()
                    or PlatformReferralFundingReleaseOperation.objects.filter(request_id=request_id).exists()):
                raise RepaymentError('请求编号已用于其他操作', 409, 'REQUEST_CONFLICT')
            operation = PlatformReferralFundingOperation.objects.select_for_update().filter(request_id=request_id).first()
            if operation:
                if (operation.claim_id != claim.pk or operation.actor_id != actor.pk
                        or operation.payload_digest != digest or operation.order_id != order_id or operation.amount != amount
                        or operation.target_refund_amount != refund_amount):
                    raise RepaymentError('请求编号已用于其他操作', 409, 'REQUEST_CONFLICT')
                if operation.rejected_at:
                    raise RepaymentError(operation.rejection_message, 409, 'REFERRAL_FUNDING_REJECTED')
            else:
                with store_context(store):
                    from orders.referral_refund_funding import enabled
                    if not enabled():
                        raise RepaymentError('推荐佣金退款补资尚未开放', 503, 'REFERRAL_FUNDING_UNAVAILABLE')
                if _has_pending(store):
                    raise RepaymentError('本店有待确认的补资操作，请先核对', 409, 'REPAYMENT_BUSY')
                operation = PlatformReferralFundingOperation.objects.create(request_id=request_id, claim=claim,
                    actor=actor, order_id=order_id, amount=amount, target_refund_amount=refund_amount, payload_digest=digest)
                PlatformAuditEvent.objects.create(account=actor, action='referral_funding.requested',
                    target_store_slug=slug, target_type='referral_funding_operation', target_id=str(operation.pk))
            operation_id = operation.pk
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            operation = PlatformReferralFundingOperation.objects.select_for_update().select_related('claim').get(pk=operation_id)
            with store_context(store), tenant_transaction.atomic():
                from orders.referral_refund_funding import reserve_locked
                receipt = _claimed_receipt(operation.claim, slug)
                _verified_receipt(operation.claim, receipt)
                allocation, action = reserve_locked(order_id=order_id, receipt_id=receipt.pk, amount=amount,
                    refund_amount=refund_amount, resume=True,
                    request_key=f'referral:{operation.pk.hex}', actor=str(actor.pk))
                if operation.tenant_action_id is not None and operation.tenant_action_id != action.pk:
                    raise RepaymentError('原操作结果不一致，请核对', 409, 'REFERRAL_FUNDING_REVIEW')
            if operation.tenant_action_id is None:
                operation.tenant_action_id, operation.completed_at = action.pk, timezone.now()
                operation.save(update_fields=['tenant_action_id', 'completed_at'])
                PlatformAuditEvent.objects.create(account=actor, action='referral_funding.completed',
                    target_store_slug=slug, target_type='referral_funding_operation', target_id=str(operation.pk),
                    detail={'tenant_action_id': action.pk})
            return {'operation_id': str(operation.pk),
                    'status': 'RELEASED' if hasattr(allocation, 'release') else 'COMPLETED', **action.result}
    except ValidationError as exc:
        raise RepaymentError('补资结果或条件待核对，请使用原请求重试', 409, 'REFERRAL_FUNDING_REVIEW') from exc
    except DatabaseError as exc:
        raise RepaymentError('补资结果不明确，请使用原请求重试', 503, 'REFERRAL_FUNDING_UNCERTAIN') from exc


class PlatformReferralFundingView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, slug, claim_id):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        try:
            result = reserve(request.auth, slug, claim_id, request.data)
        except RepaymentError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        response = Response({'code': 0, 'data': result})
        response['Cache-Control'] = 'no-store'
        return response


class PlatformReferralFundingQuoteView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, slug, order_id):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        raw = request.query_params.get('refund_amount', '')
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit() or not 0 < int(raw) <= 2147483647:
            return Response({'code': 400, 'msg': '请填写有效的本次退款金额'}, status=400)
        try:
            with transaction.atomic(using='default'):
                _, store = _locked_finance(request.auth, slug)
                with store_context(store):
                    from orders.models import Order
                    from orders.referral_refund_funding import enabled, required_for_refund
                    from orders.refunds import RefundError
                    from orders.settlement import SettlementError
                    if not enabled():
                        raise RepaymentError('推荐佣金退款补资尚未开放', 503, 'REFERRAL_FUNDING_UNAVAILABLE')
                    order = Order.objects.filter(pk=order_id).first()
                    if not order:
                        raise RepaymentError('订单不存在或无权查看', 404, 'ORDER_NOT_FOUND')
                    try:
                        amount = required_for_refund(order, int(raw))
                    except (ValidationError, RefundError, SettlementError) as exc:
                        raise RepaymentError('订单佣金或退款事实待核对', 409, 'REFERRAL_FUNDING_REVIEW') from exc
            response = Response({'code': 0, 'data': {'order_id': order_id, 'refund_amount': int(raw),
                'amount': amount, 'requires_funding': amount > 0}})
            response['Cache-Control'] = 'no-store'
            return response
        except RepaymentError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return Response({'code': 503, 'msg': '补资试算暂不可用'}, status=503)


class PlatformReferralFundingVoidView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, slug, operation_id):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        reason = request.data.get('reason') if isinstance(request.data, Mapping) else None
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
            return Response({'code': 400, 'msg': '请填写不超过 200 字的核实原因'}, status=400)
        try:
            with transaction.atomic(using='default'):
                actor, store = _locked_finance(request.auth, slug)
                operation = (PlatformReferralFundingOperation.objects.select_for_update().select_related('claim')
                             .filter(pk=operation_id, claim__store=store).first())
                if not operation:
                    raise RepaymentError('操作不存在或无权查看', 404, 'REPAYMENT_NOT_FOUND')
                if operation.rejected_at:
                    if operation.rejection_message == reason.strip():
                        return Response({'code': 0, 'data': {'status': 'REJECTED'}})
                    raise RepaymentError('操作已处理，请刷新', 409, 'REFERRAL_FUNDING_REJECTED')
                if operation.tenant_action_id or operation.completed_at:
                    raise RepaymentError('补资已完成，不能取消', 409, 'REFERRAL_FUNDING_REVIEW')
                key = f'referral:{operation.pk.hex}'
                with store_context(store), tenant_transaction.atomic():
                    from orders.models import Order, ReferralRefundFunding, ReferralRefundFundingAction
                    from orders.financial_lock import lock_order_money
                    from users.receipt_usage_ledger import _locked_receipt
                    from users.receipt_usage_sources import verify_allocations_locked
                    from users.models import PassReceiptUsageEvent
                    order = Order.objects.select_for_update().filter(pk=operation.order_id).first()
                    if not order:
                        raise RepaymentError('订单不存在，须人工核对', 409, 'REFERRAL_FUNDING_REVIEW')
                    lock_order_money()
                    receipt = _claimed_receipt(operation.claim, slug)
                    _verified_receipt(operation.claim, receipt)
                    _locked_receipt(receipt.pk)
                    verify_allocations_locked(receipt.pk)
                    if (ReferralRefundFunding.objects.filter(request_key=key).exists()
                            or ReferralRefundFundingAction.objects.filter(request_key=key).exists()
                            or PassReceiptUsageEvent.objects.filter(state__receipt=receipt,
                                                                     request_key=f'referral:{key}').exists()):
                        raise RepaymentError('已存在门店补资凭据，请使用原请求恢复', 409, 'REFERRAL_FUNDING_REVIEW')
                operation.rejected_at, operation.rejection_message = timezone.now(), reason.strip()
                operation.save(update_fields=['rejected_at', 'rejection_message'])
                PlatformAuditEvent.objects.create(account=actor, action='referral_funding.pending_rejected',
                    target_store_slug=slug, target_type='referral_funding_operation', target_id=str(operation.pk),
                    detail={'reason': reason.strip(), 'original_actor_id': str(operation.actor_id)})
            return Response({'code': 0, 'data': {'status': 'REJECTED'}})
        except RepaymentError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except (DatabaseError, ValidationError):
            return Response({'code': 503, 'errorCode': 'REFERRAL_FUNDING_UNCERTAIN',
                             'msg': '核对暂不可用，请稍后重试'}, status=503)


class PlatformReferralFundingReleaseView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, slug, operation_id):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not isinstance(request.data, Mapping) or set(request.data) != {'request_id', 'reason'}:
            return Response({'code': 400, 'msg': '请填写请求编号和释放原因'}, status=400)
        try:
            request_id = UUID(str(request.data['request_id']))
            reason = request.data['reason']
            if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
                raise ValueError('invalid reason')
            reason = reason.strip()
        except (TypeError, ValueError, AttributeError):
            return Response({'code': 400, 'msg': '请求编号或释放原因无效'}, status=400)
        try:
            with transaction.atomic(using='default'):
                actor, store = _locked_finance(request.auth, slug)
                original = (PlatformReferralFundingOperation.objects.select_for_update().select_related('claim')
                            .filter(pk=operation_id, claim__store=store).first())
                if not original or not original.completed_at or not original.tenant_action_id or original.rejected_at:
                    raise RepaymentError('原补资未完成或无权查看', 404, 'REFERRAL_FUNDING_REVIEW')
                if (PlatformPassRepaymentClaim.objects.filter(request_id=request_id).exists()
                        or PlatformPassRepaymentOperation.objects.filter(request_id=request_id).exists()
                        or PlatformPointsFundingOperation.objects.filter(request_id=request_id).exists()
                        or PlatformReferralFundingOperation.objects.filter(request_id=request_id).exists()):
                    raise RepaymentError('请求编号已用于其他操作', 409, 'REQUEST_CONFLICT')
                op = PlatformReferralFundingReleaseOperation.objects.select_for_update().filter(request_id=request_id).first()
                if op:
                    if op.funding_operation_id != original.pk or op.actor_id != actor.pk or op.reason != reason:
                        raise RepaymentError('请求编号已用于其他操作', 409, 'REQUEST_CONFLICT')
                    if op.rejected_at:
                        raise RepaymentError('该释放请求已取消', 409, 'REFERRAL_RELEASE_REJECTED')
                else:
                    if _has_pending(store):
                        raise RepaymentError('本店有待确认的补资操作，请先核对', 409, 'REPAYMENT_BUSY')
                    if PlatformReferralFundingReleaseOperation.objects.filter(funding_operation=original,
                                                                               completed_at__isnull=False).exists():
                        raise RepaymentError('该补资已释放，请查看原操作', 409, 'REFERRAL_FUNDING_RELEASED')
                    op = PlatformReferralFundingReleaseOperation.objects.create(request_id=request_id,
                        funding_operation=original, actor=actor, reason=reason)
                    PlatformAuditEvent.objects.create(account=actor, action='referral_funding.release_requested',
                        target_store_slug=slug, target_type='referral_funding_release', target_id=str(op.pk))
                release_operation_id = op.pk
            with transaction.atomic(using='default'):
                actor, store = _locked_finance(request.auth, slug)
                op = (PlatformReferralFundingReleaseOperation.objects.select_for_update()
                      .select_related('funding_operation__claim').get(pk=release_operation_id))
                original = op.funding_operation
                if op.rejected_at:
                    raise RepaymentError('该释放请求已取消', 409, 'REFERRAL_RELEASE_REJECTED')
                with store_context(store), tenant_transaction.atomic():
                    from orders.models import ReferralRefundFundingAction
                    from orders.referral_refund_funding import release_locked
                    receipt = _claimed_receipt(original.claim, slug)
                    _verified_receipt(original.claim, receipt)
                    action = ReferralRefundFundingAction.objects.filter(pk=original.tenant_action_id,
                        request_key=f'referral:{original.pk.hex}').first()
                    if not action or action.allocation.receipt_id != receipt.pk:
                        raise RepaymentError('本店原补资凭据缺失', 409, 'REFERRAL_FUNDING_REVIEW')
                    release = release_locked(allocation_id=action.allocation_id,
                        request_key=f'referral-release:{op.pk.hex}', actor=str(actor.pk), reason=op.reason)
                    if op.tenant_release_id is not None and op.tenant_release_id != release.pk:
                        raise RepaymentError('原释放结果不一致', 409, 'REFERRAL_FUNDING_REVIEW')
                if op.tenant_release_id is None:
                    op.tenant_release_id, op.completed_at = release.pk, timezone.now()
                    op.save(update_fields=['tenant_release_id', 'completed_at'])
                    PlatformAuditEvent.objects.create(account=actor, action='referral_funding.release_completed',
                        target_store_slug=slug, target_type='referral_funding_release', target_id=str(op.pk),
                        detail={'tenant_release_id': release.pk, 'reason': op.reason})
            response = Response({'code': 0, 'data': {'operation_id': str(op.pk), 'status': 'COMPLETED', **release.result}})
            response['Cache-Control'] = 'no-store'
            return response
        except RepaymentError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except (DatabaseError, ValidationError):
            return Response({'code': 503, 'errorCode': 'REFERRAL_FUNDING_UNCERTAIN',
                             'msg': '释放结果待核对，请使用原请求重试'}, status=503)


class PlatformReferralFundingReleaseVoidView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, slug, release_operation_id):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        reason = request.data.get('reason') if isinstance(request.data, Mapping) else None
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
            return Response({'code': 400, 'msg': '请填写不超过 200 字的核实原因'}, status=400)
        reason = reason.strip()
        try:
            with transaction.atomic(using='default'):
                actor, store = _locked_finance(request.auth, slug)
                op = (PlatformReferralFundingReleaseOperation.objects.select_for_update()
                      .select_related('funding_operation__claim')
                      .filter(pk=release_operation_id, funding_operation__claim__store=store).first())
                if not op:
                    raise RepaymentError('释放请求不存在', 404, 'REPAYMENT_NOT_FOUND')
                if op.rejected_at:
                    if op.rejection_reason == reason:
                        return Response({'code': 0, 'data': {'status': 'REJECTED'}})
                    raise RepaymentError('释放请求已处理', 409, 'REFERRAL_RELEASE_REJECTED')
                if op.tenant_release_id or op.completed_at:
                    raise RepaymentError('释放已完成，不能取消', 409, 'REFERRAL_FUNDING_REVIEW')
                original = op.funding_operation
                with store_context(store), tenant_transaction.atomic():
                    from orders.models import Order, ReferralRefundFundingAction, ReferralRefundFundingRelease
                    from orders.financial_lock import lock_order_money
                    from orders.referral_refund_funding import prelock
                    from users.models import PassReceiptUsageEvent
                    order = Order.objects.select_for_update().filter(pk=original.order_id).first()
                    if not order:
                        raise RepaymentError('原订单不存在，须人工核对', 409, 'REFERRAL_FUNDING_REVIEW')
                    lock_order_money()
                    prelock(order)
                    action = ReferralRefundFundingAction.objects.filter(pk=original.tenant_action_id,
                        request_key=f'referral:{original.pk.hex}', allocation__order=order).first()
                    if not action:
                        raise RepaymentError('原补资凭据缺失', 409, 'REFERRAL_FUNDING_REVIEW')
                    key = f'referral-release:{op.pk.hex}'
                    if (ReferralRefundFundingRelease.objects.filter(allocation=action.allocation).exists()
                            or PassReceiptUsageEvent.objects.filter(state__receipt_id=action.allocation.receipt_id,
                                request_key=f'referral-release:{key}').exists()):
                        raise RepaymentError('已有本店释放凭据，请用原请求恢复', 409, 'REFERRAL_FUNDING_REVIEW')
                op.rejected_at, op.rejection_reason = timezone.now(), reason
                op.save(update_fields=['rejected_at', 'rejection_reason'])
                PlatformAuditEvent.objects.create(account=actor, action='referral_funding.release_pending_rejected',
                    target_store_slug=slug, target_type='referral_funding_release', target_id=str(op.pk),
                    detail={'reason': reason, 'original_actor_id': str(op.actor_id)})
            return Response({'code': 0, 'data': {'status': 'REJECTED'}})
        except RepaymentError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except (DatabaseError, ValidationError):
            return Response({'code': 503, 'errorCode': 'REFERRAL_FUNDING_UNCERTAIN', 'msg': '核对暂不可用'}, status=503)


class PlatformReferralFundingRequestStatusView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, slug):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        try:
            request_id = UUID(str(request.query_params.get('request_id', '')))
            with transaction.atomic(using='default'):
                _, store = _locked_finance(request.auth, slug)
                funding = PlatformReferralFundingOperation.objects.filter(request_id=request_id, claim__store=store).first()
                release = PlatformReferralFundingReleaseOperation.objects.filter(
                    request_id=request_id, funding_operation__claim__store=store).first()
                if bool(funding) == bool(release):
                    raise RepaymentError('操作不存在或凭据重复', 404 if not funding else 409, 'REFERRAL_FUNDING_REVIEW')
                op = funding or release
                status = 'REJECTED' if op.rejected_at else 'COMPLETED' if op.completed_at else 'PENDING'
                if funding and status == 'COMPLETED' and PlatformReferralFundingReleaseOperation.objects.filter(
                        funding_operation=funding, completed_at__isnull=False).exists():
                    status = 'RELEASED'
                data = {'operation_id': str(op.pk), 'kind': 'ALLOCATION' if funding else 'RELEASE', 'status': status}
            response = Response({'code': 0, 'data': data})
            response['Cache-Control'] = 'no-store'
            return response
        except (ValueError, TypeError, AttributeError):
            return Response({'code': 400, 'msg': '请求编号无效'}, status=400)
        except RepaymentError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return Response({'code': 503, 'msg': '操作状态暂不可用'}, status=503)
