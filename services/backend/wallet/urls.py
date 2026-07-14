from django.urls import path
from .views import (
    DepositView,
    IncomeRecordsView,
    ProviderBenefitRecordsView,
    ReportListCreateView,
    ReportImageUploadView,
    ReportSubmitView,
    TopupView,
    TransactionsView,
    WalletInfoView,
    WithdrawView,
)

urlpatterns = [
    path('info/', WalletInfoView.as_view()),
    path('topup/', TopupView.as_view()),
    path('income/', IncomeRecordsView.as_view()),
    path('provider-benefits/', ProviderBenefitRecordsView.as_view()),
    path('withdraw/', WithdrawView.as_view()),
    path('transactions/', TransactionsView.as_view()),
    path('reports/', ReportListCreateView.as_view()),
    path('reports/<int:pk>/images/', ReportImageUploadView.as_view()),
    path('reports/<int:pk>/submit/', ReportSubmitView.as_view()),
    path('deposit/', DepositView.as_view()),
]
