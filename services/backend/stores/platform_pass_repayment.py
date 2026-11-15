"""Platform-wide receipt reservation and recoverable tenant registration."""
import hashlib
import json
import unicodedata
from collections.abc import Mapping
from datetime import datetime, timezone as utc_timezone
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connections, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from stores.context import store_context
from stores.db import transaction as tenant_transaction
from users.pass_repayment import allocate, register_receipt, verify_receipt
from users.models import (PassRefundRepaymentAction, PassRefundRepaymentAllocation,
                          PassRefundRepaymentReceipt, ProviderPassRefund)
from .hosts import is_admin_host
from .models import (PlatformAuditEvent, PlatformPassRepaymentClaim, PlatformPassRepaymentOperation,
                     PlatformPointsFundingOperation, Store)
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication, account_for_token
from .platform_permissions import platform_may

ACTION = 'pass_refund_repayment:register'


class RepaymentError(Exception):
    def __init__(self, message, status=400, code='REPAYMENT_INVALID'):
        super().__init__(message)
        self.status, self.code = status, code


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _parse(data):
    if not isinstance(data, Mapping) or set(data) != {'request_id', 'external_reference', 'amount', 'received_at', 'evidence'}:
        raise RepaymentError('到账信息格式不正确')
    try:
        request_id = UUID(str(data['request_id']))
    except (ValueError, TypeError, AttributeError):
        raise RepaymentError('请求编号无效') from None
    ref, amount, evidence = data['external_reference'], data['amount'], data['evidence']
    try:
        when = parse_datetime(data['received_at']) if isinstance(data['received_at'], str) else None
    except ValueError:
        when = None
    if (not isinstance(ref, str) or not ref.strip() or len(ref.strip()) > 128
            or type(amount) is not int or not 0 < amount <= 2147483647
            or not isinstance(evidence, str) or not evidence.strip() or len(evidence.strip()) > 4000
            or not isinstance(when, datetime) or timezone.is_naive(when)):
        raise RepaymentError('请填写有效金额、到账时间、流水号和凭证')
    return request_id, ref.strip(), amount, when.astimezone(utc_timezone.utc), evidence.strip()


def _locked_finance(token, slug):
    # Grant changes use this directory anchor; hold it through the tenant write
    # so that scope revocation cannot race a new registration.
    from .database_identity import lock_store_registration
    try:
        lock_store_registration()
    except ValueError as exc:
        raise RepaymentError('平台目录尚未初始化', 503, 'PLATFORM_UNAVAILABLE') from exc
    store = Store.objects.select_for_update().only('id', 'slug', 'database_alias', 'initialized_at').filter(slug=slug).first()
    try:
        actor = account_for_token(token)
    except TokenError:
        raise RepaymentError('平台登录已失效', 401, 'PLATFORM_SESSION_INVALID') from None
    if store is None or not platform_may(actor, ACTION, store_slug=slug):
        raise RepaymentError('门店不存在或没有登记权限', 404, 'STORE_NOT_FOUND')
    from .dynamic_database import discover_store_connection
    try:
        available = bool(store.initialized_at and discover_store_connection(store))
    except (ValueError, KeyError):
        available = False
    if not available:
        raise RepaymentError('门店数据暂不可用', 503, 'STORE_UNAVAILABLE')
    return actor, store


def _points_operation_table_exists():
    # Older deployments lack the points-operation table. Once present, errors
    # reading it must fail closed, not hide an unresolved shared receipt use.
    tables = connections['default'].introspection.table_names()
    if PlatformPointsFundingOperation._meta.db_table in tables:
        return True
    from django.db.migrations.recorder import MigrationRecorder
    if ('django_migrations' not in tables or MigrationRecorder.Migration.objects.using('default').filter(
            app='stores', name='0015_platformpointsfundingoperation').exists()):
        raise DatabaseError('积分补资操作表缺失，须先核对数据库')
    return False


def _has_pending(store):
    from .models import PlatformReferralFundingOperation, PlatformReferralFundingReleaseOperation
    if PlatformReferralFundingReleaseOperation.objects.filter(funding_operation__claim__store=store,
            tenant_release_id__isnull=True, rejected_at__isnull=True).exists():
        return True
    if PlatformReferralFundingOperation.objects.filter(claim__store=store, tenant_action_id__isnull=True,
                                                        rejected_at__isnull=True).exists():
        return True
    if (PlatformPassRepaymentClaim.objects.filter(store=store, tenant_receipt_id__isnull=True, voided_at__isnull=True).exists()
            or PlatformPassRepaymentOperation.objects.filter(claim__store=store, tenant_action_id__isnull=True,
                                                               rejected_at__isnull=True, voided_at__isnull=True).exists()):
        return True
    return (_points_operation_table_exists() and PlatformPointsFundingOperation.objects.filter(
        claim__store=store, tenant_action_id__isnull=True, rejected_at__isnull=True, voided_at__isnull=True).exists())


def register(token, slug, data):
    request_id, reference, amount, received_at, evidence = _parse(data)
    # Normalize purely for duplicate detection; original evidence stays in the
    # tenant database. A shared platform receiving account makes this global.
    ref_digest = _sha(unicodedata.normalize('NFKC', reference).casefold())
    content = json.dumps([slug, reference, amount, received_at.isoformat(), evidence], ensure_ascii=False, separators=(',', ':'))
    payload_digest = _sha(content)
    try:
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            claim = PlatformPassRepaymentClaim.objects.filter(request_id=request_id).first()
            if claim:
                if (claim.store_id != store.pk or claim.actor_id != actor.pk or claim.payload_digest != payload_digest):
                    raise RepaymentError('请求编号已用于其他操作', 409, 'REQUEST_CONFLICT')
                if claim.voided_at:
                    raise RepaymentError('该待确认登记已作废，请使用新请求登记', 409, 'REPAYMENT_VOIDED')
            else:
                if _has_pending(store):
                    raise RepaymentError('本店有待确认的补款操作，请先核对', 409, 'REPAYMENT_BUSY')
                if PlatformPassRepaymentClaim.objects.filter(external_reference_digest=ref_digest).exists():
                    raise RepaymentError('该到账流水已登记或正在核对', 409, 'RECEIPT_DUPLICATE')
                claim = PlatformPassRepaymentClaim.objects.create(request_id=request_id,
                    external_reference_digest=ref_digest, payload_digest=payload_digest, store=store, actor=actor)
                PlatformAuditEvent.objects.create(account=actor, action='pass_repayment.register_requested',
                    target_store_slug=slug, target_type='pass_repayment_claim', target_id=str(claim.pk))
            claim_id = claim.pk
        # If this database commit fails after the tenant commit, the claim and
        # deterministic tenant request key allow a same-payload retry.
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            claim = PlatformPassRepaymentClaim.objects.select_for_update().get(pk=claim_id)
            if claim.voided_at:
                raise RepaymentError('该待确认登记已作废，请使用新请求登记', 409, 'REPAYMENT_VOIDED')
            if claim.tenant_receipt_id is not None:
                with store_context(store):
                    existing = PassRefundRepaymentReceipt.objects.filter(pk=claim.tenant_receipt_id).first()
                    if not existing or (existing.request_key, existing.registered_by, existing.external_reference,
                            existing.amount, existing.received_at, existing.evidence) != (
                            f'platform:{claim.pk.hex}', str(actor.pk), reference, amount, received_at, evidence):
                        raise RepaymentError('到账记录不一致，请联系平台核对', 409, 'REPAYMENT_REVIEW')
                return {'claim_id': str(claim.pk), 'receipt_id': claim.tenant_receipt_id, 'status': 'RECORDED'}
            with store_context(store):
                receipt = register_receipt(actor=str(actor.pk), request_key=f'platform:{claim.pk.hex}',
                    external_reference=reference, amount=amount, received_at=received_at, evidence=evidence)
            claim.tenant_receipt_id, claim.recorded_at = receipt.pk, timezone.now()
            claim.save(update_fields=['tenant_receipt_id', 'recorded_at'])
            PlatformAuditEvent.objects.create(account=actor, action='pass_repayment.register_recorded',
                target_store_slug=slug, target_type='pass_repayment_claim', target_id=str(claim.pk),
                detail={'receipt_id': receipt.pk})
            return {'claim_id': str(claim.pk), 'receipt_id': receipt.pk, 'status': 'RECORDED'}
    except IntegrityError:
        # Includes a concurrent duplicate reservation; re-read with the same
        # request ID instead of guessing which transaction committed.
        raise RepaymentError('到账登记结果需核对，请使用原请求编号重试', 409, 'REPAYMENT_RETRY_SAME_REQUEST') from None
    except ValidationError:
        raise RepaymentError('到账记录存在冲突，请联系平台核对', 409, 'REPAYMENT_REVIEW') from None
    except DatabaseError:
        raise RepaymentError('登记结果暂不明确，请使用原请求编号重试', 503, 'REPAYMENT_RESULT_UNCERTAIN') from None


class PlatformPassRepaymentRegistrationView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, slug):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not getattr(settings, 'PLATFORM_PASS_REPAYMENT_ENABLED', False):
            return Response({'code': 503, 'msg': '到账登记暂未开放'}, status=503)
        try:
            result = register(request.auth, slug, request.data)
        except RepaymentError as error:
            return Response({'code': error.status, 'errorCode': error.code, 'msg': str(error)}, status=error.status)
        response = Response({'code': 0, 'data': result})
        response['Cache-Control'] = 'no-store'
        return response


def _claimed_receipt(claim, slug, receipt=None):
    if claim.tenant_receipt_id is None:
        raise RepaymentError('到账登记尚未完成，请先核对', 409, 'REPAYMENT_REVIEW')
    receipt = receipt or PassRefundRepaymentReceipt.objects.filter(pk=claim.tenant_receipt_id).first()
    if not receipt:
        raise RepaymentError('到账记录缺失，请先核对', 409, 'REPAYMENT_REVIEW')
    content = json.dumps([slug, receipt.external_reference, receipt.amount,
        receipt.received_at.astimezone(utc_timezone.utc).isoformat(), receipt.evidence],
        ensure_ascii=False, separators=(',', ':'))
    if (receipt.pk != claim.tenant_receipt_id or receipt.request_key != f'platform:{claim.pk.hex}' or receipt.registered_by != str(claim.actor_id)
            or _sha(content) != claim.payload_digest
            or _sha(unicodedata.normalize('NFKC', receipt.external_reference).casefold()) != claim.external_reference_digest):
        raise RepaymentError('到账记录不一致，请先核对', 409, 'REPAYMENT_REVIEW')
    return receipt


def _checked_action(operation, receipt, kind, refund_id, amount, action=None):
    action = action or PassRefundRepaymentAction.objects.filter(request_key=f'platform:{operation.pk.hex}').select_related('allocation').first()
    if not action:
        return None
    if (action.request_key != f'platform:{operation.pk.hex}' or action.receipt_id != receipt.pk
            or action.kind != kind or action.actor != str(operation.actor_id)
            or not isinstance(action.result, dict)):
        raise RepaymentError('操作记录不一致，请联系平台核对', 409, 'REPAYMENT_REVIEW')
    if kind == 'VERIFY':
        expected = {'receipt_id': receipt.pk, 'verified_by': receipt.verified_by,
                    'verified_at': receipt.verified_at.isoformat() if receipt.verified_at else None}
        valid = bool(receipt.verified_at) and action.result == expected and not action.allocation_id
        from django.conf import settings
        if getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True:
            if receipt.usage_version is None:
                raise RepaymentError('到账用途账本未建立，请联系平台核对', 409, 'REPAYMENT_REVIEW')
            from users.receipt_usage_guard import isolated_allocation_state_locked
            try:
                isolated_allocation_state_locked(receipt)
            except ValidationError as exc:
                raise RepaymentError('到账用途账本待核对', 409, 'REPAYMENT_REVIEW') from exc
    else:
        row = action.allocation
        expected = {'receipt_id': receipt.pk, 'allocation_id': row.pk if row else None,
                    'refund_id': refund_id, 'amount': amount}
        valid = bool(row and row.receipt_id == receipt.pk and row.refund_id == refund_id and row.amount == amount
                     and row.request_key == f'platform:{operation.pk.hex}' and row.allocated_by == str(operation.actor_id)
                     and action.result == expected)
        if receipt.usage_version is not None:
            from users.receipt_usage_guard import isolated_allocation_state_locked
            try:
                isolated_allocation_state_locked(receipt)
            except ValidationError as exc:
                raise RepaymentError('到账用途账本待核对', 409, 'REPAYMENT_REVIEW') from exc
    if not valid:
        raise RepaymentError('操作结果不一致，请联系平台核对', 409, 'REPAYMENT_REVIEW')
    return action


def act(token, slug, claim_id, kind, data):
    if kind not in ('VERIFY', 'ALLOCATE') or not isinstance(data, Mapping):
        raise RepaymentError('操作格式不正确')
    expected = {'request_id'} if kind == 'VERIFY' else {'request_id', 'refund_id', 'amount'}
    if set(data) != expected:
        raise RepaymentError('操作格式不正确')
    try:
        request_id, claim_id = UUID(str(data['request_id'])), UUID(str(claim_id))
    except (ValueError, TypeError, AttributeError):
        raise RepaymentError('请求编号无效') from None
    refund_id, amount = data.get('refund_id'), data.get('amount')
    if kind == 'ALLOCATE' and (type(refund_id) is not int or not 0 < refund_id <= 9223372036854775807
            or type(amount) is not int or not 0 < amount <= 2147483647):
        raise RepaymentError('退款编号或金额无效')
    digest = _sha(json.dumps([str(claim_id), kind, refund_id, amount], separators=(',', ':')))
    operation_id = None
    try:
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            claim = PlatformPassRepaymentClaim.objects.filter(pk=claim_id, store=store).first()
            if not claim or claim.tenant_receipt_id is None:
                raise RepaymentError('到账登记不存在或尚未完成', 404, 'REPAYMENT_NOT_FOUND')
            if (_points_operation_table_exists()
                    and PlatformPointsFundingOperation.objects.filter(request_id=request_id).exists()):
                raise RepaymentError('请求编号已用于其他操作', 409, 'REQUEST_CONFLICT')
            operation = PlatformPassRepaymentOperation.objects.filter(request_id=request_id).first()
            if operation:
                if (operation.claim_id != claim.pk or operation.actor_id != actor.pk
                        or operation.kind != kind or operation.payload_digest != digest):
                    raise RepaymentError('请求编号已用于其他操作', 409, 'REQUEST_CONFLICT')
                if operation.rejected_at:
                    raise RepaymentError(operation.rejection_message, 409, 'REPAYMENT_REJECTED')
                if operation.voided_at:
                    raise RepaymentError('该待确认操作已作废，请使用新请求', 409, 'REPAYMENT_VOIDED')
            else:
                if _has_pending(store):
                    raise RepaymentError('本店有待确认的补款操作，请先核对', 409, 'REPAYMENT_BUSY')
                operation = PlatformPassRepaymentOperation.objects.create(request_id=request_id, claim=claim,
                    actor=actor, kind=kind, payload_digest=digest)
                PlatformAuditEvent.objects.create(account=actor, action=f'pass_repayment.{kind.lower()}_requested',
                    target_store_slug=slug, target_type='pass_repayment_operation', target_id=str(operation.pk))
            operation_id = operation.pk
        with transaction.atomic(using='default'):
            actor, store = _locked_finance(token, slug)
            operation = PlatformPassRepaymentOperation.objects.select_for_update().select_related('claim').get(pk=operation_id)
            if operation.rejected_at:
                raise RepaymentError(operation.rejection_message, 409, 'REPAYMENT_REJECTED')
            if operation.voided_at:
                raise RepaymentError('该待确认操作已作废，请使用新请求', 409, 'REPAYMENT_VOIDED')
            with store_context(store), tenant_transaction.atomic():
                receipt = _claimed_receipt(operation.claim, slug)
                action = _checked_action(operation, receipt, kind, refund_id, amount)
                if operation.tenant_action_id is not None:
                    if not action or action.pk != operation.tenant_action_id:
                        raise RepaymentError('操作记录缺失，请联系平台核对', 409, 'REPAYMENT_REVIEW')
                elif not action:
                    if kind == 'VERIFY':
                        receipt = verify_receipt(actor=str(actor.pk), receipt_id=receipt.pk)
                        result = {'receipt_id': receipt.pk, 'verified_by': receipt.verified_by,
                                  'verified_at': receipt.verified_at.isoformat()}
                        allocation = None
                    else:
                        try:
                            allocation = allocate(actor=str(actor.pk), request_key=f'platform:{operation.pk.hex}',
                                receipt_id=receipt.pk, refund_id=refund_id, amount=amount)
                        except ProviderPassRefund.DoesNotExist:
                            raise ValidationError('退款记录不存在') from None
                        result = {'receipt_id': receipt.pk, 'allocation_id': allocation.pk,
                                  'refund_id': refund_id, 'amount': amount}
                    action = PassRefundRepaymentAction.objects.create(request_key=f'platform:{operation.pk.hex}',
                        kind=kind, receipt=receipt, allocation=allocation, actor=str(actor.pk), result=result)
            if operation.tenant_action_id is None:
                operation.tenant_action_id, operation.completed_at = action.pk, timezone.now()
                operation.save(update_fields=['tenant_action_id', 'completed_at'])
                PlatformAuditEvent.objects.create(account=actor, action=f'pass_repayment.{kind.lower()}_completed',
                    target_store_slug=slug, target_type='pass_repayment_operation', target_id=str(operation.pk),
                    detail={'tenant_action_id': action.pk})
            return {'operation_id': str(operation.pk), 'status': 'COMPLETED', **action.result}
    except ValidationError as error:
        message = str(error.messages[0])[:200] if error.messages else '操作不符合核销条件'
        try:
            with transaction.atomic(using='default'):
                actor, store = _locked_finance(token, slug)
                operation = PlatformPassRepaymentOperation.objects.select_for_update().get(pk=operation_id)
                if operation.tenant_action_id is not None:
                    raise RepaymentError('操作结果需核对，请使用原请求编号重试', 409, 'REPAYMENT_RESULT_UNCERTAIN')
                if operation.voided_at:
                    raise RepaymentError('该待确认操作已作废，请使用新请求', 409, 'REPAYMENT_VOIDED')
                if not operation.rejected_at:
                    operation.rejected_at, operation.rejection_message = timezone.now(), message
                    operation.save(update_fields=['rejected_at', 'rejection_message'])
                    PlatformAuditEvent.objects.create(account=actor, action=f'pass_repayment.{kind.lower()}_rejected',
                        target_store_slug=slug, target_type='pass_repayment_operation', target_id=str(operation.pk))
        except DatabaseError:
            raise RepaymentError('操作结果不明确，请使用原请求编号重试', 503, 'REPAYMENT_RESULT_UNCERTAIN') from None
        raise RepaymentError(operation.rejection_message, 409, 'REPAYMENT_REJECTED') from None
    except (IntegrityError, DatabaseError):
        raise RepaymentError('操作结果不明确，请使用原请求编号重试', 503, 'REPAYMENT_RESULT_UNCERTAIN') from None


class PlatformPassRepaymentActionView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, slug, claim_id, kind):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not getattr(settings, 'PLATFORM_PASS_REPAYMENT_ENABLED', False):
            return Response({'code': 503, 'msg': '补款核销暂未开放'}, status=503)
        try:
            result = act(request.auth, slug, claim_id, kind.upper(), request.data)
        except RepaymentError as error:
            return Response({'code': error.status, 'errorCode': error.code, 'msg': str(error)}, status=error.status)
        response = Response({'code': 0, 'data': result})
        response['Cache-Control'] = 'no-store'
        return response
