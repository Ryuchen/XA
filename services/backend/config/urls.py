from django.contrib import admin
from django.urls import path, include
from common.private_media import private_media, public_media, RenewPrivateMediaView
from common.health import readiness

urlpatterns = [
    path('api/health/', readiness),
    path('api/media/renew/', RenewPrivateMediaView.as_view()),
    path('api/media/private/<str:ticket>/', private_media, name='private-media'),
    path('media/<path:path>', public_media, name='public-media'),
    path('admin/', admin.site.urls),
    path('api/users/', include('users.urls')),
    path('api/provider-registration/', include('users.registration_urls')),
    path('api/wallet/', include('wallet.urls')),
    path('api/orders/', include('orders.urls')),
    path('api/support/', include('support.urls')),
    path('api/messages/', include('site_messages.urls')),
    path('api/announcements/', include('announcements.urls')),
    path('api/banners/', include('banners.urls')),
    path('api/coupons/', include('coupons.urls')),
    path('api/chat/', include('chat.urls')),
    path('api/audition/', include('audition.urls')),
    path('api/admin/', include('console.urls')),
    path('api/club/', include('engagement.urls')),
]
