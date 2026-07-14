from django.urls import path

from .views import AnnouncementDetailView, AnnouncementListView

urlpatterns = [
    path('', AnnouncementListView.as_view()),
    path('<int:pk>/', AnnouncementDetailView.as_view()),
]
