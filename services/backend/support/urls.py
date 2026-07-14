from django.urls import path

from .views import ContactCardListView, ContactCardView

urlpatterns = [
    path('contact-card/', ContactCardView.as_view()),
    path('contact-cards/', ContactCardListView.as_view()),
]
