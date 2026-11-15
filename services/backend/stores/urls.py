from django.urls import path

from .views import AvailableStoresView, CurrentStoreView, StoreDetailView, StoreLogoUploadView, StoreManageView, StoreResumeProvisionView, StoreSwitchView

urlpatterns = [
    path('current/', CurrentStoreView.as_view()),
    path('available/', AvailableStoresView.as_view()),
    path('switch/', StoreSwitchView.as_view()),
    path('manage/', StoreManageView.as_view()),
    path('manage/logo-upload/', StoreLogoUploadView.as_view()),
    path('manage/<int:pk>/initialize/', StoreResumeProvisionView.as_view()),
    path('manage/<int:pk>/', StoreDetailView.as_view()),
]
