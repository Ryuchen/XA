"""Saved withdrawal destinations: one per provider and payment method."""
from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from .models import WithdrawalAccount, WithdrawRequest
from .services import get_wallet


def serialize_account(row):
    return {'id': row.pk, 'payee_method': row.payee_method, 'payee_account': row.payee_account,
            'payee_name': row.payee_name, 'version': row.version, 'updated_at': row.updated_at}


def validate_payee(data):
    method = data.get('payee_method')
    method = method.strip().upper() if isinstance(method, str) else None
    if method not in WithdrawRequest.PayeeMethod.values:
        raise ValueError('请选择有效的收款方式')
    account, name = data.get('payee_account'), data.get('payee_name')
    if not isinstance(account, str) or not isinstance(name, str) or not account.strip() or not name.strip():
        raise ValueError('请填写收款账号与收款人姓名')
    if len(account.strip()) > 100 or len(name.strip()) > 50:
        raise ValueError('收款账号不能超过100字，收款人姓名不能超过50字')
    return method, account.strip(), name.strip()


class WithdrawalAccountListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可管理提现账户'})
        rows = WithdrawalAccount.objects.filter(account=request.account)
        return Response({'code': 0, 'data': [serialize_account(row) for row in rows]})


class WithdrawalAccountDetailView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def change(self, request, payee_method, *, delete=False):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可管理提现账户'})
        if payee_method not in WithdrawRequest.PayeeMethod.values:
            return Response({'code': 400, 'msg': '请选择有效的收款方式'})
        expected = request.data.get('expected_version')
        expected_id = request.data.get('expected_id')
        if type(expected) is not int or expected < 0 or type(expected_id) is not int or expected_id < 0:
            return Response({'code': 400, 'msg': '请刷新收款账户后再操作'})
        if not delete:
            try:
                _, number, name = validate_payee({**request.data, 'payee_method': payee_method})
            except ValueError as error:
                return Response({'code': 400, 'msg': str(error)})
        with transaction.atomic():
            # Match withdrawal's wallet -> destination lock order, including the
            # first binding (which has no destination row to lock yet). Locking
            # ClubAccount here would deadlock with the withdrawal receipt's FK.
            get_wallet(account=request.account, user=request.legacy_user, for_update=True)
            row = WithdrawalAccount.objects.select_for_update().filter(
                account=request.account, payee_method=payee_method,
            ).first()
            if (row.version if row else 0) != expected or (row.pk if row else 0) != expected_id:
                return Response({'code': 409, 'errorCode': 'PAYEE_CHANGED',
                                 'msg': '收款账户已变化，请刷新后核对再操作'})
            if delete:
                if row:
                    row.delete()
                return Response({'code': 0, 'msg': '已解绑，已提交的提现申请不受影响'})
            if row:
                row.payee_account, row.payee_name = number, name
                row.version += 1
                row.save(update_fields=['payee_account', 'payee_name', 'version', 'updated_at'])
            else:
                row = WithdrawalAccount.objects.create(account=request.account, payee_method=payee_method,
                                                       payee_account=number, payee_name=name)
            return Response({'code': 0, 'msg': '收款账户已保存', 'data': serialize_account(row)})

    def put(self, request, payee_method):
        return self.change(request, payee_method)

    def delete(self, request, payee_method):
        return self.change(request, payee_method, delete=True)
