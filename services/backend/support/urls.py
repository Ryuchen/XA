from django.urls import path

from .views import ContactCardListView, ContactCardView, PublicContactCardView

urlpatterns = [
    path('public-contact/', PublicContactCardView.as_view()),
    path('contact-card/', ContactCardView.as_view()),
    path('contact-cards/', ContactCardListView.as_view()),
]
