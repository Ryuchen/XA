from django.urls import path

from .views import ChatMessageStatusView, ChatReadView, ChatSendView, ChatSessionView, ChatUnreadView

urlpatterns = [
    path('session/', ChatSessionView.as_view()),
    path('unread/', ChatUnreadView.as_view()),
    path('send/', ChatSendView.as_view()),
    path('message-status/', ChatMessageStatusView.as_view()),
    path('read/', ChatReadView.as_view()),
]
