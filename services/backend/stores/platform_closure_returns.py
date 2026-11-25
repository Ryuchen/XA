"""Platform API for SA02 per-account closure principal returns."""
from collections.abc import Mapping

from django.db import DatabaseError, InterfaceError
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response

from wallet.closure_return_audit import mark_pending, mark_recorded, publish_committed_event
from wallet.closure_returns import (
    ClosureReturnError, closure_return_detail, closure_return_list, prepare_return,
    record_payment, release_return,
)
from stores.context import store_context

from .closure import ClosureError, read_platform_closure
from .platform_closure import PlatformClosureBase


def _response(payload, status=200):
    response = Response(payload, status=status)
    response['Cache-Control'] = 'no-store'
    response['Pragma'] = 'no-cache'
    return response


def _error(error):
    return _response({'code': error.status, 'errorCode': error.code, 'msg': str(error)}, error.status)


def _unavailable(message='闭店返还结果暂不可核验，请使用原请求编号重试', code='CLOSURE_RETURN_UNAVAILABLE'):
    return _response({'code': 503, 'errorCode': code, 'msg': message}, 503)


def _require_settlement(store):
    state = read_platform_closure(store)['status']
    if state != 'SETTLEMENT_PENDING':
        raise ClosureReturnError('门店尚未进入待结清状态', 409, 'CLOSURE_NOT_SETTLEMENT_PENDING')


def _committed_response(store, result, created, event_id, *, created_status=200):
    """Tenant money is committed before this best-effort control audit phase."""
    try:
        audit, _created = publish_committed_event(store, event_id)
        mark_recorded(store, event_id, audit.pk)
    except (DatabaseError, InterfaceError):
        mark_pending(store, event_id)
        return _unavailable(code='CLOSURE_RETURN_AUDIT_UNAVAILABLE')
    except ValueError:
        mark_pending(store, event_id, 'AUDIT_REVIEW_REQUIRED')
        return _unavailable('店库结果已保留，平台审计待核对；请使用原请求编号恢复',
                            'CLOSURE_RETURN_AUDIT_REVIEW')
    return _response({'code': 0, 'data': result}, created_status if created else 200)


class PlatformStoreClosureReturnsView(PlatformClosureBase):
    def get(self, request, pk):
        try:
            store = self.authorized_store(request, pk, 'closure:view')
            _require_settlement(store)
            try:
                page = int(request.query_params.get('page', '1'))
                page_size = int(request.query_params.get('page_size', '20'))
                if not 1 <= page <= 100000 or not 1 <= page_size <= 100:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ClosureReturnError('分页参数无效') from None
            with store_context(store):
                data = closure_return_list(page=page, page_size=page_size)
            return _response({'code': 0, 'data': data})
        except (ClosureError, ClosureReturnError) as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable('闭店返还清单暂不可用', 'CLOSURE_RETURN_READ_UNAVAILABLE')

    def post(self, request, pk):
        return _response({'code': 405, 'errorCode': 'METHOD_NOT_ALLOWED', 'msg': '请使用指定的准备接口'}, 405)


class PlatformStoreClosureReturnPrepareView(PlatformClosureBase):
    def post(self, request, pk):
        try:
            store = self.authorized_store(request, pk, 'closure:manage')
            _require_settlement(store)
            data = dict(request.data) if isinstance(request.data, Mapping) else request.data
            result, created, event_id = prepare_return(request.platform_account, store, data)
            return _committed_response(store, result, created, event_id, created_status=201)
        except (ClosureError, ClosureReturnError) as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable()


class PlatformStoreClosureReturnDetailView(PlatformClosureBase):
    def get(self, request, pk, case_id):
        try:
            store = self.authorized_store(request, pk, 'closure:view')
            _require_settlement(store)
            with store_context(store):
                data = closure_return_detail(case_id)
            return _response({'code': 0, 'data': data})
        except (ClosureError, ClosureReturnError) as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable('闭店返还详情暂不可用', 'CLOSURE_RETURN_READ_UNAVAILABLE')


class PlatformStoreClosureReturnPaymentView(PlatformClosureBase):
    def post(self, request, pk, case_id):
        try:
            store = self.authorized_store(request, pk, 'closure:manage')
            _require_settlement(store)
            if not isinstance(request.data, Mapping):
                raise ClosureReturnError('请求格式无效')
            data = dict(request.data)
            if isinstance(data.get('occurred_at'), str):
                try:
                    data['occurred_at'] = parse_datetime(data['occurred_at'])
                except ValueError:
                    data['occurred_at'] = None
            result, created, event_id = record_payment(request.platform_account, store, case_id, data)
            return _committed_response(store, result, created, event_id)
        except (ClosureError, ClosureReturnError) as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable()


class PlatformStoreClosureReturnReleaseView(PlatformClosureBase):
    def post(self, request, pk, case_id):
        try:
            store = self.authorized_store(request, pk, 'closure:manage')
            _require_settlement(store)
            data = dict(request.data) if isinstance(request.data, Mapping) else request.data
            result, created, event_id = release_return(request.platform_account, store, case_id, data)
            return _committed_response(store, result, created, event_id)
        except (ClosureError, ClosureReturnError) as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _unavailable()
