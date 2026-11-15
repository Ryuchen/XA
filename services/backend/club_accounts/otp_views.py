from collections.abc import Mapping

from rest_framework.response import Response
from rest_framework.views import APIView

from .models import OtpCredential
from .otp import otp_login_gate
from .permissions import IsClubAccountAuthenticated


def _no_store(response):
    response['Cache-Control'] = 'no-store'
    return response


class OtpSetupView(APIView):
    """Enroll only the authenticated account; existing credentials cannot be replaced."""

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'login'

    def get(self, request):
        return _no_store(Response({'code': 0, 'data': {
            'isOtpBound': OtpCredential.objects.filter(account=request.account).exists(),
        }}))

    def post(self, request):
        if not isinstance(request.data, Mapping):
            return _no_store(Response({'code': 400, 'msg': '验证请求格式不正确'}, status=400))
        result = otp_login_gate(request.account, request.data, 'setup', setup_only=True)
        if isinstance(result, Response):
            return _no_store(result)
        return _no_store(Response({'code': 0, 'data': {
            'isOtpBound': True, 'recoveryCodes': result['recoveryCodes'],
        }}))
