"""Shared web login session lookup for the official-account H5 flow."""
import uuid

from stores.context import current_store
from stores.media import decode_login_scene
from .web_login_models import WebLoginSession


def session_for(ticket, *, lock=False):
    from .views import WechatLoginError
    try:
        internal_ticket = isinstance(ticket, uuid.UUID)
        store_id, ticket_id = decode_login_scene(ticket)
        store = current_store()
        if store is None or (not internal_ticket and store_id != store.pk):
            raise ValueError('Login ticket store mismatch')
        query = WebLoginSession.objects.select_for_update() if lock else WebLoginSession.objects
        return query.get(ticket=ticket_id)
    except (ValueError, TypeError, AttributeError, WebLoginSession.DoesNotExist):
        raise WechatLoginError('登录请求无效，请在网页重新发起授权', 'WEB_LOGIN_INVALID', 400)
