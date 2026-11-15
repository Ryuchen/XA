"""Historical, not-yet-active pass refunds, within the current store only."""
from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from wallet.models import Transaction, get_platform_wallet
from wallet.receipts import fund_success, recover_fund_result
from wallet.services import claim_fund_write, get_wallet

from .models import EscortProfile, ProviderPassPointRedemption, ProviderPassPurchase, ProviderPassRefund
from .pass_rules import lock_pass_config


def _owner(profile):
    return Q(account_id=profile.account_id) | Q(account__isnull=True, provider_id=profile.user_id)


class ProviderPassRefundView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'wallet_write'

    @recover_fund_result('provider.pass.refund')
    def post(self, request, purchase_id):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩本人可申请退款'})
        request_id = request.data.get('client_request_id')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 64:
            return Response({'code': 400, 'msg': '缺少有效的退款请求编号'})

        with transaction.atomic():
            if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_provider_operation
                try:
                    lock_provider_operation(request.account)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请联系客服'}, status=503)
                # Authentication can precede a competing lifecycle change.
                # Existing entitlement refunds remain available to a stopped
                # provider in the same store membership cycle.
                if not ClubAccount.objects.filter(pk=request.account.pk,
                        account_type=ClubAccount.AccountType.PROVIDER, license_membership_active=True,
                        business_cycle=request.account.business_cycle).exists():
                    return Response({'code': 409, 'msg': '陪玩账号周期已变化，请重新登录核对'}, status=409)
            # Match purchase lock order; serialization covers purchases and
            # concurrent refunds, including their schedule changes.
            lock_pass_config()
            profile = EscortProfile.objects.select_for_update().filter(account=request.account).first()
            if profile is None or not profile.user_id:
                return Response({'code': 403, 'msg': '陪玩资料不完整，请联系客服核对'})
            records = list(ProviderPassPurchase.objects.select_for_update().filter(
                _owner(profile),
            ).exclude(pk__in=ProviderPassRefund.objects.values('purchase_id'))
                .order_by('created_at', 'id'))
            selected = next((item for item in records if item.pk == purchase_id), None)
            if selected is None:
                return Response({'code': 404, 'msg': '通行证不存在或已退款，请查看采购记录'})
            now = timezone.now()
            if selected.starts_at <= now:
                return Response({'code': 400, 'msg': '通行证已生效，不能退款'})
            if selected.provider_id != profile.user_id:
                return Response({'code': 409, 'msg': '通行证归属需核对，请联系客服'})
            if (selected.account_id != request.account.pk or not selected.business_cycle
                    or selected.business_cycle != request.account.business_cycle):
                return Response({'code': 409, 'msg': '通行证购买来源周期待核对，请联系客服'}, status=409)

            pending = [item for item in records if item.starts_at > now]
            active = [item for item in records if item.starts_at <= now < item.expires_at]
            if (any(item.provider_id != profile.user_id or item.account_id != request.account.pk
                    or not item.business_cycle or item.business_cycle != request.account.business_cycle
                    for item in pending + active)
                    or ProviderPassPurchase.objects.filter(
                        provider_id=profile.user_id, expires_at__gt=now,
                    ).exclude(_owner(profile)).exclude(
                        pk__in=ProviderPassRefund.objects.values('purchase_id'),
                    ).exists()):
                return Response({'code': 409, 'msg': '通行证归属需核对，请联系客服'})
            if len(active) > 1 or any(a.expires_at > b.starts_at for a, b in zip(pending, pending[1:])):
                return Response({'code': 409, 'msg': '通行证时间存在冲突，请联系客服核对'})
            if active and pending and active[0].expires_at > pending[0].starts_at:
                return Response({'code': 409, 'msg': '通行证时间存在冲突，请联系客服核对'})
            # Old pass implementation queued same-tier days in the profile.
            # Refuse to rewrite a schedule whose stored profile is inconsistent.
            if (not profile.pass_expires_at or not profile.pass_tier
                    or profile.pass_expires_at != pending[-1].expires_at
                    or any(item.tier != profile.pass_tier for item in pending)
                    or (active and active[0].tier != profile.pass_tier)):
                return Response({'code': 409, 'msg': '通行证权益记录需核对，请联系客服'})

            original_tx = selected.transaction
            if (original_tx.tx_type != Transaction.TxType.PASS_PURCHASE
                    or original_tx.status != Transaction.Status.SUCCESS
                    or selected.paid_amount <= 0 or original_tx.amount != -selected.paid_amount
                    or ProviderPassPurchase.objects.filter(transaction_id=original_tx.pk).count() != 1
                    or original_tx.reversals.filter(status=Transaction.Status.SUCCESS, amount__gt=0).exists()):
                return Response({'code': 409, 'msg': '原付款记录需核对，请联系客服'})
            wallet = get_wallet(account=request.account, user_id=profile.user_id, for_update=True)
            if original_tx.wallet_id != wallet.pk:
                return Response({'code': 409, 'msg': '原付款账户需核对，请联系客服'})
            points = ProviderPassPointRedemption.objects.select_for_update().select_related('ledger').filter(purchase=selected).first()
            points_profile = None
            if bool(selected.point_discount_amount) != bool(points):
                return Response({'code': 409, 'msg': '原积分支付记录需核对，请联系客服'})
            if points:
                if (points.discount_units != selected.point_discount_amount
                        or points.ledger.account_id != request.account.pk
                        or points.ledger.amount != -points.points_hundredths
                        or points.ledger.event_key != f'pass:{selected.pk}:points'
                        or points.ledger.kind != 'PASS_POINTS'):
                    return Response({'code': 409, 'msg': '原积分支付记录需核对，请联系客服'})
                from engagement.services import locked_profile
                points_profile = locked_profile(request.account)
            if wallet.balance + selected.paid_amount > 2147483647:
                return Response({'code': 409, 'msg': '退款金额超出钱包范围，请联系客服'})
            shop = get_platform_wallet(for_update=True)
            # The shared shop wallet contains a dedicated deposit pool. A pass
            # refund must not consume that pool; the unfunded amount is owed.
            deposit_pool = Transaction.objects.filter(
                wallet=shop, tx_type=Transaction.TxType.DEPOSIT_INCOME,
                status=Transaction.Status.SUCCESS,
            ).aggregate(total=Sum('amount'))['total'] or 0
            available = max(0, shop.balance - max(0, deposit_pool))
            funded = min(selected.paid_amount, available)
            shortfall = selected.paid_amount - funded
            allowed, reason = claim_fund_write(
                account=request.account, request_id=request_id,
                scope='provider.pass.refund', fingerprint=str(selected.pk),
            )
            if not allowed:
                return Response({'code': 409, 'msg': reason})

            old_start, old_end = selected.starts_at, selected.expires_at
            changes = []
            cursor = old_start
            for item in pending[pending.index(selected) + 1:]:
                old_item_start, old_item_end = item.starts_at, item.expires_at
                item.starts_at, item.expires_at = cursor, cursor + (old_item_end - old_item_start)
                cursor = item.expires_at
                if (item.starts_at, item.expires_at) != (old_item_start, old_item_end):
                    changes.append({'purchase_id': item.pk,
                                    'old_start': old_item_start.isoformat(), 'old_end': old_item_end.isoformat(),
                                    'new_start': item.starts_at.isoformat(), 'new_end': item.expires_at.isoformat()})
                    item.save(update_fields=['starts_at', 'expires_at'])
            remaining = [item for item in pending if item.pk != selected.pk]
            # When earlier entitlement was entered before purchase records
            # existed, the first queued start is evidence of its expiry.
            prior_end = active[0].expires_at if active else pending[0].starts_at
            profile.pass_expires_at = remaining[-1].expires_at if remaining else prior_end
            if profile.pass_expires_at <= now:
                profile.pass_tier = ''
            profile.save(update_fields=['pass_tier', 'pass_expires_at'])

            before = wallet.balance
            wallet.balance += selected.paid_amount
            wallet.save(update_fields=['balance'])
            provider_tx = Transaction.objects.create(
                wallet=wallet, tx_type=Transaction.TxType.PASS_PURCHASE,
                amount=selected.paid_amount, balance_before=before, balance_after=wallet.balance,
                reversal_of=original_tx, remark=f'通行证退款 #{selected.pk}',
            )
            shop_tx = None
            if funded:
                before_shop = shop.balance
                shop.balance -= funded
                shop.save(update_fields=['balance'])
                shop_tx = Transaction.objects.create(
                    wallet=shop, tx_type=Transaction.TxType.PASS_INCOME,
                    amount=-funded, balance_before=before_shop, balance_after=shop.balance,
                    external_tx_id=provider_tx.tx_no, remark=f'通行证退款 #{selected.pk}',
                )
            if points:
                from engagement.services import change_points
                return_ledger = change_points(points_profile, points.points_hundredths,
                    f'pass:{selected.pk}:points-refund', 'PASS_POINTS_RETURN', f'通行证退款 #{selected.pk} 退回积分')
            refund = ProviderPassRefund.objects.create(
                purchase=selected, account=request.account, amount=selected.paid_amount,
                funded_amount=funded, shortfall_amount=shortfall,
                provider_transaction=provider_tx, shop_transaction=shop_tx,
                original_starts_at=old_start, original_expires_at=old_end, schedule_changes=changes,
                **({'points_returned': points.points_hundredths,
                    'points_return_ledger': return_ledger} if points else {}),
            )
            result = fund_success(request, 'provider.pass.refund', msg='通行证退款成功', data={
                'purchase_id': selected.pk, 'refund_id': refund.pk,
                'refunded_amount': refund.amount, 'balance': wallet.balance,
                'points_returned': refund.points_returned,
            })
            from wallet.notifier import notify_wallet_update
            transaction.on_commit(lambda: notify_wallet_update(wallet))
        return result
