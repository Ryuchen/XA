from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    # 三端统一使用根路径；保留旧小程序地址，兼容已安装的历史版本。
    re_path(r'^(?:api/)?ws/orders/$', consumers.OrderConsumer.as_asgi()),
]
