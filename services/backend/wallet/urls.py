from django.urls import path
from .payees import WithdrawalAccountListView, WithdrawalAccountDetailView
from .withdraw_cancellation import WithdrawCancelView
from .deposit_applications import ProviderDepositApplicationView, ProviderDepositApplicationCancelView
from .views import (
    DepositView,
    IncomeRecordsView,
    ProviderBenefitRecordsView,
    ReportListCreateView,
    ReportOrderChoicesView,
    ReportImageUploadView,
    ReportImageDeleteView,
    ReportSubmitView,
    ReportWithdrawView,
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
    path('withdraw/<int:pk>/cancel/', WithdrawCancelView.as_view()),
    path('withdraw-accounts/', WithdrawalAccountListView.as_view()),
    path('withdraw-accounts/<str:payee_method>/', WithdrawalAccountDetailView.as_view()),
    path('transactions/', TransactionsView.as_view()),
    path('reports/', ReportListCreateView.as_view()),
    path('report-order-choices/', ReportOrderChoicesView.as_view()),
    path('reports/<int:pk>/images/', ReportImageUploadView.as_view()),
    path('reports/<int:pk>/images/<int:image_id>/', ReportImageDeleteView.as_view()),
    path('reports/<int:pk>/submit/', ReportSubmitView.as_view()),
    path('reports/<int:pk>/withdraw/', ReportWithdrawView.as_view()),
    path('deposit/', DepositView.as_view()),
    path('deposit-applications/', ProviderDepositApplicationView.as_view()),
    path('deposit-applications/<int:pk>/cancel/', ProviderDepositApplicationCancelView.as_view()),
]
