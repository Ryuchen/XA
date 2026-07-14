from django.urls import path

from .views import CouponClaimView, CouponListView, MyCouponListView

urlpatterns = [
    path('', CouponListView.as_view()),
    path('mine/', MyCouponListView.as_view()),
    path('<int:coupon_id>/claim/', CouponClaimView.as_view()),
]
