from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/users/', include('users.urls')),
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
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
