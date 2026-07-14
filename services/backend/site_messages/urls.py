from django.urls import path

from .views import MessageDetailView, MessageListView, MessageReadAllView, MessageUnreadCountView

urlpatterns = [
    path('', MessageListView.as_view()),
    path('unread-count/', MessageUnreadCountView.as_view()),
    path('read-all/', MessageReadAllView.as_view()),
    path('<int:message_id>/', MessageDetailView.as_view()),
]
