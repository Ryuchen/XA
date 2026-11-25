from django.contrib import admin
from django.urls import path, include
from common.private_media import private_media, public_media, RenewPrivateMediaView
from common.health import readiness
from engagement.h5_public import PublicH5HTMLView
from stores.platform_guarantee import PlatformStoreGuaranteeEventView, PlatformStoreGuaranteeView
from stores.platform_closure import PlatformStoreClosureExpiryView, PlatformStoreClosureView
from stores.platform_closure_readiness import PlatformStoreClosureReadinessView
from stores.urls import closure_return_urlpatterns

urlpatterns = [
    *closure_return_urlpatterns,
    path('h5/pages/<uuid:token>/', PublicH5HTMLView.as_view()),
    path('api/admin/platform/store-management/<int:pk>/closure', PlatformStoreClosureView.as_view()),
    path('api/admin/platform/store-management/<int:pk>/closure/confirm-expiry', PlatformStoreClosureExpiryView.as_view()),
    path('api/admin/platform/store-management/<int:pk>/closure/settlement-readiness', PlatformStoreClosureReadinessView.as_view()),
    path('api/admin/platform/store-management/<int:pk>/guarantee', PlatformStoreGuaranteeView.as_view()),
    path('api/admin/platform/store-management/<int:pk>/guarantee/events', PlatformStoreGuaranteeEventView.as_view()),
    path('api/health/', readiness),
    path('api/stores/', include('stores.urls')),
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
