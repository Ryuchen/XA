"""Platform-only offline license billing; no payment provider is called."""
import uuid
from collections.abc import Mapping
from datetime import date

from django.db import DatabaseError, transaction
from django.db.models import Q

from .license_billing import LicenseBillingError, contract_summary, receipt_summary, record_receipt, update_due, void_receipt
from .models import PlatformAuditEvent, StoreLicenseContract, StoreLicenseReceipt
from .platform_accounts import AccountChangeError
from .platform_license_views import _PlatformLicenseBase, _response


class PlatformLicenseContractView(_PlatformLicenseBase):
    def get(self, request, contract_id):
        if not self.allowed(request):
            return self.denied()
        try:
            with transaction.atomic(using='default'):
                contract = StoreLicenseContract.objects.select_for_update().filter(pk=contract_id).first()
                if not contract:
                    return _response({'code': 404, 'msg': '合同不存在'}, 404)
                receipts = list(StoreLicenseReceipt.objects.filter(contract=contract).order_by('created_at', 'id'))
                receipt_ids = [str(row.pk) for row in receipts]
                events = (PlatformAuditEvent.objects.filter(target_store_slug=contract.store.slug).filter(
                    Q(action='license.contract_amount_changed', target_type='license_contract', target_id=str(contract.pk))
                    | Q(action__in=('license.receipt_recorded', 'license.receipt_voided'),
                        target_type='license_receipt', target_id__in=receipt_ids))
                    .select_related('account').order_by('-created_at', '-id'))
                allowed = {'license.contract_amount_changed': ('before_fen', 'after_fen', 'reason'),
                    'license.receipt_recorded': ('contract_id', 'amount_fen', 'received_on', 'note'),
                    'license.receipt_voided': ('contract_id', 'reason')}
                audit = []
                for event in events:
                    detail = event.detail if isinstance(event.detail, dict) else {}
                    audit.append({'id': event.pk, 'action': event.action, 'target_type': event.target_type,
                        'target_id': event.target_id, 'created_at': event.created_at.isoformat(),
                        'actor': {'id': str(event.account_id), 'username': event.account.username,
                                  'nickname': event.account.nickname},
                        'detail': {key: detail[key] for key in allowed[event.action] if key in detail}})
                return _response({'code': 0, 'data': {**contract_summary(contract, lock=True),
                    'receipts': [receipt_summary(row) for row in receipts], 'audit_events': audit}})
        except DatabaseError:
            return _response({'code': 503, 'msg': '合同服务暂不可用'}, 503)

    def patch(self, request, contract_id):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'amount_due_fen', 'reason'}:
            return _response({'code': 400, 'msg': '请填写合同金额和变更原因'}, 400)
        try:
            data = update_due(request.auth, contract_id=contract_id, amount_fen=request.data['amount_due_fen'],
                              reason=request.data['reason'])
            return _response({'code': 0, 'data': data})
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except LicenseBillingError as error:
            return _response({'code': 400, 'msg': str(error)}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '合同服务暂不可用'}, 503)


class PlatformLicenseReceiptView(_PlatformLicenseBase):
    def post(self, request, contract_id):
        if not self.allowed(request):
            return self.denied()
        required = {'request_id', 'amount_fen', 'received_on', 'note'}
        if not isinstance(request.data, Mapping) or set(request.data) != required:
            return _response({'code': 400, 'msg': '请填写完整收款信息'}, 400)
        try:
            request_id = uuid.UUID(request.data['request_id'])
            received_on = date.fromisoformat(request.data['received_on'])
            receipt, created = record_receipt(request.auth, contract_id=contract_id, request_id=request_id,
                amount_fen=request.data['amount_fen'], received_on=received_on, note=request.data['note'])
            return _response({'code': 0, 'data': {**receipt_summary(receipt), 'newly_recorded': created}}, 201 if created else 200)
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except LicenseBillingError as error:
            return _response({'code': 400, 'msg': str(error)}, 400)
        except (ValueError, TypeError, AttributeError):
            return _response({'code': 400, 'msg': '收款信息无效或超过合同应收'}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '收款服务暂不可用'}, 503)


class PlatformLicenseReceiptVoidView(_PlatformLicenseBase):
    def post(self, request, receipt_id):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'reason'}:
            return _response({'code': 400, 'msg': '请填写作废原因'}, 400)
        try:
            receipt = void_receipt(request.auth, receipt_id=receipt_id, reason=request.data['reason'])
            return _response({'code': 0, 'data': receipt_summary(receipt)})
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except LicenseBillingError as error:
            return _response({'code': 400, 'msg': str(error)}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '收款服务暂不可用'}, 503)
