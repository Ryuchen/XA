"""Platform guarantee management and store-host public guarantee projection."""
from __future__ import annotations

import uuid
from collections.abc import Mapping

from django.db import DatabaseError, InterfaceError
from django.utils.dateparse import parse_datetime
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .context import current_store
from .guarantee import (
    GuaranteeError, configure_contract, event_summary, public_status,
    read_platform_summary, record_event,
)
from .hosts import is_admin_host
from .models import Store
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_permissions import platform_may


def _response(payload, status=200):
    response = Response(payload, status=status)
    response['Cache-Control'] = 'no-store'
    response['Pragma'] = 'no-cache'
    return response


def _error(error):
    return _response(
        {'code': error.status, 'errorCode': error.code, 'msg': str(error)}, error.status,
    )


def _unavailable():
    return _response({
        'code': 503, 'errorCode': 'GUARANTEE_UNAVAILABLE', 'msg': '保证金服务暂不可用，请稍后重试',
    }, 503)


def _uuid(value, label):
    if value is None:
        return None
    try:
        return uuid.UUID(value) if isinstance(value, str) else value
    except (ValueError, TypeError, AttributeError) as exc:
        raise GuaranteeError(f'{label}无效') from exc


class PlatformGuaranteeBase(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def authorized_store(self, request, pk, action):
        if not is_admin_host(request.get_host()):
            raise GuaranteeError('需要平台管理入口', 403, 'PLATFORM_FORBIDDEN')
        store = Store.objects.filter(pk=pk).first()
        if store is None:
            raise GuaranteeError('门店不存在', 404, 'STORE_NOT_FOUND')
        if not platform_may(request.platform_account, action, store_slug=store.slug):
            raise GuaranteeError('缺少该门店的保证金平台权限', 403, 'PLATFORM_FORBIDDEN')
        return store


class PlatformStoreGuaranteeView(PlatformGuaranteeBase):
    def get(self, request, pk):
        try:
            store = self.authorized_store(request, pk, 'guarantee:view')
            return _response({'code': 0, 'data': read_platform_summary(store)})
        except GuaranteeError as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable()

    def put(self, request, pk):
        try:
            store = self.authorized_store(request, pk, 'guarantee:configure')
            required = {'amount_due_fen', 'expected_version', 'reason'}
            allowed = required | {'source_license_contract_id'}
            if not isinstance(request.data, Mapping) or not required <= set(request.data) or set(request.data) - allowed:
                raise GuaranteeError('请填写金额、版本和变更原因')
            data = configure_contract(
                request.platform_account, store_id=store.pk,
                amount_due_fen=request.data['amount_due_fen'],
                expected_version=request.data['expected_version'], reason=request.data['reason'],
                source_license_contract_id=_uuid(
                    request.data.get('source_license_contract_id'), '来源 License 合同',
                ),
            )
            return _response({'code': 0, 'msg': '保证金合同应收已登记', 'data': data})
        except GuaranteeError as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable()


class PlatformStoreGuaranteeEventView(PlatformGuaranteeBase):
    def post(self, request, pk):
        try:
            store = self.authorized_store(request, pk, 'guarantee:record')
            required = {
                'business_key', 'client_request_id', 'direction', 'amount_fen',
                'occurred_at', 'evidence_sha256', 'evidence_ref',
            }
            if (not isinstance(request.data, Mapping) or not required <= set(request.data)
                    or set(request.data) - (required | {'compensates_event_id'})):
                raise GuaranteeError('请填写完整的线下收退凭证信息')
            occurred_at = parse_datetime(request.data['occurred_at']) if isinstance(
                request.data.get('occurred_at'), str,
            ) else None
            event, created = record_event(
                request.platform_account, store_id=store.pk,
                business_key=request.data['business_key'],
                client_request_id=request.data['client_request_id'],
                direction=request.data['direction'], amount_fen=request.data['amount_fen'],
                occurred_at=occurred_at, evidence_sha256=request.data['evidence_sha256'],
                evidence_ref=request.data['evidence_ref'],
                compensates_event_id=_uuid(request.data.get('compensates_event_id'), '补偿原事件'),
            )
            return _response({
                'code': 0, 'msg': '线下凭证已登记；未发起或确认任何支付渠道结果',
                'data': {**event_summary(event), 'replayed': not created},
            }, 201 if created else 200)
        except GuaranteeError as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable()


class PublicStoreGuaranteeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        store = current_store()
        if store is None:
            return _response({'code': 404, 'errorCode': 'STORE_NOT_FOUND', 'msg': '门店不存在'}, 404)
        try:
            return _response({'code': 0, 'data': public_status(store)})
        except (DatabaseError, InterfaceError):
            return _unavailable()
