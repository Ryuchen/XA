from django.urls import path

from .views import AssignmentAlertCursorView, MessageDetailView, MessageListView, MessageReadAllView, MessageUnreadCountView

urlpatterns = [
    path('', MessageListView.as_view()),
    path('unread-count/', MessageUnreadCountView.as_view()),
    path('assignment-alerts/', AssignmentAlertCursorView.as_view()),
    path('read-all/', MessageReadAllView.as_view()),
    path('<int:message_id>/', MessageDetailView.as_view()),
]
