"""Isolated test URL configuration; not included in production URLs."""
from django.urls import path
from .wechatpay_test_callback import isolated_callback

urlpatterns = [path('isolated/wechatpay/callback', isolated_callback)]
