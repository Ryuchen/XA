from django.contrib import admin
from django.urls import path
from lottery import views

urlpatterns = [
    path('', views.index, name='index'),
    path('r/<slug:room_code>/', views.index, name='room'),
    path('api/catalog/', views.CatalogView.as_view()),
    path('api/login/', views.LoginView.as_view()),
    path('api/me/', views.MeView.as_view()),
    path('api/draw/', views.DrawView.as_view()),
    path('api/draw-batch/', views.DrawBatchView.as_view()),
    path('api/history/', views.HistoryView.as_view()),
    path('control/', views.control, name='control'),
    path('health/', views.health),
    path('admin/', admin.site.urls),
]
