from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    # 三端统一使用根路径。
    re_path(r'^ws/orders/$', consumers.OrderConsumer.as_asgi()),
]
