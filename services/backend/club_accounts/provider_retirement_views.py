"""Store staff preview and execute an explicit provider retirement."""
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from club_accounts.provider_lifecycle import ProviderLifecycleError
from club_accounts.provider_retirement import preview_retirement, retire_provider


class ProviderRetirementView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def _provider(self, account_id):
        return ClubAccount.objects.filter(pk=account_id, account_type=ClubAccount.AccountType.PROVIDER).first()

    def get(self, request, account_id):
        account = self._provider(account_id)
        if not account:
            return Response({'code': 404, 'msg': '陪玩账号不存在'}, status=404)
        return Response({'code': 0, 'data': preview_retirement(account, request.account)})

    def post(self, request, account_id):
        account = self._provider(account_id)
        if not account:
            return Response({'code': 404, 'msg': '陪玩账号不存在'}, status=404)
        data = request.data
        if not isinstance(data, dict) or set(data) != {'reason', 'confirmation_token', 'confirm', 'client_request_id'}:
            return Response({'code': 400, 'msg': '请填写注销原因、确认信息及操作编号'}, status=400)
        try:
            result = retire_provider(account, request.account, data['reason'], data['confirmation_token'],
                                     confirm=data['confirm'], request_id=data['client_request_id'])
        except ProviderLifecycleError:
            return Response({'code': 503, 'msg': '陪玩生命周期状态暂不可核验，请稍后重试'}, status=503)
        return Response({'code': 0, 'data': result, 'msg': '陪玩注销已办理'})
