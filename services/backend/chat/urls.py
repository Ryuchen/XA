from django.urls import path

from .views import ChatReadView, ChatSendView, ChatSessionView

urlpatterns = [
    path('session/', ChatSessionView.as_view()),
    path('send/', ChatSendView.as_view()),
    path('read/', ChatReadView.as_view()),
]
