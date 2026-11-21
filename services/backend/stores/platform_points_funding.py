"""Recoverable platform-finance allocation for enabled order funding paths."""
import json
from collections.abc import Mapping
from contextlib import contextmanager
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError as RestValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import OrderPointsFundingAction, OrderPointsFundingAllocation
from orders.points_checkout_test import (ordinary_production_enabled, require_isolated_checkout,
                                         require_order_funding_access)
from orders.points_funding_write import allocate, pending_handover_for_allocation_replay
from stores.context import store_context
from stores.db import transaction as tenant_transaction
from .hosts import is_admin_host
from .models import (PlatformAuditEvent, PlatformPassRepaymentClaim, PlatformPassRepaymentOperation,
                     PlatformPointsFundingOperation)
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_pass_repayment import (RepaymentError, _checked_action, _claimed_receipt,
                                      _has_pending, _locked_finance, _sha)


def _funding_available():
    return bool(getattr(settings, 'SERVICE_POINTS_FUNDING_TESTING', False) or ordinary_production_enabled()
        or (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True))


def _require_funding_environment():
    try:
        require_isolated_checkout()
    except RestValidationError:
        from django.conf import settings
        from stores.context import current_store
        from stores.db import transaction as tenant_transaction
        store = current_store()
        connection = tenant_transaction.get_connection()
        formal_points = bool(getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
            and store and store.database_alias == connection.alias and connection.vendor == 'mysql')
        if not (ordinary_production_enabled() or formal_points):
            raise


@contextmanager
def _addon_funding_scope(order_id, receipt_id, *, request_key=None):
    if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is not True:
        yield
        return
    from orders.models import Order, OrderOriginalShareTransfer
    from orders.points_service_payment import needs_integrated_processing
    # Transfers use the same row lock. Classify the order only after any
    # concurrent transfer has finished in this tenant.
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order or not needs_integrated_processing(order):
        yield
        return
    if receipt_id is None:
        from orders.financial_lock import lock_existing_order_money
        lock_existing_order_money()
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        from orders.points_transfer_refund_access import after_transfer
        pending_payment_id = pending_handover_for_allocation_replay(order, receipt_id, request_key)
        scope = after_transfer(order_id, phase='settlement', extra_receipt_id=receipt_id,
                               pending_payment_id=pending_payment_id)
    else:
        from orders.points_business_access import service_order
        scope = service_order(order_id, phase='funding', addon=order.addon_intents.exists(), extra_receipt_id=receipt_id)
    with scope:
        yield


def _require_order(order_id):
    from orders.models import Order
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise RepaymentError('订单不存在或无权查看', 404, 'ORDER_NOT_FOUND')
    try:
        require_order_funding_access(order)
    except RestValidationError as exc:
        raise RepaymentError('订单不在已开放的补资范围', 409, 'POINTS_FUNDING_REVIEW') from exc
    return order


def _checked_points_action(operation, receipt, order_id, amount, *, pending_followup_posting_id=None):
    key = f'points:{operation.pk.hex}'
    action = OrderPointsFundingAction.objects.select_related('allocation__redemption').filter(request_key=key).first()
    if not action:
        return None
    _require_order(order_id)
    return _checked_points_action_for_locked_order(operation, receipt, order_id, amount,
        pending_followup_posting_id=pending_followup_posting_id, action=action)


def _checked_points_action_for_locked_order(operation, receipt, order_id, amount, *,
                                            pending_followup_posting_id=None, action=None):
    # Caller must have locked and authorized the order in this tenant.
    key = f'points:{operation.pk.hex}'
    action = action or OrderPointsFundingAction.objects.select_related('allocation__redemption').filter(request_key=key).first()
    if not action:
        return None
    allocation = action.allocation
    expected = {'receipt_id': receipt.pk, 'allocation_id': allocation.pk, 'order_id': order_id, 'amount': amount}
    if receipt.usage_version is not None:
        if pending_followup_posting_id is not None:
            raise RepaymentError('账本尚未接通后续退款', 409, 'POINTS_FUNDING_REVIEW')
        from users.receipt_usage_guard import isolated_allocation_state_locked
        try:
            ledger = isolated_allocation_state_locked(receipt)
        except ValidationError as exc:
            raise RepaymentError('到账用途账本待核对', 409, 'POINTS_FUNDING_REVIEW') from exc
        pass_used, points_used = ledger.pass_units, (ledger.order_units + ledger.referral_units
            - ledger.referral_released_units - ledger.released_units)
    else:
        pass_used = receipt.allocations.aggregate(value=Sum('amount'))['value'] or 0
        from orders.points_funding_release import receipt_points_used_locked
        try:
            points_used = receipt_points_used_locked(receipt, pending_followup_posting_id=pending_followup_posting_id)
        except RestValidationError as exc:
            raise RepaymentError('到账补资释放凭证待核对', 409, 'POINTS_FUNDING_REVIEW') from exc
    if (action.actor != str(operation.actor_id) or allocation.request_key != key
            or allocation.allocated_by != str(operation.actor_id) or allocation.receipt_id != receipt.pk
            or allocation.funding_order_id != order_id or allocation.amount != amount or action.result != expected
            or pass_used + points_used > receipt.amount):
        raise RepaymentError('订单补资操作记录不一致，请先核对', 409, 'POINTS_FUNDING_REVIEW')
    return action


def _verified_receipt(claim, receipt):
    operations = list(PlatformPassRepaymentOperation.objects.filter(claim=claim, kind='VERIFY',
        tenant_action_id__isnull=False, completed_at__isnull=False, rejected_at__isnull=True, voided_at__isnull=True))
    for operation in operations:
        action = _checked_action(operation, receipt, 'VERIFY', None, None)
        if action and action.pk == operation.tenant_action_id:
            return
    raise RepaymentError('到账核实记录不完整，请先核对', 409, 'POINTS_FUNDING_REVIEW')


def _enqueue_settlement_after_commit(store_slug, order_id):
    """Nudge the existing store-local processor; its periodic scan is the fallback."""
    def enqueue():
        try:
            from engagement.tasks import process_funded_order
            # The finance response uses the committed receipt. Subscribing to
            # this task's result can block it while the result store reconnects.
            process_funded_order.apply_async(args=[order_id], headers={'store_slug': store_slug},
                                             retry=False, ignore_result=True)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                'Funded order %s in store %s awaits the periodic settlement scan', order_id, store_slug)
    # The tenant allocation may commit before the platform operation. Only
    # enqueue once the durable platform evidence has also committed.
    transaction.on_commit(enqueue, using='default')


def act(token, slug, claim_id, data):
    if not isinstance(data, Mapping) or set(data) != {'request_id', 'order_id', 'amount'}:
        raise RepaymentError('订单补资信息格式不正确')
    try:
        request_id, claim_id = UUID(str(data['request_id'])), UUID(str(claim_id))
    except (ValueError, TypeError, AttributeError):
        raise RepaymentError('请求编号无效') from None
    order_id, amount = data['order_id'], data['amount']
    if (type(order_id) is not int or not 0 < order_id <= 9223372036854775807
            or type(amount) is not int or not 0 < amount <= 2147483647):
        raise RepaymentError('订单编号或金额无效')
    digest = _sha(json.dumps([str(claim_id), order_id, amount], separators=(',', ':')))
    operation_id = None
    try:
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            with store_context(store):
                try:
                    _require_funding_environment()
                except RestValidationError:
                    raise RepaymentError('订单补资暂未开放', 503, 'POINTS_FUNDING_UNAVAILABLE') from None
            claim = PlatformPassRepaymentClaim.objects.filter(pk=claim_id, store=store, voided_at__isnull=True).first()
            if not claim or claim.tenant_receipt_id is None:
                raise RepaymentError('到账记录不存在或尚未完成', 404, 'REPAYMENT_NOT_FOUND')
            if PlatformPassRepaymentOperation.objects.filter(request_id=request_id).exists():
                raise RepaymentError('请求编号已用于其他操作', 409, 'REQUEST_CONFLICT')
            operation = PlatformPointsFundingOperation.objects.filter(request_id=request_id).first()
            if operation:
                if operation.claim_id != claim.pk or operation.actor_id != actor.pk or operation.payload_digest != digest:
                    raise RepaymentError('请求编号已用于其他操作', 409, 'REQUEST_CONFLICT')
                if operation.rejected_at:
                    raise RepaymentError(operation.rejection_message, 409, 'POINTS_FUNDING_REJECTED')
                if operation.voided_at:
                    raise RepaymentError('该操作已作废，请使用新请求', 409, 'POINTS_FUNDING_VOIDED')
            request_key = f'points:{operation.pk.hex}' if operation else None
            with store_context(store), tenant_transaction.atomic(), _addon_funding_scope(
                    order_id, claim.tenant_receipt_id, request_key=request_key):
                _require_order(order_id)
                receipt = _claimed_receipt(claim, slug)
                _verified_receipt(claim, receipt)
            if not operation:
                if _has_pending(store):
                    raise RepaymentError('本店有待确认的补款操作，请先核对', 409, 'REPAYMENT_BUSY')
                operation = PlatformPointsFundingOperation.objects.create(request_id=request_id, claim=claim,
                    actor=actor, payload_digest=digest)
                PlatformAuditEvent.objects.create(account=actor, action='points_funding.allocate_requested',
                    target_store_slug=slug, target_type='points_funding_operation', target_id=str(operation.pk))
            operation_id = operation.pk
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            operation = PlatformPointsFundingOperation.objects.select_for_update().select_related('claim').get(pk=operation_id)
            if operation.rejected_at:
                raise RepaymentError(operation.rejection_message, 409, 'POINTS_FUNDING_REJECTED')
            if operation.voided_at:
                raise RepaymentError('该操作已作废，请使用新请求', 409, 'POINTS_FUNDING_VOIDED')
            request_key = f'points:{operation.pk.hex}'
            with store_context(store), tenant_transaction.atomic(), _addon_funding_scope(
                    order_id, operation.claim.tenant_receipt_id, request_key=request_key):
                receipt = _claimed_receipt(operation.claim, slug)
                _verified_receipt(operation.claim, receipt)
                action = _checked_points_action(operation, receipt, order_id, amount)
                if operation.tenant_action_id is not None and (not action or action.pk != operation.tenant_action_id):
                    raise RepaymentError('订单补资结果缺失，请先核对', 409, 'POINTS_FUNDING_REVIEW')
                allocation = allocate(actor=str(actor.pk), request_key=request_key,
                                      receipt_id=receipt.pk, order_id=order_id, amount=amount)
                if not action:
                    result = {'receipt_id': receipt.pk, 'allocation_id': allocation.pk,
                              'order_id': order_id, 'amount': amount}
                    action = OrderPointsFundingAction.objects.create(request_key=request_key,
                        allocation=allocation, actor=str(actor.pk), result=result)
            if operation.tenant_action_id is None:
                operation.tenant_action_id, operation.completed_at = action.pk, timezone.now()
                operation.save(update_fields=['tenant_action_id', 'completed_at'])
                PlatformAuditEvent.objects.create(account=actor, action='points_funding.allocate_completed',
                    target_store_slug=slug, target_type='points_funding_operation', target_id=str(operation.pk),
                    detail={'tenant_action_id': action.pk})
            _enqueue_settlement_after_commit(store.slug, order_id)
            return {'operation_id': str(operation.pk), 'status': 'COMPLETED', **action.result}
    except ValidationError as error:
        message = str(error.messages[0])[:200] if error.messages else '订单补资条件不满足'
        if operation_id is None:
            raise RepaymentError(message, 400, 'POINTS_FUNDING_INVALID') from None
        try:
            with transaction.atomic(using='default'):
                actor, store = _locked_finance(token, slug)
                operation = PlatformPointsFundingOperation.objects.select_for_update().get(pk=operation_id)
                if operation.tenant_action_id is not None:
                    raise RepaymentError('操作结果待核对，请使用原请求重试', 409, 'POINTS_FUNDING_REVIEW')
                if operation.voided_at:
                    raise RepaymentError('该操作已作废，请使用新请求', 409, 'POINTS_FUNDING_VOIDED')
                if not operation.rejected_at:
                    operation.rejected_at, operation.rejection_message = timezone.now(), message
                    operation.save(update_fields=['rejected_at', 'rejection_message'])
                    PlatformAuditEvent.objects.create(account=actor, action='points_funding.allocate_rejected',
                        target_store_slug=slug, target_type='points_funding_operation', target_id=str(operation.pk))
        except DatabaseError:
            raise RepaymentError('操作结果不明确，请使用原请求重试', 503, 'POINTS_FUNDING_UNCERTAIN') from None
        raise RepaymentError(operation.rejection_message, 409, 'POINTS_FUNDING_REJECTED') from None
    except (DatabaseError, IntegrityError):
        raise RepaymentError('操作结果不明确，请使用原请求重试', 503, 'POINTS_FUNDING_UNCERTAIN') from None


def recover_or_void(token, slug, operation_id, *, reason=None):
    voiding = reason is not None
    if voiding and (not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200):
        raise RepaymentError('请填写不超过 200 字的核实及作废原因')
    reason = reason.strip() if voiding else None
    try:
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            with store_context(store):
                try:
                    _require_funding_environment()
                except RestValidationError:
                    raise RepaymentError('订单补资暂未开放', 503, 'POINTS_FUNDING_UNAVAILABLE') from None
            operation = (PlatformPointsFundingOperation.objects.select_for_update().select_related('claim')
                         .filter(pk=operation_id, claim__store=store).first())
            if not operation:
                raise RepaymentError('操作不存在或无权查看', 404, 'REPAYMENT_NOT_FOUND')
            if operation.voided_at:
                if voiding and operation.void_reason == reason:
                    return {'status': 'VOIDED', 'operation_id': str(operation.pk)}
                raise RepaymentError('该操作已作废', 409, 'POINTS_FUNDING_VOIDED')
            if operation.rejected_at:
                raise RepaymentError('该操作已拒绝，不能恢复或作废', 409, 'POINTS_FUNDING_REJECTED')
            if voiding and (operation.tenant_action_id is not None or operation.completed_at):
                raise RepaymentError('操作已有结果，不能作废', 409, 'POINTS_FUNDING_REVIEW')
            key = f'points:{operation.pk.hex}'
            with store_context(store), tenant_transaction.atomic():
                try:
                    _require_funding_environment()
                except RestValidationError:
                    raise RepaymentError('订单补资暂未开放', 503, 'POINTS_FUNDING_UNAVAILABLE') from None
                receipt = _claimed_receipt(operation.claim, slug)
                _verified_receipt(operation.claim, receipt)
                action = (OrderPointsFundingAction.objects.select_related('allocation__redemption')
                          .filter(request_key=key).first())
                allocation = OrderPointsFundingAllocation.objects.filter(request_key=key).first()
                if voiding:
                    if action or allocation:
                        raise RepaymentError('已找到门店补资记录，请先核对恢复', 409, 'RECOVERY_EVIDENCE_EXISTS')
                else:
                    if not action or not allocation:
                        raise RepaymentError('门店补资记录不完整，请先核对', 409, 'RECOVERY_EVIDENCE_MISSING')
                    order_id, amount = allocation.funding_order_id, allocation.amount
                    digest = _sha(json.dumps([str(operation.claim_id), order_id, amount], separators=(',', ':')))
                    with _addon_funding_scope(order_id, receipt.pk, request_key=key):
                        checked = _checked_points_action(operation, receipt, order_id, amount)
                        if (not checked or checked.pk != action.pk or digest != operation.payload_digest
                                or (operation.tenant_action_id is not None and operation.tenant_action_id != action.pk)):
                            raise RepaymentError('补资记录不一致，请人工核对', 409, 'POINTS_FUNDING_REVIEW')
                        try:
                            allocate(actor=str(operation.actor_id), request_key=key, receipt_id=receipt.pk,
                                     order_id=order_id, amount=amount)
                        except ValidationError as exc:
                            raise RepaymentError('订单补资凭证待核对', 409, 'POINTS_FUNDING_REVIEW') from exc
            if voiding:
                operation.voided_at, operation.voided_by, operation.void_reason = timezone.now(), actor, reason
                operation.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
                PlatformAuditEvent.objects.create(account=actor, action='points_funding.allocate_voided',
                    target_store_slug=slug, target_type='points_funding_operation', target_id=str(operation.pk),
                    detail={'reason': reason, 'original_actor_id': str(operation.actor_id)})
                return {'status': 'VOIDED', 'operation_id': str(operation.pk)}
            if operation.tenant_action_id is None:
                operation.tenant_action_id, operation.completed_at = action.pk, timezone.now()
                operation.save(update_fields=['tenant_action_id', 'completed_at'])
                PlatformAuditEvent.objects.create(account=actor, action='points_funding.allocate_recovered',
                    target_store_slug=slug, target_type='points_funding_operation', target_id=str(operation.pk),
                    detail={'tenant_action_id': action.pk, 'original_actor_id': str(operation.actor_id)})
            _enqueue_settlement_after_commit(store.slug, order_id)
            return {'status': 'COMPLETED', 'operation_id': str(operation.pk), **action.result}
    except DatabaseError:
        raise RepaymentError('核对结果暂不明确，请稍后重试', 503, 'POINTS_FUNDING_UNCERTAIN') from None


class PlatformPointsFundingRecoveryView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, slug, operation_id, action):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not _funding_available():
            return Response({'code': 503, 'msg': '订单补资暂未开放'}, status=503)
        if action == 'recover' and request.data in ({}, None):
            reason = None
        elif action == 'void' and isinstance(request.data, Mapping) and set(request.data) == {'reason'}:
            reason = request.data['reason']
            if reason is None:
                return Response({'code': 400, 'msg': '请填写核实及作废原因'}, status=400)
        else:
            return Response({'code': 400, 'msg': '核对请求格式不正确'}, status=400)
        try:
            result = recover_or_void(request.auth, slug, operation_id, reason=reason)
        except RepaymentError as error:
            return Response({'code': error.status, 'errorCode': error.code, 'msg': str(error)}, status=error.status)
        response = Response({'code': 0, 'data': result})
        response['Cache-Control'] = 'no-store'
        return response


class PlatformOrderFundingStatusView(APIView):
    """Expose verified order funding to platform finance in the selected store."""
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, slug, order_id):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not _funding_available():
            return Response({'code': 503, 'msg': '订单补资查询暂未开放'}, status=503)
        try:
            with transaction.atomic(using='default'):
                _, store = _locked_finance(request.auth, slug)
                with store_context(store):
                    try:
                        _require_funding_environment()
                    except RestValidationError:
                        raise RepaymentError('订单补资查询暂未开放', 503, 'POINTS_FUNDING_UNAVAILABLE') from None
                    from orders.models import Order
                    if not Order.objects.filter(pk=order_id).exists():
                        raise RepaymentError('订单不存在或无权查看', 404, 'ORDER_NOT_FOUND')
                    from orders.points_funding_evidence import verify_order_funding
                    from orders.points_funding_plan import PointsFundingPlanError
                    from orders.settlement import SettlementError
                    try:
                        with tenant_transaction.atomic(), _addon_funding_scope(order_id, None):
                            funding = verify_order_funding(order_id, allow_finalized=True,
                                                           allow_followup_recorded=True, include_prestart_cancelled=True)
                    except (PointsFundingPlanError, RestValidationError, SettlementError) as exc:
                        raise RepaymentError('订单补资记录待核对', 409, 'POINTS_FUNDING_REVIEW') from exc
        except RepaymentError as error:
            return Response({'code': error.status, 'errorCode': error.code, 'msg': str(error)}, status=error.status)
        except DatabaseError:
            return Response({'code': 503, 'errorCode': 'POINTS_FUNDING_UNCERTAIN',
                             'msg': '订单补资状态暂不可用，请稍后重试'}, status=503)
        response = Response({'code': 0, 'data': {'order_id': order_id, 'store_slug': slug,
            'required_units': funding.required, 'allocated_units': funding.allocated,
            'remaining_units': funding.remaining, 'fully_funded': funding.fully_funded}})
        response['Cache-Control'] = 'no-store'
        return response


class PlatformOrderFundingQueueView(APIView):
    """Scan unsettled service orders and return verified gaps or review items."""
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, slug):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not _funding_available():
            return Response({'code': 503, 'msg': '订单补资查询暂未开放'}, status=503)
        try:
            after = int(request.query_params.get('after_id', '0'))
            limit = int(request.query_params.get('limit', '20'))
            if not 0 <= after <= 9223372036854775807 or not 1 <= limit <= 50:
                raise ValueError
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '分页参数无效'}, status=400)
        try:
            with transaction.atomic(using='default'):
                _, store = _locked_finance(request.auth, slug)
                with store_context(store):
                    try:
                        _require_funding_environment()
                    except RestValidationError:
                        raise RepaymentError('订单补资查询暂未开放', 503, 'POINTS_FUNDING_UNAVAILABLE') from None
            from orders.models import Order
            from orders.points_funding_evidence import verify_order_funding
            from orders.points_funding_plan import PointsFundingPlanError
            from orders.settlement import SettlementError
            scan_limit = min(200, max(20, limit * 10))
            with store_context(store):
                candidates = list(Order.objects.filter(pk__gt=after, settled_at__isnull=True,
                    is_gift_snapshot=False, source_order__isnull=True, customer_account__isnull=False,
                    funding_terms__isnull=False, status__in=('GRABBED', 'IN_SERVICE', 'COMPLETED', 'CANCELLED'),
                    payment_status__in=('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED'))
                    .order_by('pk').values('pk', 'order_no', 'status', 'payment_status')[:scan_limit + 1])
                rows, scanned, cursor = [], 0, after
                for candidate in candidates[:scan_limit]:
                    cursor, scanned = candidate['pk'], scanned + 1
                    # A settlement may finish after the candidate list is read.
                    if not Order.objects.filter(pk=cursor, settled_at__isnull=True).exists():
                        continue
                    try:
                        with tenant_transaction.atomic(), _addon_funding_scope(cursor, None):
                            funding = verify_order_funding(cursor, allow_unstarted=True, include_prestart_cancelled=True)
                    except (PointsFundingPlanError, RestValidationError, SettlementError):
                        if not Order.objects.filter(pk=cursor, settled_at__isnull=True).exists():
                            continue
                        rows.append({'order_id': cursor, 'order_no': candidate['order_no'],
                            'order_status': candidate['status'], 'payment_status': candidate['payment_status'],
                            'funding_status': 'REVIEW', 'required_units': None, 'allocated_units': None,
                            'remaining_units': None})
                    else:
                        if funding.remaining:
                            rows.append({'order_id': cursor, 'order_no': candidate['order_no'],
                                'order_status': candidate['status'], 'payment_status': candidate['payment_status'],
                                'funding_status': 'NEEDS_FUNDING', 'required_units': funding.required,
                                'allocated_units': funding.allocated, 'remaining_units': funding.remaining})
                    if len(rows) >= limit:
                        break
                has_more = scanned < len(candidates)
            # Do not return a long scan after the finance grant or tenant
            # routing changed while individual orders were being verified.
            with transaction.atomic(using='default'):
                _, current = _locked_finance(request.auth, slug)
                if (current.pk, current.database_alias) != (store.pk, store.database_alias):
                    raise RepaymentError('门店路由已变化，请重新查询', 409, 'STORE_ROUTING_CHANGED')
        except RepaymentError as error:
            return Response({'code': error.status, 'errorCode': error.code, 'msg': str(error)}, status=error.status)
        except DatabaseError:
            return Response({'code': 503, 'errorCode': 'POINTS_FUNDING_UNCERTAIN',
                             'msg': '订单补资队列暂不可用，请稍后重试'}, status=503)
        response = Response({'code': 0, 'data': {'store_slug': slug, 'list': rows, 'scanned': scanned,
            'has_more': has_more, 'next_after_id': cursor if has_more else None}})
        response['Cache-Control'] = 'no-store'
        return response


class PlatformPointsFundingActionView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, slug, claim_id):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not _funding_available():
            return Response({'code': 503, 'msg': '订单补资暂未开放'}, status=503)
        try:
            result = act(request.auth, slug, claim_id, request.data)
        except RepaymentError as error:
            return Response({'code': error.status, 'errorCode': error.code, 'msg': str(error)}, status=error.status)
        response = Response({'code': 0, 'data': result})
        response['Cache-Control'] = 'no-store'
        return response
