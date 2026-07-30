import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

django_asgi_app = get_asgi_application()

from orders.routing import websocket_urlpatterns
from club_accounts.channels_middleware import ClubAccountTokenAuthMiddleware

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': ClubAccountTokenAuthMiddleware(
        URLRouter(websocket_urlpatterns)
    ),
})
