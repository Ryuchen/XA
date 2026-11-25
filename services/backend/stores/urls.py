from django.urls import path

from .views import AvailableStoresView, CurrentStoreView, StoreDetailView, StoreLogoUploadView, StoreManageView, StoreResumeProvisionView, StoreSwitchView
from .platform_guarantee import PublicStoreGuaranteeView
from .platform_closure import PublicStoreClosureView
from .platform_closure_returns import (
    PlatformStoreClosureReturnDetailView, PlatformStoreClosureReturnPaymentView,
    PlatformStoreClosureReturnPrepareView, PlatformStoreClosureReturnReleaseView,
    PlatformStoreClosureReturnsView,
)

# Root URLconf includes these exact admin-origin routes. Keeping their complete
# contracts here avoids exposing any boss-store public return endpoint.
closure_return_urlpatterns = [
    path('api/admin/platform/store-management/<int:pk>/closure/returns',
         PlatformStoreClosureReturnsView.as_view()),
    path('api/admin/platform/store-management/<int:pk>/closure/returns/prepare',
         PlatformStoreClosureReturnPrepareView.as_view()),
    path('api/admin/platform/store-management/<int:pk>/closure/returns/<uuid:case_id>/record-payment',
         PlatformStoreClosureReturnPaymentView.as_view()),
    path('api/admin/platform/store-management/<int:pk>/closure/returns/<uuid:case_id>/release',
         PlatformStoreClosureReturnReleaseView.as_view()),
    path('api/admin/platform/store-management/<int:pk>/closure/returns/<uuid:case_id>',
         PlatformStoreClosureReturnDetailView.as_view()),
]

urlpatterns = [
    path('current/', CurrentStoreView.as_view()),
    path('guarantee/', PublicStoreGuaranteeView.as_view()),
    path('closure/', PublicStoreClosureView.as_view()),
    path('available/', AvailableStoresView.as_view()),
    path('switch/', StoreSwitchView.as_view()),
    path('manage/', StoreManageView.as_view()),
    path('manage/logo-upload/', StoreLogoUploadView.as_view()),
    path('manage/<int:pk>/initialize/', StoreResumeProvisionView.as_view()),
    path('manage/<int:pk>/', StoreDetailView.as_view()),
]
