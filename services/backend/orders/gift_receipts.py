"""Read a matching committed gift receipt without executing a funds operation."""
import re

from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from wallet.models import IdempotencyKey
from wallet.receipts import fingerprint_for


class GiftReceiptView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '仅赠送老板可以核对'}, status=403)
        request_id = request.data.get('client_request_id')
        path = request.data.get('path')
        payload = request.data.get('payload')
        if (not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,64}', request_id)
                or not isinstance(path, str) or not (path == '/orders/gifts/send/'
                    or re.fullmatch(r'/orders/orders/[0-9]+/tip/', path))
                or not isinstance(payload, dict) or 'client_request_id' in payload):
            return Response({'code': 400, 'msg': '请提供原赠送凭证'}, status=400)
        # Recover the deployment prefix from this URL, then reproduce the original request path.
        original_path = request.path[:-len('gifts/receipt/')] + path[len('/orders/'):]
        fingerprint = fingerprint_for(original_path, payload)
        receipt = IdempotencyKey.objects.filter(account=request.account, request_id=request_id).first()
        if receipt is None or not receipt.request_fingerprint or receipt.response is None:
            # Absence can mean an in-flight request or an old incomplete receipt.
            return Response({'code': 0, 'data': {'status': 'UNKNOWN'}})
        if receipt.scope != 'order.tip' or receipt.request_fingerprint != fingerprint:
            return Response({'code': 409, 'errorCode': 'REQUEST_ID_CONFLICT',
                             'msg': '交易凭证不匹配，请联系客服核对'}, status=409)
        return Response({'code': 0, 'data': {'status': 'COMMITTED', 'fundResult': receipt.response}})
