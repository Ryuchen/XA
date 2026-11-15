from django.urls import path

from .views import ChatMessageStatusView, ChatParticipationDetailView, ChatReadView, ChatSendView, ChatSessionView, ChatUnreadView

urlpatterns = [
    path('messages/<int:message_id>/participation/', ChatParticipationDetailView.as_view()),
    path('session/', ChatSessionView.as_view()),
    path('unread/', ChatUnreadView.as_view()),
    path('send/', ChatSendView.as_view()),
    path('message-status/', ChatMessageStatusView.as_view()),
    path('read/', ChatReadView.as_view()),
]
