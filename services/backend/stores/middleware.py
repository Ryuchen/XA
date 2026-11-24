import json

from django.core import signing
from django.db import DatabaseError, InterfaceError
from django.http import JsonResponse

from .context import StoreNotFound, StoreUnavailable, resolve_store, store_context
from .hosts import host_store_slug, is_admin_host, request_store_slug


class StoreMiddleware:
    """Select a known store before sessions, auth and business database access."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_admin_host(request.get_host()):
            from .admin_entry import neutral_admin_request
            return neutral_admin_request(request, self.get_response)
        # Readiness and static files need Host validation, but no tenant route.
        if request.path.startswith('/static/') or request.path == '/api/health/':
            request.get_host()
            return self.get_response(request)
        try:
            slug = request_store_slug(request.get_host(), request.headers.get('X-Store-Slug'), request.GET.getlist('store'))
        except StoreNotFound as exc:
            from .media import legacy_public_media_redirect
            response = legacy_public_media_redirect(request)
            if response is not None:
                return response
            return JsonResponse({'code': 404, 'msg': str(exc)}, status=404)
        except (DatabaseError, InterfaceError):
            return JsonResponse({'code': 503, 'errorCode': 'STORE_DIRECTORY_UNAVAILABLE',
                                 'msg': '门店域名服务暂不可用，请稍后重试'}, status=503)
        except ValueError as exc:
            return JsonResponse({'code': 400, 'msg': str(exc)}, status=400)
        try:
            # Only QR login scenes carry a ticket-based routing hint. The H5
            # UUID is looked up with its polling secret in the host store.
            web_login = '/web-login/' in request.path
            h5_state = request.GET.get('state') if request.path.endswith('/h5-login/callback/') else None
            if web_login or h5_state:
                ticket = request.GET.get('ticket') if web_login else None
                if web_login and request.method == 'POST' and request.content_type == 'application/json':
                    try:
                        data = json.loads(request.body or b'{}')
                        if isinstance(data, dict):
                            ticket = data.get('ticket', ticket)
                    except (ValueError, UnicodeDecodeError):
                        pass  # DRF will return the ordinary malformed-body error.
                if h5_state:
                    claim = signing.loads(h5_state, salt='xa.wechat-h5.login.v1', max_age=600)
                    selected_slug = claim.get('store_slug')
                    if selected_slug:
                        if slug is not None and slug != selected_slug:
                            return JsonResponse({'code': 400, 'msg': '授权请求与当前门店不一致，请重新发起'}, status=400)
                        slug = selected_slug
                elif ticket:
                    from .media import decode_login_scene
                    from .models import Store
                    store_id, _ = decode_login_scene(ticket)
                    if store_id is not None:
                        if slug is not None and host_store_slug(request.get_host()) == slug:
                            selected = resolve_store(slug, allow_cached=True)
                            if selected.pk != store_id:
                                return JsonResponse({'code': 400, 'msg': '登录票据与当前门店不一致，请重新扫码'}, status=400)
                        else:
                            selected = Store.objects.filter(pk=store_id).first()
                        if selected is None:
                            raise StoreNotFound('门店不存在，请重新扫码')
                        if slug is not None and slug != selected.slug:
                            return JsonResponse({'code': 400, 'msg': '登录票据与当前门店不一致，请重新扫码'}, status=400)
                        slug = selected.slug
            # Offline routing is allowed only for the server-bound store host,
            # never merely because a query parameter/header named a store.
            if slug is None:
                raise StoreNotFound('缺少所属门店，请使用门店入口')
            store = resolve_store(slug, allow_cached=bool(host_store_slug(request.get_host())))
        except StoreNotFound as exc:
            return JsonResponse({'code': 404, 'msg': str(exc)}, status=404)
        except StoreUnavailable as exc:
            return JsonResponse({'code': 403, 'msg': str(exc)}, status=403)
        except (DatabaseError, InterfaceError):
            return JsonResponse({'code': 503, 'errorCode': 'STORE_DIRECTORY_UNAVAILABLE',
                                 'msg': '门店服务暂不可用，请稍后重试'}, status=503)
        except signing.BadSignature:
            return JsonResponse({'code': 400, 'msg': '微信授权请求已失效，请重新发起'}, status=400)
        except (ValueError, TypeError):
            return JsonResponse({'code': 400, 'msg': '登录票据格式不正确，请重新扫码'}, status=400)
        request.store = store
        with store_context(store):
            response = self.get_response(request)
            response['X-Store-Slug'] = store.slug
            return response
