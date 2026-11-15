"""Order-scoped discovery of store-configured service add-ons."""

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from .catalog import visible_service_q
from .models import (Order, OrderParticipationSegment, OrderProvider,
                     ServiceAddonConfiguration, ServiceAddonPriceOption)


def _owner_order(account, order_id):
    if (account.account_type != ClubAccount.AccountType.BOSS
            or not 0 < order_id <= 9223372036854775807):
        return None
    return Order.objects.select_related('service').filter(
        pk=order_id, customer_account=account, business_cycle=account.business_cycle).first()


def _service_in_progress(order):
    if (order.status != Order.Status.IN_SERVICE or order.is_gift_snapshot
            or order.service.is_addon or not order.service.game_category_id):
        return False
    if order.payment_status in (Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED):
        return True
    if order.payment_status != Order.PaymentStatus.REFUNDED:
        return False
    # Discovery and intent creation do not move money. Require the persisted
    # completed refund terminations and an actually continuing participant;
    # the payment transaction separately verifies the complete funding chain.
    from .models import PointsNegotiationPosting, OrderOriginalShareTransfer
    if (order.refund_records.exists() and OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=order).exists()):
        postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
            termination_in_service=True, termination_processed_at__isnull=False)
            .order_by('points_refund__refund_id'))
        if len(postings) != order.refund_records.count():
            return False
        active = OrderParticipationSegment.objects.filter(attribution__order=order,
            attribution__status='ACTIVE', released_at__isnull=True, started_at__isnull=False,
            financial_snapshot__isnull=False)
        for segment in active:
            if (OrderProvider.objects.filter(order=order, provider_account_id=segment.provider_account_id,
                    released_at__isnull=True, settled_at__isnull=True).exists()
                    and all(segment.provider_account_id not in posting.proposal.affected_accounts
                            or (posting.proposal.affected_segment_ids and
                                segment.pk not in posting.proposal.affected_segment_ids)
                            for posting in postings)):
                return True
        return False
    if (order.required_provider_count != 2 or order.refund_records.count() != 1
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        return False
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
        points_refund__is_final=True, termination_in_service=True, completed_by_negotiation=False,
        termination_processed_at__isnull=False).select_related('points_refund')[:2])
    active = list(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True))
    if len(postings) != 1 or len(active) != 1 or not active[0]:
        return False
    shares = list(postings[0].shares.select_related('assignment'))
    return bool(len(shares) == 1 and shares[0].account_id != active[0]
                and shares[0].assignment.released_at == postings[0].accepted_at
                and OrderParticipationSegment.objects.filter(attribution__order=order,
                    provider_account_id=active[0], released_at__isnull=True,
                    started_at__isnull=False).exists())


def _configurations(order):
    return (ServiceAddonConfiguration.objects.filter(
        visible_service_q('addon__'), service_id=order.service_id, enabled=True,
        addon__is_addon=True, addon__game_category_id=order.service.game_category_id)
        .exclude(addon__service_category__is_gift=True))


class AddonSelectionError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def validate_selection(order, data, *, lock=False):
    """Rebuild a selection from current store data, optionally under locks."""
    fields = {'addon_id', 'unit', 'quantity', 'configuration_version', 'provider_account_id'}
    if (not isinstance(data, dict) or set(data) != fields
            or any(type(data[key]) is not int or not 0 < data[key] <= 2147483647
                   for key in ('addon_id', 'quantity', 'configuration_version', 'provider_account_id'))
            or data['unit'] not in ServiceAddonPriceOption.Unit.values):
        raise AddonSelectionError('请选择项目、计价单位、整数数量和负责人', 400)
    if not _service_in_progress(order):
        raise AddonSelectionError('订单当前不能加购')
    if lock:
        from .models import ServiceItem
        # Configuration editors acquire service rows in the same stable order.
        list(ServiceItem.objects.select_for_update().filter(pk__in=[order.service_id, data['addon_id']])
             .order_by('pk').values_list('pk', flat=True))
    configs = _configurations(order)
    if lock:
        configs = configs.select_for_update()
    config = configs.select_related('addon').filter(addon_id=data['addon_id']).first()
    if not config or config.version != data['configuration_version']:
        raise AddonSelectionError('加购配置已变化，请刷新')
    options = config.price_options
    if lock:
        options = options.select_for_update()
    option = options.filter(unit=data['unit'], enabled=True).first()
    if option is None or option.price <= 0:
        raise AddonSelectionError('计价单位已变化，请刷新')
    segments = OrderParticipationSegment.objects.filter(attribution__order=order,
        attribution__status='ACTIVE', provider_account_id=data['provider_account_id'],
        released_at__isnull=True, started_at__isnull=False, financial_snapshot__isnull=False)
    assignments = OrderProvider.objects.filter(order=order, provider_account_id=data['provider_account_id'],
                                                released_at__isnull=True)
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.exists():
        assignments = assignments.filter(settled_at__isnull=True)
    if lock:
        segments, assignments = segments.select_for_update(), assignments.select_for_update()
    segments = list(segments)
    if len(segments) != 1 or not assignments.exists():
        raise AddonSelectionError('请选择主单当前履约的陪玩')
    if not _configurations(order).filter(pk=config.pk, version=config.version).exists():
        raise AddonSelectionError('加购配置已变化，请刷新')
    amount = option.price * data['quantity']
    if amount > 2147483647:
        raise AddonSelectionError('加购金额超出支持范围', 400)
    return config, option, segments[0], amount


class OrderAddonOffersView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, order_id):
        order = _owner_order(request.account, order_id)
        if order is None:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        if not _service_in_progress(order):
            return Response({'code': 0, 'data': {'order_id': order.pk, 'items': [],
                'purchase_available': False, 'reason': 'ORDER_NOT_ELIGIBLE'}})

        rows = (_configurations(order).select_related('addon').prefetch_related('price_options')
                .order_by('addon__sort_order', 'pk'))
        items = []
        for row in rows:
            options = [{'unit': option.unit, 'unit_price_raw': option.price}
                       for option in row.price_options.all() if option.enabled and option.price > 0]
            if options:
                items.append({'addon_id': row.addon_id, 'name': row.addon.name,
                              'conditions': row.conditions, 'condition_mode': row.condition_mode,
                              'configuration_version': row.version,
                              'options': sorted(options, key=lambda option: option['unit'])})
        # The catalogue is not a purchase authorization: game rules, pricing,
        # wallet posting and settlement must be checked together on purchase.
        return Response({'code': 0, 'data': {'order_id': order.pk, 'items': items,
            'purchase_available': False, 'reason': 'PURCHASE_NOT_AVAILABLE'}})


class OrderAddonQuoteView(APIView):
    """Preview store prices; payment must revalidate every fact under lock."""

    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        order = _owner_order(request.account, order_id)
        if order is None:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        try:
            config, option, segment, amount = validate_selection(order, request.data)
        except AddonSelectionError as exc:
            return Response({'code': exc.status, 'msg': str(exc)}, status=exc.status)
        # A quote can tell the buyer whether the selected facts support an
        # application, but it cannot promise payment before an intent fixes
        # wallet sources and the payment transaction rechecks the funding.
        intent_reason = None
        if config.condition_mode != ServiceAddonConfiguration.ConditionMode.NONE:
            intent_reason = 'STAFF_REVIEW_REQUIRED'
        elif config.conditions:
            intent_reason = 'CONDITIONS_REVIEW_REQUIRED'
        else:
            from .addon_intents import _commission
            try:
                from .points_service_payment import needs_integrated_processing
                if needs_integrated_processing(order) and order.refund_records.count() >= 4:
                    from stores.db import transaction
                    from .points_transfer_refund_access import after_transfer
                    from .points_round_funding import verify_active_segment_locked
                    with transaction.atomic():
                        with after_transfer(order.pk, phase='refund', allow_addon_completion=True) as locked_order:
                            config, option, segment, amount = validate_selection(locked_order, request.data, lock=True)
                            verify_active_segment_locked(locked_order, segment)
                            _commission(segment, allow_refund_review=True)
                            if config.condition_mode != ServiceAddonConfiguration.ConditionMode.NONE:
                                intent_reason = 'STAFF_REVIEW_REQUIRED'
                            elif config.conditions:
                                intent_reason = 'CONDITIONS_REVIEW_REQUIRED'
                else:
                    _commission(segment, lock=False)
            except AddonSelectionError:
                intent_reason = 'COMMISSION_REVIEW_REQUIRED'
            except ValidationError:
                intent_reason = 'COMMISSION_REVIEW_REQUIRED'
            from django.conf import settings
            if not intent_reason and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                from club_accounts.provider_lifecycle import ensure_provider_lifecycle, provider_membership_current, ProviderLifecycleError
                provider = ClubAccount.objects.filter(pk=segment.provider_account_id,
                    account_type=ClubAccount.AccountType.PROVIDER).first()
                try:
                    if (provider is None or segment.provider_business_cycle != provider.business_cycle
                            or not provider_membership_current(provider)):
                        raise ProviderLifecycleError('陪玩账号周期已变化')
                    ensure_provider_lifecycle(provider)
                except ProviderLifecycleError:
                    intent_reason = 'PROVIDER_REVIEW_REQUIRED'
        return Response({'code': 0, 'data': {'order_id': order.pk, 'addon_id': config.addon_id,
            'name': config.addon.name, 'unit': option.unit, 'quantity': request.data['quantity'],
            'unit_price_raw': option.price, 'amount_raw': amount, 'configuration_version': config.version,
            'provider_account_id': segment.provider_account_id, 'segment_id': segment.pk,
            'conditions': config.conditions, 'condition_mode': config.condition_mode,
            'intent_available': intent_reason is None, 'intent_reason': intent_reason,
            'purchase_available': False, 'reason': 'PURCHASE_NOT_AVAILABLE'}})
