from django.urls import path
from .views import (MemberView, HomeFeedView, ContentView, PointsView, RewardsView,
    RedeemView, ClaimsView, LotteryView)
from .kook_views import KookBindingView

urlpatterns = [
    path('me/', MemberView.as_view()),
    path('feed/', HomeFeedView.as_view()),
    path('content/', ContentView.as_view()),
    path('content/<int:pk>/', ContentView.as_view()),
    path('points/', PointsView.as_view()),
    path('kook-binding/', KookBindingView.as_view()),
    path('rewards/', RewardsView.as_view()),
    path('redeem/', RedeemView.as_view()),
    path('claims/', ClaimsView.as_view()),
    path('claims/<int:pk>/', ClaimsView.as_view()),
    path('lottery/', LotteryView.as_view()),
]
