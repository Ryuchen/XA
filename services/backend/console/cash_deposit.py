"""Record verified offline cash against the store deposit pool."""
import hashlib
import unicodedata

from django.conf import settings
from django.db import IntegrityError
from rest_framework import serializers
from rest_framework.response import Response
from stores.db import transaction

from common.media import build_media_url
from users.models import EscortProfile
from users.provider_registration import validate_picture
from wallet.models import CashDepositTopup, Transaction, Wallet, get_platform_wallet
from wallet.services import claim_fund_write, normalize_request_id


class CashGapReconciliationError(ValueError):
    pass


def row_data(row, request):
    return {'id': row.pk, 'kind': row.kind, 'kind_label': row.get_kind_display(), 'amount': row.amount, 'paid_before': row.paid_before,
        'paid_after': row.paid_after, 'target_snapshot': row.target_snapshot,
        'reason': row.reason, 'reference': row.reference, 'created_at': row.created_at,
        'operator_name': row.operator.nickname or row.operator.username, 'registration_id': row.source_registration_id,
        'proof_image_url': build_media_url(request, row.proof_image),
        'platform_transaction_id': row.platform_transaction_id}


def list_topups(request, profile):
    rows = CashDepositTopup.objects.filter(profile=profile).select_related('operator')[:100]
    return Response({'code': 0, 'data': [row_data(row, request) for row in rows]})


def register_topup(request, profile_id):
    try:
        amount = serializers.IntegerField(min_value=1, max_value=2147483647).run_validation(request.data.get('amount'))
        reason = serializers.CharField(max_length=255, min_length=1).run_validation(request.data.get('reason'))
        reference = serializers.CharField(max_length=100, allow_blank=True, required=False).run_validation(request.data.get('reference', ''))
        request_id = serializers.CharField(min_length=8, max_length=64).run_validation(request.data.get('client_request_id'))
        if normalize_request_id(request_id) != request_id or request_id.startswith(('auto:', 'registration-')):
            raise serializers.ValidationError('操作编号无效')
        proof = request.FILES.get('proof_image')
        proof_key = None
        if proof:
            proof = serializers.ImageField(validators=[validate_picture]).run_validation(proof)
            digest = hashlib.sha256()
            for chunk in proof.chunks():
                digest.update(chunk)
            proof_key = digest.hexdigest()
            proof.seek(0)
        if not reference and not proof:
            raise serializers.ValidationError('请提供线下收款凭据编号或上传凭证图片')
    except serializers.ValidationError as error:
        return Response({'code': 400, 'msg': str(error.detail)}, status=400)

    normalized = unicodedata.normalize('NFKC', reference).strip().casefold()
    reference_key = hashlib.sha256(normalized.encode('utf-8')).hexdigest() if normalized else None
    saved = None
    lifecycle_provider = None
    if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        from club_accounts.models import ClubAccount
        account_id = EscortProfile.objects.filter(pk=profile_id).values_list('account_id', flat=True).first()
        lifecycle_provider = ClubAccount.objects.filter(pk=account_id, account_type='PROVIDER').first()
        if lifecycle_provider is None:
            return Response({'code': 409, 'msg': '陪玩账号归属待核对'}, status=409)
    try:
        with transaction.atomic():
            if lifecycle_provider:
                from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_provider_operation
                try:
                    lock_provider_operation(lifecycle_provider)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请联系平台'}, status=503)
            profile = EscortProfile.objects.select_for_update().select_related('account').filter(pk=profile_id).first()
            if not profile or not profile.account_id:
                return Response({'code': 404, 'msg': '陪玩不存在或尚未绑定门店账号'}, status=404)
            if lifecycle_provider and profile.account_id != lifecycle_provider.pk:
                return Response({'code': 409, 'msg': '陪玩账号已变化，请重新核对'}, status=409)
            if lifecycle_provider and not ClubAccount.objects.filter(
                    pk=lifecycle_provider.pk, account_type=ClubAccount.AccountType.PROVIDER,
                    business_cycle=lifecycle_provider.business_cycle,
                    license_membership_active=True).exists():
                # A retirement or re-entry may have won while this request
                # waited for the lifecycle lock. Never post a prior-cycle
                # offline receipt into the new or retired membership.
                return Response({'code': 409, 'msg': '陪玩门店身份已变化，请重新核对'}, status=409)
            if profile.deposit_mode != 'CASH':
                return Response({'code': 400, 'msg': '仅现金押金模式可登记线下补差'}, status=400)
            if not profile.account.is_active:
                return Response({'code': 403, 'msg': '陪玩账号已停用，请先核对'}, status=403)
            previous = CashDepositTopup.objects.select_for_update().filter(operator=request.account,
                client_request_id=request_id).first()
            if previous:
                return Response({'code': 409, 'msg': '该操作已登记，请核对补差记录'}, status=409)
            if reference_key and CashDepositTopup.objects.filter(profile=profile, reference_key=reference_key).exists():
                return Response({'code': 409, 'msg': '该陪玩已登记此收款凭据，请核对历史记录'}, status=409)
            if proof_key:
                same_proof = CashDepositTopup.objects.filter(profile=profile, proof_key=proof_key)
                if (not reference_key and same_proof.exists()) or (reference_key and same_proof.filter(reference_key__isnull=True).exists()):
                    return Response({'code': 409, 'msg': '该陪玩已登记此凭证图片，请核对历史记录'}, status=409)
            target = profile.effective_deposit_required
            if amount > max(target - profile.deposit_paid, 0):
                return Response({'code': 400, 'msg': '补差金额超过当前未缴押金，请刷新后核对'}, status=400)
            from wallet.deposit_cycle import deposit_cycle
            try:
                cycle = deposit_cycle(profile, increase=True)
            except ValueError as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)
            platform = get_platform_wallet(for_update=True)
            if platform.balance + amount > 2147483647:
                return Response({'code': 400, 'msg': '门店押金账户余额超过系统范围'}, status=400)
            allowed, reason_code = claim_fund_write(account=request.account, request_id=request_id,
                scope='deposit.cash_topup', fingerprint=f'{profile.pk}:{amount}:{reference_key}:{reason}')
            if not allowed:
                return Response({'code': 409, 'msg': reason_code}, status=409)
            before, paid_before = platform.balance, profile.deposit_paid
            platform.balance += amount
            platform.save(update_fields=['balance'])
            profile.deposit_paid += amount
            profile.save(update_fields=['deposit_paid', 'deposit_business_cycle'])
            platform_tx = Transaction.objects.create(wallet=platform, amount=amount,
                tx_type=Transaction.TxType.DEPOSIT_INCOME, balance_before=before, balance_after=platform.balance,
                operator=request.legacy_user, operator_account=request.account,
                remark=f'线下现金押金补差：陪玩档案 {profile.pk}；{reason}'[:255])
            saved = CashDepositTopup(profile=profile, account=profile.account, operator=request.account,
                account_business_cycle=cycle,
                amount=amount, paid_before=paid_before, paid_after=profile.deposit_paid, target_snapshot=target,
                reason=reason, reference=reference, reference_key=reference_key, proof_image=proof or '', proof_key=proof_key,
                client_request_id=request_id, platform_transaction=platform_tx)
            saved.save()
            # A refund deficit is a separate liability from the deposit
            # balance. Reconcile the same audited receipt before committing.
            from wallet.models import RefundDepositCashRepayment, RefundDepositDeficit, RefundDepositRecoveryPermit
            from wallet.refund_deposit import apply_cash_topup_locked, protection_enabled
            connection = transaction.get_connection()
            tables = set(connection.introspection.table_names())
            if RefundDepositDeficit._meta.db_table in tables:
                if (RefundDepositDeficit.objects.filter(profile=profile, remaining__gt=0).exists()
                        and not {RefundDepositCashRepayment._meta.db_table,
                                 RefundDepositRecoveryPermit._meta.db_table}.issubset(tables)):
                    raise CashGapReconciliationError('退款押金缺口数据尚未就绪，请先核对')
                try:
                    apply_cash_topup_locked(topup=saved, profile=profile, platform=platform)
                except ValueError as exc:
                    raise CashGapReconciliationError(str(exc)) from exc
            elif protection_enabled():
                raise CashGapReconciliationError('退款押金缺口数据尚未就绪，请先核对')
            from site_messages.deposit_event_notice import record_topup
            record_topup(saved)
            provider_wallet = Wallet.objects.filter(account_id=profile.account_id).first()
            if provider_wallet:
                from wallet.notifier import notify_wallet_update
                transaction.on_commit(lambda: notify_wallet_update(provider_wallet))
            return Response({'code': 0, 'msg': '现金押金补差已登记', 'data': row_data(saved, request)})
    except CashGapReconciliationError as exc:
        if saved and saved.proof_image and saved.proof_image._committed:
            saved.proof_image.storage.delete(saved.proof_image.name)
        return Response({'code': 409, 'msg': str(exc)}, status=409)
    except IntegrityError:
        if saved and saved.proof_image and saved.proof_image._committed:
            saved.proof_image.storage.delete(saved.proof_image.name)
        return Response({'code': 409, 'msg': '补差记录或凭据已登记，请核对历史记录'}, status=409)
    except Exception:
        if saved and saved.proof_image and saved.proof_image._committed:
            saved.proof_image.storage.delete(saved.proof_image.name)
        raise
