"""Platform closure notice API and store-domain public announcement."""
from collections.abc import Mapping

from django.db import DatabaseError, InterfaceError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .closure import (
    ClosureError, confirm_expiry, event_summary, initiate_closure,
    public_announcement, read_platform_closure,
)
from .context import current_store
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
        'code': 503, 'errorCode': 'CLOSURE_UNAVAILABLE', 'msg': '闭店公示服务暂不可用，请稍后重试',
    }, 503)


class PlatformClosureBase(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def authorized_store(self, request, pk, action):
        if not is_admin_host(request.get_host()):
            raise ClosureError('需要平台管理入口', 403, 'PLATFORM_FORBIDDEN')
        store = Store.objects.filter(pk=pk).first()
        if store is None:
            raise ClosureError('门店不存在', 404, 'STORE_NOT_FOUND')
        if not platform_may(request.platform_account, action, store_slug=store.slug):
            raise ClosureError('缺少该门店的闭店平台权限', 403, 'PLATFORM_FORBIDDEN')
        return store


class PlatformStoreClosureView(PlatformClosureBase):
    def get(self, request, pk):
        try:
            store = self.authorized_store(request, pk, 'closure:view')
            return _response({'code': 0, 'data': read_platform_closure(store)})
        except ClosureError as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable()

    def post(self, request, pk):
        try:
            store = self.authorized_store(request, pk, 'closure:manage')
            required = {'reason_category', 'reason', 'client_request_id'}
            if not isinstance(request.data, Mapping) or set(request.data) != required:
                raise ClosureError('请填写闭店原因类别、原因和请求编号')
            notice, outcome = initiate_closure(
                request.platform_account, store_id=store.pk,
                reason_category=request.data['reason_category'], reason=request.data['reason'],
                client_request_id=request.data['client_request_id'],
            )
            return _response({
                'code': 0, 'msg': '闭店公示已登记，原倒计时保持不变',
                'data': {
                    'closure_id': str(notice.pk), 'started_at': notice.started_at.isoformat(),
                    'notice_policy_version': notice.notice_policy_version,
                    'notice_days': notice.notice_days,
                    'earliest_settlement_at': notice.earliest_settlement_at.isoformat(),
                    'outcome': outcome, 'replayed': outcome == 'REPLAY',
                    'already_started': outcome == 'EXISTING',
                },
            }, 201 if outcome == 'CREATED' else 200)
        except ClosureError as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable()


class PlatformStoreClosureExpiryView(PlatformClosureBase):
    def post(self, request, pk):
        try:
            store = self.authorized_store(request, pk, 'closure:manage')
            if not isinstance(request.data, Mapping) or set(request.data) != {'reason', 'client_request_id'}:
                raise ClosureError('请填写到期确认原因和请求编号')
            event, outcome = confirm_expiry(
                request.platform_account, store_id=store.pk, reason=request.data['reason'],
                client_request_id=request.data['client_request_id'],
            )
            return _response({
                'code': 0, 'msg': '公示到期已确认；门店仅进入待结清，未自动停用',
                'data': {
                    **event_summary(event), 'status': 'SETTLEMENT_PENDING',
                    'store_status': store.status, 'outcome': outcome,
                    'replayed': outcome == 'REPLAY', 'already_confirmed': outcome == 'EXISTING',
                },
            }, 201 if outcome == 'CREATED' else 200)
        except ClosureError as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable()


class PublicStoreClosureView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        store = current_store()
        if store is None:
            return _response({'code': 404, 'errorCode': 'STORE_NOT_FOUND', 'msg': '门店不存在'}, 404)
        try:
            return _response({'code': 0, 'data': public_announcement(store)})
        except (DatabaseError, InterfaceError):
            return _unavailable()
