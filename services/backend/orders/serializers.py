from datetime import timedelta

from users.qualifications import service_skill
from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.utils import timezone

from club_accounts.models import ClubAccount
from club_accounts.services import (
    get_or_create_account_for_legacy_user,
    link_legacy_relations,
)
from .models import Evaluation, Order, OrderStatusLog, ServiceFavorite, ServiceItem
from .catalog import visible_service_q


class ServiceItemSerializer(EntityNumberModelSerializer):
    # Public prices are the full package price; accounting retains per-person
    # unit prices and applies billing_provider_count exactly once.
    price = serializers.SerializerMethodField()
    unit_price = serializers.IntegerField(source="price", read_only=True)

    def get_price(self, obj):
        return obj.price * obj.billing_provider_count

    gameplay_name = serializers.CharField(source='gameplay.name', read_only=True, default='')
    gameplay_kind = serializers.CharField(source='gameplay.kind', read_only=True, default='')
    gameplay_theme = serializers.CharField(source='gameplay.theme', read_only=True, default='custom')
    gameplay_cover_url = serializers.CharField(source='gameplay.cover_url', read_only=True, default='')
    platform_name = serializers.CharField(source='get_platform_display', read_only=True)
    security_level_name = serializers.CharField(source='get_security_level_display', read_only=True)
    game_category_name = serializers.CharField(source='game_category.name', read_only=True, default='')
    service_category_name = serializers.CharField(source='service_category.name', read_only=True, default='')
    is_gift = serializers.BooleanField(source='service_category.is_gift', read_only=True, default=False)
    gift_commission_rate = serializers.SerializerMethodField()
    required_level_name = serializers.CharField(source='required_level.name', read_only=True, default='')
    provider_requirement = serializers.SerializerMethodField()
    def get_provider_requirement(self,obj):
        from users.qualifications import public_requirement
        return public_requirement(obj)

    class Meta:
        model = ServiceItem
        fields = ['id', 'name', 'description', 'price', 'unit_price', 'billing_provider_count', 'fulfillment_provider_count', 'gift_naming_days', 'gift_festival', 'gift_starts_at', 'gift_ends_at', 'gameplay', 'gameplay_name', 'gameplay_kind', 'gameplay_theme', 'gameplay_cover_url', 'platform', 'platform_name', 'security_level', 'security_level_name', 'game_category', 'game_category_name',
                  'service_category', 'service_category_name', 'is_gift', 'gift_commission_rate',
                  'required_level', 'required_level_name', 'provider_kind', 'provider_grade', 'provider_requirement', 'cover_url',
                  'price_unit', 'purchase_mode', 'purchase_note', 'is_addon', 'catalog_family', 'catalog_variant', 'experience_kind', 'service_type', 'allows_discount_coupons']

    def to_representation(self, instance):
        from common.catalog_images import catalog_display_url
        data = super().to_representation(instance)
        for field in ('cover_url', 'gameplay_cover_url'):
            if field in data:
                data[field] = catalog_display_url(data[field])
        for field in ('images', 'rules_images', 'introduction_images'):
            if field in data:
                data[field] = list(dict.fromkeys(url for value in (data[field] or [])
                    if (url := catalog_display_url(value))))
        return data

    def get_gift_commission_rate(self, obj):
        from .gifts import GIFT_COMMISSION_RATE
        if not (obj.service_category and obj.service_category.is_gift):
            return None
        return GIFT_COMMISSION_RATE if obj.commission_rate is None else obj.commission_rate


class ServiceItemDetailSerializer(ServiceItemSerializer):
    details_html = serializers.SerializerMethodField()
    details_images = serializers.SerializerMethodField()
    account_fields = serializers.SerializerMethodField()

    def get_account_fields(self, obj):
        from .account_fields import effective
        try:
            return effective(obj)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None

    def _content(self, obj):
        from common.rich_text import catalog_content
        cache = getattr(self, '_catalog_content_cache', None)
        if cache is None:
            cache = self._catalog_content_cache = {}
        if obj.pk not in cache:
            cache[obj.pk] = catalog_content(obj)
        return cache[obj.pk]

    def get_details_html(self, obj):
        return self._content(obj)['html']

    def get_details_images(self, obj):
        return self._content(obj)['images']

    variants = serializers.SerializerMethodField()
    roulette_pool = serializers.SerializerMethodField()
    rules = serializers.SerializerMethodField()

    def get_variants(self, obj):
        if not obj.catalog_family or (obj.service_category and obj.service_category.is_gift):
            return []
        from .purchase_benefits import account_service_q
        variants = ServiceItem.objects.filter(
            visible_service_q(), account_service_q(getattr(self.context.get('request'), 'account', None)),
            catalog_family=obj.catalog_family, gameplay_id=obj.gameplay_id,
            game_category_id=obj.game_category_id, is_active=True,
        ).exclude(service_category__is_gift=True).select_related('gameplay', 'game_category', 'service_category', 'required_level').order_by('sort_order', 'id')
        coupon = self.context.get('selected_coupon')
        if coupon:
            from coupons.benefits import applies_to
            variants = [variant for variant in variants if applies_to(coupon.coupon, variant)]
        return ServiceItemSerializer(variants, many=True, context=self.context).data

    def get_rules(self, obj):
        return obj.rules or (obj.gameplay.rules if obj.gameplay_id else '')

    def get_roulette_pool(self, obj):
        from .gameplay import public_pool
        return public_pool(obj.roulette_pool) if obj.roulette_pool else []
    sales_count = serializers.SerializerMethodField()
    is_favorited = serializers.SerializerMethodField()

    class Meta:
        model = ServiceItem
        fields = ServiceItemSerializer.Meta.fields + [
            'rules', 'details_html', 'details_images', 'rules_images', 'introduction_images', 'roulette_pool', 'variants', 'account_fields',
            'id',
            'name',
            'description',
            'price',
            'cover_url',
            'images',
            'highlights',
            'sales_count',
            'is_favorited',
        ]

    def get_sales_count(self, obj):
        # 已完成订单数
        return Order.objects.filter(
            service_id=obj.id,
            status=Order.Status.COMPLETED,
        ).count()

    def get_is_favorited(self, obj):
        request = self.context.get('request')
        if not request or not getattr(request, 'account', None):
            return False
        return ServiceFavorite.objects.filter(
            account=request.account,
            service_id=obj.id,
        ).exists()


class OrderListSerializer(EntityNumberListSerializer):
    def to_representation(self, data):
        from django.db.models import Prefetch, prefetch_related_objects
        from .models import OrderRefund, RefundRecoveryLine
        from wallet.settlement_display import prepare_order_settlement_display
        rows = list(data.all() if hasattr(data, 'all') else data)
        recoveries = RefundRecoveryLine.objects.select_related('wallet', 'reversal_transaction', 'original_transaction')
        refunds = OrderRefund.objects.order_by('-created_at', '-pk').prefetch_related(
            Prefetch('recovery_lines', queryset=recoveries),
        )
        prefetch_related_objects(rows, 'aftersales', 'reservation', Prefetch('refund_records', queryset=refunds))
        account = getattr(self.context.get('request'), 'account', None)
        from .refunds import prepare_provider_refund_evidence
        prepare_provider_refund_evidence(rows, account)
        points_prepared = False
        owned = [row for row in rows if account and account.account_type == 'BOSS' and row.customer_account_id == account.pk]
        if owned:
            prefetch_related_objects(owned, 'direct_payment')
            # A lost marker must not hide a points payment. Batch evidence on
            # lists so old ordinary orders do not incur schema checks per row.
            from engagement.models import PointLedger
            from .models import OrderPointsRedemption
            from django.db import DatabaseError
            from .points_service_payment import points_receipt_table_exists
            ids = [row.pk for row in owned]
            ledger_rows = list(PointLedger.objects.filter(order_id__in=ids, kind='ORDER_POINTS_REDEEM')
                               .values_list('order_id', 'pk'))
            evidence = {order_id for order_id, _ in ledger_rows}
            try:
                has_receipts = points_receipt_table_exists()
            except DatabaseError:
                evidence.update(ids)
                has_receipts = False
            receipts = []
            if has_receipts:
                receipts = list(OrderPointsRedemption.objects.filter(order_id__in=ids)
                                .select_related('payment__wallet', 'ledger'))
                evidence.update(row.order_id for row in receipts)
            self.child.context['_points_evidence_order_ids'] = evidence
            ledger_ids = {}
            for order_id, ledger_id in ledger_rows:
                ledger_ids.setdefault(order_id, set()).add(ledger_id)
            facts = {'receipts':{row.order_id:row for row in receipts}, 'ledger_ids':ledger_ids,
                     'payment_ids':{}, 'reversed_ids':set(), 'mappings':set()}
            self.child.context['_points_display_facts'] = facts
            if receipts:
                from wallet.models import Transaction
                from club_accounts.models import LegacyAccountMap
                receipt_ids = [row.order_id for row in receipts]
                payments = list(Transaction.objects.filter(order_id__in=receipt_ids, tx_type=Transaction.TxType.PAY,
                    status=Transaction.Status.SUCCESS).values_list('order_id', 'pk'))
                payment_ids = {}
                for order_id, payment_id in payments:
                    payment_ids.setdefault(order_id, set()).add(payment_id)
                reversed_ids = set(Transaction.objects.filter(reversal_of_id__in=[row.payment_id for row in receipts])
                                   .values_list('reversal_of_id', flat=True))
                mappings = set(LegacyAccountMap.objects.filter(account_id=account.pk)
                               .values_list('account_id', 'legacy_user_id'))
                facts.update(payment_ids=payment_ids, reversed_ids=reversed_ids, mappings=mappings)
        elif account and rows:
            # Never display a coin-only income if the points marker was lost.
            # Batch independent evidence for list rendering.
            from engagement.models import PointLedger
            from .models import OrderPointsRedemption
            from django.db import DatabaseError
            from .points_service_payment import MARKER, points_receipt_table_exists
            ids = [row.pk for row in rows]
            evidence = set(PointLedger.objects.filter(order_id__in=ids, kind='ORDER_POINTS_REDEEM')
                           .values_list('order_id', flat=True))
            try:
                has_receipts = points_receipt_table_exists()
            except DatabaseError:
                evidence.update(ids)
                has_receipts = False
            if has_receipts:
                evidence.update(OrderPointsRedemption.objects.filter(order_id__in=ids).values_list('order_id', flat=True))
            self.child.context['_points_evidence_order_ids'] = evidence
            for row in rows:
                row._display_points_evidence = (row.pk in evidence or
                    isinstance(row.funding_terms, dict) and MARKER in row.funding_terms)
            points_prepared = True
        request = self.context.get('request')
        if account and account.account_type == 'BOSS':
            from .ownership import owns_order_party
            cancellable = [row for row in rows if row.status == Order.Status.PENDING
                           and row.payment_status == Order.PaymentStatus.PAID
                           and owns_order_party(row, request, 'customer')]
            if cancellable:
                prefetch_related_objects(cancellable, 'used_coupons')
        prepare_order_settlement_display(rows, points_prepared=points_prepared, order_list_only=True)
        from .team_progress import prepare_team_progress
        prepare_team_progress(rows, for_display=True)
        # Load public provider avatars once for the whole list, including the
        # current primary provider and active team members.
        from .models import OrderProvider
        provider_ids = {row.provider_account_id for row in rows if row.provider_account_id}
        provider_ids.update(member.provider_account_id for row in rows for member in getattr(row, 'active_participants', ())
                            if member.provider_account_id)
        provider_ids.update(OrderProvider.objects.filter(order_id__in=[row.pk for row in rows if not hasattr(row, 'active_participants')],
            released_at__isnull=True, provider_account_id__isnull=False).values_list('provider_account_id', flat=True))
        from common.catalog_images import bounded_public_image_url
        self.child.context['_provider_avatar_map'] = {
            pk: bounded_public_image_url(avatar or '') for pk, avatar in
            ClubAccount.objects.filter(pk__in=provider_ids, account_type=ClubAccount.AccountType.PROVIDER)
            .values_list('pk', 'avatar_url')}
        scoped=[row for row in rows if row.provider_requirement_snapshot]
        if scoped:prefetch_related_objects(scoped,'applications__applicant__escort_profile__qualifications__game_category','applications__partner__escort_profile__qualifications__game_category')
        return super().to_representation(rows)


def reservation_origin(reservation):
    """Original appointment for a converted order, never a new schedule."""
    date = serializers.DateTimeField()
    return {'id': reservation.pk, 'reservation_no': reservation.reservation_no,
            'start_time': date.to_representation(reservation.start_time),
            'end_time': date.to_representation(reservation.end_time)}


class OrderSerializer(EntityNumberModelSerializer):
    game_nickname = serializers.SerializerMethodField()
    game_uid = serializers.SerializerMethodField()
    source_order_no = serializers.CharField(source='source_order.order_no', read_only=True, default='')
    report_entry = serializers.SerializerMethodField()
    reservation_origin = serializers.SerializerMethodField()
    settled_at = serializers.DateTimeField(read_only=True)
    settlement_status = serializers.SerializerMethodField()
    pending_deadline_expired = serializers.SerializerMethodField()
    payment_display = serializers.SerializerMethodField()
    points_payment = serializers.SerializerMethodField()

    def _has_points_evidence(self, obj):
        from .points_service_payment import MARKER, display_has_points
        if isinstance(obj.funding_terms, dict) and MARKER in obj.funding_terms:
            return True
        evidence = self.context.get('_points_evidence_order_ids')
        if evidence is not None:
            return obj.pk in evidence
        cache = self.context.setdefault('_points_evidence_cache', {})
        if obj.pk not in cache:
            cache[obj.pk] = display_has_points(obj)
        return cache[obj.pk]

    def _customer_details_visible(self, obj):
        request = self.context.get('request')
        account = getattr(request, 'account', None)
        if not account or account.account_type != ClubAccount.AccountType.PROVIDER:
            return True
        from .ownership import owns_order_party
        # Applicants can still read an application after another team wins.
        # Only an actual current or historical participant gets identity data.
        return owns_order_party(obj, request, 'provider') or self._requester_assignment(obj) is not None

    def get_game_nickname(self, obj):
        return obj.game_nickname if self._customer_details_visible(obj) else ''

    def get_game_uid(self, obj):
        return obj.game_uid if self._customer_details_visible(obj) else ''

    def get_payment_display(self, obj):
        # Staging payment records can contain simulated channel evidence. They
        # must never be represented as confirmed real-world WeChat payments.
        account = getattr(self.context.get('request'), 'account', None)
        if not account or account.account_type != 'BOSS' or obj.customer_account_id != account.pk:
            return None
        try:
            intent = obj.direct_payment
        except ObjectDoesNotExist:
            return None
        return {'kind': 'DIRECT_TEST', 'state': 'REVIEW' if intent.status == 'REVIEW' else 'UNCONFIRMED'}

    def get_points_payment(self, obj):
        """Expose only the customer's verified original redemption, never a guess."""
        account = getattr(self.context.get('request'), 'account', None)
        if not account or account.account_type != 'BOSS' or obj.customer_account_id != account.pk:
            return None
        from .points_service_payment import MARKER
        terms = obj.funding_terms
        if not isinstance(terms, dict) or MARKER not in terms:
            present = self._has_points_evidence(obj)
            return {'state': 'REVIEW'} if present else None
        review = {'state': 'REVIEW'}
        marker = terms[MARKER]
        if (not isinstance(marker, dict) or set(marker) != {'receipt_id'}
                or type(marker['receipt_id']) is not int or marker['receipt_id'] <= 0):
            return review
        from .models import OrderPointsRedemption
        facts = self.context.get('_points_display_facts')
        if facts is not None:
            receipt = facts['receipts'].get(obj.pk)
            if receipt and receipt.pk != marker['receipt_id']:
                receipt = None
        else:
            from stores.db import transaction
            connection = transaction.get_connection()
            with connection.cursor() as cursor:
                if OrderPointsRedemption._meta.db_table not in connection.introspection.table_names(cursor):
                    return review
            receipt = (OrderPointsRedemption.objects.filter(pk=marker['receipt_id'], order_id=obj.pk)
                       .select_related('payment__wallet', 'ledger').first())
        if not receipt:
            return review
        try:
            if obj.direct_payment is not None:
                return review
        except ObjectDoesNotExist:
            pass
        from engagement.models import PointLedger
        from wallet.models import Transaction
        from club_accounts.models import LegacyAccountMap
        payment, ledger = receipt.payment, receipt.ledger
        if facts is not None:
            reversed_payment = payment.pk in facts['reversed_ids']
            mapping_exists = (account.pk, obj.customer_id) in facts['mappings']
            extra_redemption = bool(facts['ledger_ids'].get(obj.pk, set()) - {ledger.pk})
            extra_payment = bool(facts['payment_ids'].get(obj.pk, set()) - {payment.pk})
        else:
            reversed_payment = payment.reversals.exists()
            mapping_exists = obj.customer_id is None or LegacyAccountMap.objects.filter(
                account_id=account.pk, legacy_user_id=obj.customer_id).exists()
            extra_redemption = PointLedger.objects.filter(order_id=obj.pk, kind='ORDER_POINTS_REDEEM').exclude(pk=ledger.pk).exists()
            extra_payment = Transaction.objects.filter(order_id=obj.pk, tx_type=Transaction.TxType.PAY,
                status=Transaction.Status.SUCCESS).exclude(pk=payment.pk).exists()
        if (not 0 < receipt.points_hundredths <= 2147483647 or receipt.groups <= 0
                or receipt.discount_units <= 0 or receipt.paid_units < 10
                or receipt.paid_units != obj.amount or receipt.deal_units != receipt.paid_units + receipt.discount_units
                or receipt.deal_units != obj.original_amount - obj.boss_discount - obj.promo_discount - obj.coupon_discount
                or receipt.points_hundredths != receipt.groups * receipt.group_points_hundredths
                or receipt.discount_units != receipt.groups * receipt.group_coin_units
                or payment.order_id != obj.pk or payment.wallet.account_id != account.pk
                or payment.wallet.user_id != obj.customer_id or payment.tx_type != Transaction.TxType.PAY
                or payment.status != Transaction.Status.SUCCESS or payment.amount != -receipt.paid_units
                or payment.balance_after != payment.balance_before + payment.amount or payment.reversal_of_id
                or reversed_payment or ledger.order_id != obj.pk or ledger.account_id != account.pk
                or ledger.kind != 'ORDER_POINTS_REDEEM' or ledger.amount != -receipt.points_hundredths
                or ledger.event_key != f'order:{obj.pk}:points-redemption'
                or (obj.customer_id is not None and not mapping_exists) or extra_redemption or extra_payment):
            return review
        return {'state': 'CONFIRMED', 'points_hundredths': receipt.points_hundredths,
                'discount_units': receipt.discount_units}

    def get_reservation_origin(self, obj):
        account = getattr(self.context.get('request'), 'account', None)
        if not account or account.account_type not in ('BOSS', 'PROVIDER'):
            return None
        try:
            reservation = obj.reservation
        except ObjectDoesNotExist:
            return None
        if account.account_type == 'BOSS':
            if reservation.customer_account_id != account.pk:
                return None
            can_open = True
        else:
            assignments = getattr(obj, 'requester_assignments', None)
            member = (bool(assignments) if assignments is not None else
                      obj.providers.filter(provider_account=account).exists())
            visible_pool = (self.context.get('_provider_pool_view') and obj.status == 'PENDING'
                            and obj.dispatch_route == 'PUBLIC' and obj.payment_status == 'PAID')
            if obj.provider_account_id != account.pk and not member and not visible_pool:
                return None
            can_open = reservation.provider_account_id == account.pk or (
                reservation.status == 'CONVERTED' and obj.providers.filter(
                    provider_account=account, released_at__isnull=True).exists())
        return {**reservation_origin(reservation), 'can_open': can_open}

    def get_pending_deadline_expired(self, obj):
        deadlines = self.context.get('_provider_pool_deadlines', {})
        if obj.pk in deadlines:
            return deadlines[obj.pk]
        now = self.context.get('_pending_deadline_now')
        if now is None:
            now = self.context['_pending_deadline_now'] = timezone.now()
        from .pool_visibility import pending_deadline_expired
        cache = self.context.setdefault('_pending_deadlines', {})
        if obj.pk not in cache:
            cache[obj.pk] = pending_deadline_expired(obj, now=now)
        return cache[obj.pk]

    def _verified_person_credit(self, obj, account_id):
        if getattr(obj, '_provider_has_manifest', None) is False:
            return False, None
        from .segment_settlement import verified_person_credit
        cache = self.context.setdefault('_verified_person_credits', {})
        key = (obj.pk, account_id)
        if key not in cache:
            cache[key] = verified_person_credit(obj, account_id)
        return cache[key]

    def get_settlement_status(self, obj):
        from wallet.settlement_display import order_settlement_status
        from .segment_financial_capture import transfer_income_pending
        account = getattr(self.context.get('request'), 'account', None)
        account_id = account.pk if account and account.account_type == ClubAccount.AccountType.PROVIDER else None
        segmented, amount = self._verified_person_credit(obj, account_id)
        if segmented:
            return 'SETTLED' if amount is not None else 'REVIEW'
        if transfer_income_pending(obj, account_id):
            return 'REVIEW'
        return order_settlement_status(obj)
    cancel_coupon_nonrefundable = serializers.SerializerMethodField()
    def get_cancel_coupon_nonrefundable(self, obj):
        request = self.context.get('request')
        account = getattr(request, 'account', None)
        from .ownership import owns_order_party
        if (not account or account.account_type != ClubAccount.AccountType.BOSS
                or obj.status != Order.Status.PENDING or obj.payment_status != Order.PaymentStatus.PAID
                or not owns_order_party(obj, request, 'customer')):
            return None
        return any(row.status == 'USED' and not row.restore_on_refund for row in obj.used_coupons.all())

    can_aftersale = serializers.SerializerMethodField()
    completion_block_reason = serializers.SerializerMethodField()
    refund_summary = serializers.SerializerMethodField()
    def get_completion_block_reason(self, obj):
        if obj.status != Order.Status.IN_SERVICE:
            return ''
        from .refunds import negotiated_aftersale_blocks_completion
        request = self.context.get('request')
        account = getattr(request, 'account', None)
        if account and account.account_type == ClubAccount.AccountType.PROVIDER:
            legacy_id = getattr(getattr(request, 'legacy_user', None), 'pk', None)
            participants = (obj.active_participants if hasattr(obj, 'active_participants')
                            else obj.providers.all())
            active = any(row.released_at is None and
                         (row.provider_account_id == account.pk or
                          (not row.provider_account_id and legacy_id and row.provider_id == legacy_id))
                         for row in participants)
            if not active:
                return ''
        return ('售后处理中，暂不能结单，请等待客服处理'
                if negotiated_aftersale_blocks_completion(obj) else '')
    def get_can_aftersale(self,obj):
        account = getattr(self.context.get('request'), 'account', None)
        if account and account.account_type == ClubAccount.AccountType.PROVIDER:
            return False
        from .refunds import can_request_aftersale
        return can_request_aftersale(obj)
    def get_refund_summary(self,obj):
        from .refunds import refund_summary, authorized_refund
        request = self.context.get('request')
        account = getattr(request,'account',None)
        from .ownership import owns_order_party
        owner = bool(account and account.account_type == ClubAccount.AccountType.BOSS
                     and owns_order_party(obj, request, 'customer'))
        provider = bool(account and account.account_type == ClubAccount.AccountType.PROVIDER
                        and owns_order_party(obj, request, 'provider'))
        return refund_summary(obj,detailed=authorized_refund(account),account=account,
                              customer_owner=owner,provider_owner=provider)
    dispatch_notification_enabled = serializers.SerializerMethodField()
    def get_dispatch_notification_enabled(self,obj):
        request=self.context.get('request');account=getattr(request,'account',None)
        if not account or account.account_type!='PROVIDER':return False
        from users.qualifications import dispatch_notification_enabled
        if '_provider_notification_profile' not in self.context:
            from users.models import EscortProfile
            state=self.context.get('_provider_read_state')
            self.context['_provider_notification_profile']=state[0] if state else EscortProfile.objects.filter(account=account).prefetch_related('qualifications__game_category').first()
        return dispatch_notification_enabled(self.context['_provider_notification_profile'],obj)

    provider_requirement = serializers.SerializerMethodField()
    def get_provider_requirement(self,obj):
        from users.qualifications import public_requirement
        return public_requirement(obj)
    aftersale_deadline = serializers.SerializerMethodField()
    aftersale_status = serializers.SerializerMethodField()
    aftersale_scope_selection_available = serializers.SerializerMethodField()

    def get_aftersale_scope_selection_available(self, obj):
        request = self.context.get('request')
        account = getattr(request, 'account', None)
        return bool(account and account.account_type == ClubAccount.AccountType.BOSS
                    and obj.status == Order.Status.COMPLETED and not obj.is_gift_snapshot
                    and obj.service_clock_version is not None)
    def get_aftersale_deadline(self, obj):
        from engagement.confirmation import aftersale_deadline
        return aftersale_deadline(obj)
    def get_aftersale_status(self, obj):
        request = self.context.get('request')
        account = getattr(request, 'account', None)
        if account and account.account_type == ClubAccount.AccountType.PROVIDER:
            from .aftersale_scope import visible_status
            return visible_status(obj, account.pk)
        return next((row.status for row in obj.aftersales.all()), '')

    service = ServiceItemSerializer(read_only=True)
    customer = serializers.SerializerMethodField()
    provider = serializers.SerializerMethodField()
    provider_display_name_snapshot = serializers.SerializerMethodField()
    is_evaluated = serializers.SerializerMethodField()
    can_operate = serializers.SerializerMethodField()
    can_reject = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    expected_income = serializers.SerializerMethodField()
    evaluation = serializers.SerializerMethodField()
    evaluations = serializers.SerializerMethodField()
    evaluation_targets = serializers.SerializerMethodField()
    support_contact = serializers.SerializerMethodField()
    fulfillment = serializers.SerializerMethodField()
    applications = serializers.SerializerMethodField()
    my_applications = serializers.SerializerMethodField()
    participants = serializers.SerializerMethodField()
    team_progress = serializers.SerializerMethodField()
    my_assignment_released = serializers.SerializerMethodField()
    my_assignment_settled = serializers.SerializerMethodField()
    my_assignment_departure = serializers.SerializerMethodField()
    required_team_size = serializers.SerializerMethodField()
    can_team_apply = serializers.SerializerMethodField()
    audition_recordings_required = serializers.SerializerMethodField()
    quantity_unit = serializers.SerializerMethodField()
    my_participation_segments = serializers.SerializerMethodField()
    my_pending_work_stint = serializers.SerializerMethodField()

    def get_my_pending_work_stint(self, obj):
        account = getattr(self.context.get('request'), 'account', None)
        if not account or account.account_type != ClubAccount.AccountType.PROVIDER:
            return None
        if getattr(obj, '_provider_display_account_id', None) == account.pk:
            stint_id = obj._provider_pending_work_stint
            return {'id': stint_id} if not obj._provider_has_attribution and stint_id else None
        from .models import OrderOriginalAttribution, OrderWorkStint
        if OrderOriginalAttribution.objects.filter(order=obj).exists():
            return None
        row = OrderWorkStint.objects.filter(order=obj, account=account, incoming__isnull=False,
            started_at__isnull=True, released_at__isnull=True).first()
        return {'id': row.pk} if row else None

    def _my_participation(self, obj):
        account = getattr(self.context.get('request'), 'account', None)
        if not account or account.account_type != ClubAccount.AccountType.PROVIDER:
            return []
        if (getattr(obj, '_provider_display_account_id', None) == account.pk
                and not obj._provider_has_segments):
            return []
        cache = self.context.setdefault('_my_participation_segments', {})
        key = (obj.pk, account.pk)
        if key not in cache:
            from django.db.models import Exists, OuterRef
            from .models import OrderParticipationSegment, OrderOriginalShareTransfer
            cache[key] = list(OrderParticipationSegment.objects.filter(attribution__order=obj,
                provider_account=account).select_related('attribution').annotate(
                    is_service_transfer=Exists(OrderOriginalShareTransfer.objects.filter(next_segment_id=OuterRef('pk'))))
                .order_by('pk'))
        return cache[key]

    def get_my_participation_segments(self, obj):
        from common.private_media import private_media_url
        request = self.context.get('request')
        rows = self._my_participation(obj)
        if not rows:
            return []
        can_upload_completion = False
        account = getattr(request, 'account', None)
        released_to_report = any(row.started_at and row.released_at and row.attribution.status == 'ACTIVE' for row in rows)
        if account and released_to_report:
            from wallet.models import ProviderReport
            from .services import report_edit_error
            report = ProviderReport.objects.filter(order=obj, provider_account=account).first()
            can_upload_completion = bool(report and not report_edit_error(report))
        from . import segment_reports
        if self.context.get('verify_segment_report_detail'):
            segment_reporting, transfer_upload = segment_reports.detail_support(
                obj, check_paid_transfer=released_to_report and not can_upload_completion)
            can_upload_completion = can_upload_completion or transfer_upload
        else:
            segment_reporting = segment_reports.supports(obj)
        if segment_reporting and not can_upload_completion:
            from .points_transfer_capture import enabled as points_transfer_enabled
            from .points_service_payment import needs_integrated_processing
            if points_transfer_enabled() and needs_integrated_processing(obj):
                # A transfer scope verifies completion per segment without a legacy report.
                can_upload_completion = True
        reports = {row.pk: segment_reports.report_data(row, request) for row in rows} if segment_reporting else {}
        from .participation_entry import negotiated_current_completion_allowed
        negotiated_current = {row.pk for row in rows if not row.released_at and negotiated_current_completion_allowed(obj, row)}
        return [{'id': row.pk, 'accepted_at': row.accepted_at, 'started_at': row.started_at,
                 'released_at': row.released_at, 'is_service_transfer': row.is_service_transfer,
                 'entry_image_url': private_media_url(request, row.entry_image) if row.entry_image else '',
                 'completion_image_url': private_media_url(request, row.completion_image) if row.completion_image else '',
                 'can_upload_completion': bool((reports[row.pk]['status'] in ('DRAFT','REJECTED')
                                               if reports.get(row.pk) else (can_upload_completion or row.pk in negotiated_current))
                                               and row.started_at and (row.released_at or row.pk in negotiated_current)
                                               and row.attribution.status == 'ACTIVE'),
                 'segment_report_available': bool(segment_reporting and row.started_at and row.attribution.status == 'ACTIVE'
                                                  and (not row.released_at or row.original_share > 0)),
                 'segment_report': reports.get(row.pk)}
                for row in rows]

    def get_quantity_unit(self, obj):
        snapshot = obj.gameplay_snapshot
        unit = snapshot.get('price_unit') if isinstance(snapshot, dict) else None
        # Historical orders without a saved unit must not inherit catalog edits.
        return unit.strip() if isinstance(unit, str) and unit.strip() else '份'

    def get_required_team_size(self, obj):
        from .team_rules import required_team_size
        return required_team_size(obj)

    def get_can_team_apply(self, obj):
        return self.get_required_team_size(obj) == 2

    def get_audition_recordings_required(self, obj):
        from .applications import audition_recordings_required
        return audition_recordings_required(obj)

    def get_applications(self, obj):
        from .applications import applications_data
        return applications_data(obj, self.context.get('request'),
                                 validate_readiness=self.context.get('validate_application_readiness', False))

    def get_my_applications(self, obj):
        from .applications import my_applications_data
        return my_applications_data(obj, self.context.get('request'))

    def get_participants(self, obj):
        from stores.display import provider_display_name
        rows = [row for row in (obj.active_participants if hasattr(obj, 'active_participants') else obj.providers.all())
                if row.released_at is None]
        avatars = self._provider_avatars(row.provider_account_id for row in rows)
        return [{'id': row.provider_account_id, 'nickname': row.provider_name_snapshot,
                 'display_name': provider_display_name(row.provider_name_snapshot),
                 'avatar_url': avatars.get(row.provider_account_id, '')} for row in rows]

    def _provider_avatars(self, account_ids):
        from common.catalog_images import bounded_public_image_url
        cache = self.context.setdefault('_provider_avatar_map', {})
        missing = {account_id for account_id in account_ids if account_id and account_id not in cache}
        if missing:
            cache.update({account_id: '' for account_id in missing})
            cache.update({pk: bounded_public_image_url(avatar or '') for pk, avatar in
                          ClubAccount.objects.filter(pk__in=missing, account_type=ClubAccount.AccountType.PROVIDER)
                          .values_list('pk', 'avatar_url')})
        return cache

    def get_provider_display_name_snapshot(self, obj):
        from stores.display import provider_display_name
        return provider_display_name(obj.provider_name_snapshot)

    def get_team_progress(self, obj):
        from .team_progress import team_progress
        from stores.display import provider_display_name
        progress = team_progress(obj, for_display=True)
        if progress is None:
            return None
        avatars = self._provider_avatars(member['id'] for member in progress['members'])
        return {**progress, 'members': [{**member, 'display_name': provider_display_name(member['nickname']),
                                       'avatar_url': avatars.get(member['id'], '')}
                                      for member in progress['members']]}

    def _requester_assignment(self, obj):
        request = self.context.get('request')
        account = getattr(request, 'account', None)
        if not account or account.account_type != ClubAccount.AccountType.PROVIDER:
            return None
        if hasattr(obj, 'requester_assignments'):
            assignment = next(iter(obj.requester_assignments), None)
        else:
            owner = Q(provider_account=account)
            legacy_user = getattr(request, 'legacy_user', None)
            if legacy_user:
                owner |= Q(provider_account__isnull=True, provider=legacy_user)
            assignment = obj.providers.filter(owner).only('provider_id', 'provider_account_id',
                'released_at', 'settled_at', 'settlement_base', 'provider_income').first()
        return assignment

    def get_my_assignment_released(self, obj):
        assignment = self._requester_assignment(obj)
        return bool(assignment.released_at) if assignment else None

    def get_my_assignment_settled(self, obj):
        account = getattr(self.context.get('request'), 'account', None)
        account_id = account.pk if account and account.account_type == ClubAccount.AccountType.PROVIDER else None
        segmented, amount = self._verified_person_credit(obj, account_id)
        if segmented:
            return amount is not None
        assignment = self._requester_assignment(obj)
        return bool(assignment.settled_at) if assignment else None

    def get_my_assignment_departure(self, obj):
        assignment = self._requester_assignment(obj)
        if not assignment or not assignment.released_at:
            return None
        request = self.context['request']
        if hasattr(obj, 'requester_rejections'):
            rejected = any(log.created_at >= assignment.released_at for log in obj.requester_rejections)
        else:
            actor = Q(operator_account=request.account)
            if getattr(request, 'legacy_user', None):
                actor |= Q(operator_account__isnull=True, operator=request.legacy_user)
            rejected = obj.status_logs.filter(actor, action=OrderStatusLog.Action.REJECT,
                                              created_at__gte=assignment.released_at).exists()
        if rejected:
            return 'REJECTED'
        if assignment.settlement_base > 0 or assignment.provider_income > 0:
            return 'TRANSFERRED'
        return 'UNKNOWN'


    def get_fulfillment(self, obj):
        from .gameplay import fulfillment_text
        return fulfillment_text(obj)

    class Meta:
        model = Order
        list_serializer_class = OrderListSerializer
        fields = [
            'dispatch_notification_enabled','provider_requirement',
            'id', 'reservation_origin',
            'dispatch_route', 'requires_boss_selection', 'audition_recordings_required', 'escort_mode', 'applications', 'my_applications', 'participants', 'team_progress', 'my_assignment_released', 'my_assignment_settled', 'my_assignment_departure', 'my_participation_segments', 'my_pending_work_stint',
            'required_provider_count', 'required_team_size', 'can_team_apply',
            'order_no', 'gameplay_snapshot', 'roulette_result', 'fulfillment',
            'service_name_snapshot', 'unit_price_snapshot', 'provider_name_snapshot', 'provider_display_name_snapshot',
            'status',
            'payment_status',
            'payment_display', 'points_payment',
            'amount',
            'original_amount',
            'boss_discount',
            'promo_discount',
            'coupon_discount', 'cancel_coupon_nonrefundable',
            'expected_income', 'settlement_status', 'settlement_mode',
            'commission_type', 'commission_fixed', 'commission_override', 'commission_source', 'commission_rate', 'commission_allocation_pending',
            'game_rounds',
            'quantity_unit',
            'game_region',
            'game_nickname',
            'game_uid',
            'provider_gender',
            'remark',
            'support_contact',
            'source_order', 'source_order_no', 'is_gift_snapshot',
            'service',
            'customer',
            'provider',
            'is_evaluated',
            'evaluation', 'evaluations', 'evaluation_targets',
            'report_entry', 'can_operate',
            'can_reject',
            'allowed_actions',
            'grabbed_at',
            'in_service_at',
            'completed_at', 'settled_at', 'business_cycle', 'experience_kind', 'report_ready_at', 'consumption_ready_at', 'consumption_confirmed_at', 'aftersale_deadline', 'aftersale_status', 'aftersale_scope_selection_available', 'can_aftersale', 'completion_block_reason', 'refund_summary', 'pending_deadline_expired',
            'cancelled_at',
            'refunded_at',
            'cancel_reason',
            'auto_cancel_at', 'pending_timeout_minutes',
            'reject_count',
            'created_at',
            'updated_at',
        ]

    def _evaluations(self, obj):
        if not hasattr(obj, '_evaluation_rows'):
            obj._evaluation_rows = list(obj.evaluations.all())
        return obj._evaluation_rows

    def _evaluation_targets(self, obj):
        if not hasattr(obj, '_evaluation_targets'):
            from .evaluation_targets import evaluation_targets
            obj._evaluation_targets = evaluation_targets(obj)
        return obj._evaluation_targets

    def get_is_evaluated(self, obj):
        rows = self._evaluations(obj)
        targets = self._evaluation_targets(obj)
        return bool(targets) and all(any(row.provider_id == target['provider_legacy_id'] for row in rows) for target in targets)

    def get_evaluation_targets(self, obj):
        from stores.display import provider_display_name
        account = getattr(self.context.get('request'), 'account', None)
        if not account or account.account_type != ClubAccount.AccountType.BOSS:
            return []
        rows = self._evaluations(obj)
        return [{**target, 'display_name': provider_display_name(target['name']),
                 'reviewed': any(row.provider_id == target['provider_legacy_id'] for row in rows)}
                for target in self._evaluation_targets(obj)]

    def get_evaluations(self, obj):
        from stores.display import provider_display_name
        rows = [row for row in self._evaluations(obj) if row.voided_at is None]
        request = self.context.get('request')
        account = getattr(request, 'account', None)
        if account and account.account_type == ClubAccount.AccountType.PROVIDER:
            legacy = getattr(request, 'legacy_user', None)
            rows = [row for row in rows if row.provider_account_id == account.pk or
                    (not row.provider_account_id and legacy and row.provider_id == legacy.pk)]
        names = {target['provider_legacy_id']: target['name'] for target in self._evaluation_targets(obj)}
        return [{
            'id': row.id, 'provider_account_id': row.provider_account_id, 'provider_legacy_id': row.provider_id,
            'provider_name': names.get(row.provider_id, '陪玩'),
            'provider_display_name': provider_display_name(names.get(row.provider_id, '陪玩')),
            'score': row.score, 'skill_score': row.skill_score, 'attitude_score': row.attitude_score,
            'communication_score': row.communication_score, 'content': row.content,
            'reply_content': row.reply_content, 'reply_author': row.reply_author, 'replied_at': row.replied_at, 'created_at': row.created_at,
        } for row in rows]

    def get_evaluation(self, obj):
        """订单列表内返回评价摘要，让老板能直接看到陪玩回复。"""
        rows = self.get_evaluations(obj)
        return rows[0] if rows else None

    def get_can_operate(self, obj):
        request = self.context.get('request')
        from .ownership import owns_order_party
        return bool(
            request and getattr(request, 'account', None)
            and request.account.account_type == ClubAccount.AccountType.PROVIDER
            and owns_order_party(obj, request, 'provider')
            and obj.status in (Order.Status.GRABBED, Order.Status.IN_SERVICE)
            and self._points_fulfillment_available(obj, request)
        )

    def _points_fulfillment_available(self, obj, request):
        if not self._has_points_evidence(obj):
            return True
        if obj.room_required_snapshot is True and obj.payment_status == Order.PaymentStatus.PAID:
            from .team_progress import formal_transfer_display_enabled
            if formal_transfer_display_enabled():
                return self._paid_points_room_fulfillment_available(obj, request)
        if (obj.status == Order.Status.IN_SERVICE and obj.payment_status == Order.PaymentStatus.PAID
                and self._formal_paid_points_transfer(obj)):
            return self._paid_transfer_completion_available(obj, request)
        if obj.status == Order.Status.IN_SERVICE and obj.payment_status == Order.PaymentStatus.PARTIALLY_REFUNDED:
            from .team_progress import team_progress
            progress = team_progress(obj, for_display=True)
            if hasattr(obj, '_formal_transfer_progress'):
                # A released seat cannot use the retained seat's order action.
                return bool(request.legacy_user and progress and any(
                    member['id'] == request.account.pk for member in progress['members']) and any(
                    segment.started_at and not segment.released_at and segment.attribution.status == 'ACTIVE'
                    for segment in self._my_participation(obj)))
        from .points_funding_plan import PointsFundingPlanError
        from .points_report_test import verified_report_snapshot
        try:
            verified_report_snapshot(obj, request.account.pk, getattr(getattr(request, 'legacy_user', None), 'pk', None))
        except PointsFundingPlanError:
            return False
        return True

    def _formal_paid_points_transfer(self, obj):
        from .team_progress import formal_transfer_display_enabled
        if (not formal_transfer_display_enabled() or obj.source_order_id or obj.is_gift_snapshot
                or obj.room_required_snapshot is True):
            return False
        if not hasattr(obj, '_provider_has_points_transfer'):
            from .segment_financial_capture import covered_transfers
            obj._provider_has_points_transfer = covered_transfers(obj.pk).filter(
                previous_segment__attribution__financial_coverage__source='WALLET_POINTS').exists()
        return obj._provider_has_points_transfer

    def _current_points_participation(self, obj, request):
        assignment = self._requester_assignment(obj)
        user = request.legacy_user
        if (not user or not assignment or assignment.released_at or assignment.provider_id != user.pk
                or assignment.provider_account_id != request.account.pk):
            return None
        segments = [row for row in self._my_participation(obj) if row.released_at is None]
        if len(segments) == 1 and segments[0].attribution.status == 'ACTIVE':
            return segments[0]
        return None

    def _paid_points_room_fulfillment_available(self, obj, request):
        from .points_service_payment import MARKER
        if not isinstance(obj.funding_terms, dict) or MARKER not in obj.funding_terms:
            return False
        segment = self._current_points_participation(obj, request)
        if not segment:
            return False
        if obj.status == Order.Status.GRABBED:
            return segment.started_at is None
        if not settings.ROOM_COMPLETION_REVIEW_ENABLED or segment.started_at is None:
            return False
        if obj.escort_mode == Order.EscortMode.DOUBLE and obj.requires_boss_selection:
            from .team_progress import team_progress
            progress = team_progress(obj, for_display=True)
            return bool(progress and any(member['id'] == request.account.pk and not member['submitted']
                                         for member in progress['members']))
        return not segment.completion_image

    def _paid_transfer_completion_available(self, obj, request):
        segment = self._current_points_participation(obj, request)
        if not segment or segment.started_at is None:
            return False
        if obj.escort_mode == Order.EscortMode.DOUBLE and obj.requires_boss_selection:
            from .team_progress import team_progress
            progress = team_progress(obj, for_display=True)
            return bool(progress and any(member['id'] == request.account.pk for member in progress['members']))
        from .ownership import owns_order_party
        return owns_order_party(obj, request, 'provider')

    def get_report_entry(self, obj):
        account = getattr(self.context.get('request'), 'account', None)
        if not account or account.account_type != ClubAccount.AccountType.PROVIDER:
            return None
        from .report_eligibility import report_entry_error
        reason = report_entry_error(obj, account, legacy_user=getattr(self.context.get('request'), 'legacy_user', None),
                                    points_evidence={obj.pk} if self._has_points_evidence(obj) else set())
        return {'available': not reason, 'reason': reason}

    def get_can_reject(self, obj):
        # Once assigned, any change must be coordinated by staff. Keep the
        # field for old clients while withdrawing the action consistently.
        return False

    def get_allowed_actions(self, obj):
        """后端给出当前登录人可执行动作，三端只负责按此渲染按钮。"""
        request = self.context.get('request')
        if not request or not getattr(request, 'account', None):
            return []
        account = request.account
        user = request.legacy_user
        actions = []
        from .ownership import owns_order_party
        points = self._has_points_evidence(obj)
        if points:
            from .points_checkout_test import assignment_enabled
            can_assign = assignment_enabled()
        else:
            can_assign = True

        if (
            account.account_type == ClubAccount.AccountType.BOSS
            and owns_order_party(obj, request, 'customer')
        ):
            if obj.status == Order.Status.PENDING and obj.payment_status == Order.PaymentStatus.PAID:
                actions.append('CANCEL')
                if (can_assign and obj.requires_boss_selection and obj.dispatch_route == Order.DispatchRoute.PUBLIC
                        and self.get_pending_deadline_expired(obj) is False):
                    actions.append('SELECT_PROVIDER')
            is_gift = obj.is_gift_snapshot
            if obj.status == Order.Status.COMPLETED and not is_gift and self._evaluation_targets(obj) and not self.get_is_evaluated(obj):
                actions.append('EVALUATE')
            if obj.status == Order.Status.COMPLETED and obj.payment_status in (Order.PaymentStatus.PAID,Order.PaymentStatus.PARTIALLY_REFUNDED) and not is_gift and (obj.provider_id or obj.escort_mode == Order.EscortMode.DOUBLE):
                actions.append('TIP')
            return actions

        if account.account_type == ClubAccount.AccountType.PROVIDER:
            if obj.status == Order.Status.PENDING and obj.provider_account_id is None:
                # The list serializer shares this request-local context across
                # rows. Actual grab/dispatch still rechecks under its row lock.
                if '_provider_read_state' not in self.context:
                    from .services import count_capacity_orders
                    from .capacity import current_capacity_limit
                    profile = getattr(account, 'escort_profile', None)
                    self.context['_provider_read_state'] = (
                        profile,
                        bool(profile and not profile.order_acceptance_error(allow_refund_recovery=True)),
                        count_capacity_orders(account),
                        profile.order_visibility_delay_seconds if profile else 0,
                    )
                if '_provider_capacity_limit' not in self.context:
                    from .capacity import current_capacity_limit
                    self.context['_provider_capacity_limit'] = current_capacity_limit()
                capacity_limit = self.context['_provider_capacity_limit']
                profile, available, active_count, delay = self.context['_provider_read_state']
                from .paid_service_scope import acceptance_error
                from .grab_single_flow import has_admitting_round
                from .models import OrderGrabPoolSnapshot
                if (
                    can_assign and available
                    and not acceptance_error(profile, obj, allow_refund_recovery=True,
                                             refund_deficits=self.context.get('_provider_pool_refund_deficits'))
                    and (obj.provider_gender == "ANY" or obj.provider_gender == profile.gender)
                    and obj.payment_status == Order.PaymentStatus.PAID
                    and (obj.dispatch_route == Order.DispatchRoute.PUBLIC
                         or (obj._pool_snapshot_exists if hasattr(obj, '_pool_snapshot_exists')
                             else OrderGrabPoolSnapshot.objects.filter(order=obj).exists()))
                    and obj.provider_id is None
                    and (obj.escort_mode == Order.EscortMode.SINGLE
                         or (obj.escort_mode == Order.EscortMode.DOUBLE
                             and self.get_audition_recordings_required(obj)))
                    and not obj.source_order_id
                    and not obj.is_gift_snapshot
                    and (obj.requires_boss_selection or self.context.get('_provider_pool_view')
                         or has_admitting_round(obj.pk))
                    and self.get_pending_deadline_expired(obj) is False
                ):
                    visibility_times = self.context.get('_provider_pool_visibility')
                    if visibility_times is not None:
                        visible_at = (visibility_times.get(obj.pk) if obj._pool_snapshot_exists
                                      else (obj.pool_entered_at or obj.created_at) + timedelta(seconds=delay))
                    else:
                        from .grab_pool_visibility import order_visibility_time
                        try:
                            visible_at = order_visibility_time(obj, profile)
                        except ValueError:
                            visible_at = None
                    if (active_count < capacity_limit and visible_at is not None
                            and timezone.now() >= visible_at):
                        # A malformed direct-payment quote can pass the pool's
                        # coarse SQL filter. Do not offer an action that the
                        # locked transition/application check will refuse.
                        from .direct_payment_models import DirectPaymentIntent
                        from .direct_payment_quote import quote_matches_intent
                        try:
                            intent = obj.direct_payment
                        except ObjectDoesNotExist:
                            intent = None
                        if intent is None or (intent.status == DirectPaymentIntent.Status.PAID
                                              and quote_matches_intent(intent, obj)):
                            actions.append('GRAB')
            assignment_owner = Q(provider_account=account)
            participant = owns_order_party(obj, request, 'provider') or (any(
                row.provider_account_id == account.pk
                for row in obj.active_participants) if hasattr(obj,'active_participants') else obj.providers.filter(
                    assignment_owner, released_at__isnull=True).exists())
            if participant and obj.status == Order.Status.GRABBED and self._points_fulfillment_available(obj, request):
                from .team_fulfillment import team_evidence_submitted
                if obj.escort_mode != 'DOUBLE' or not team_evidence_submitted(obj,account,'entry_image'):
                    actions.append('START')
            if participant and obj.status == Order.Status.IN_SERVICE:
                segments = self._my_participation(obj)
                pending = [segment for segment in segments if segment.attribution.status == 'ACTIVE'
                           and segment.is_service_transfer and not segment.released_at and not segment.started_at]
                if (points and obj.payment_status == Order.PaymentStatus.PAID and len(pending) == 1 and user
                        and sum(segment.released_at is None for segment in segments) == 1):
                    assignment = self._requester_assignment(obj)
                    if (assignment and not assignment.released_at and assignment.provider_account_id == account.pk
                            and assignment.provider_id == user.pk):
                        # The transfer has recorded the incoming seat. Its entry
                        # action does not use the legacy report snapshot.
                        return ['START_PARTICIPATION']
                if not self._points_fulfillment_available(obj, request):
                    return actions
                from .team_fulfillment import team_evidence_submitted
                pending_work = self.get_my_pending_work_stint(obj)
                if len(pending) == 1 or pending_work:
                    actions.append('START_PARTICIPATION')
                if not pending and not pending_work and not self.get_completion_block_reason(obj) and (obj.escort_mode != 'DOUBLE' or not team_evidence_submitted(obj,account,'completion_image')):
                    actions.append('COMPLETE')
            return actions

        if account.account_type == ClubAccount.AccountType.STAFF:
            if obj.status == Order.Status.PENDING:
                actions.extend(['ASSIGN', 'CANCEL'])
            elif obj.status == Order.Status.GRABBED and not self._has_points_evidence(obj):
                actions.extend(['START', 'REFUND'])
            elif obj.status == Order.Status.IN_SERVICE and not points:
                actions.extend(['REFUND'] if self.get_completion_block_reason(obj) else ['COMPLETE', 'REFUND'])
        return actions

    def _income_identity_matches(self, provider_id, provider_account_id):
        """Both stored identities must identify the requesting provider.

        Account-only rows remain valid. When a legacy user is also stored, a
        conflicting account column must never expose that user's income.
        """
        request = self.context.get('request')
        account = getattr(request, 'account', None)
        if not account or (provider_account_id is not None and provider_account_id != account.pk):
            return False
        if provider_id is not None:
            legacy_user = getattr(request, 'legacy_user', None)
            if legacy_user is not None:
                return provider_id == legacy_user.pk
            if '_income_legacy_user_id' not in self.context:
                from club_accounts.models import LegacyAccountMap
                self.context['_income_legacy_user_id'] = LegacyAccountMap.objects.filter(
                    account_id=account.pk,
                ).values_list('legacy_user_id', flat=True).first()
            return provider_id == self.context['_income_legacy_user_id']
        return provider_account_id == account.pk

    def get_expected_income(self, obj):
        """当前陪玩预计到手金额；双陪订单优先取本人结算明细。"""
        if obj.commission_allocation_pending:
            return None
        from .points_service_payment import MARKER
        marked = isinstance(obj.funding_terms, dict) and MARKER in obj.funding_terms
        if self._has_points_evidence(obj):
            # The legacy split uses coin paid. Only disclose an assigned
            # provider's own verified pre-points snapshot; never fall back to
            # the provisional order amount if evidence is incomplete.
            request = self.context.get('request')
            account = getattr(request, 'account', None)
            if marked and account and account.account_type == ClubAccount.AccountType.PROVIDER:
                from .points_funding_plan import PointsFundingPlanError, verified_person_income
                from .points_checkout_test import settlement_enabled
                try:
                    return verified_person_income(obj.pk, account.pk, allow_finalized=settlement_enabled())
                except PointsFundingPlanError:
                    return None
            return None
        request = self.context.get('request')
        if request and getattr(request, 'account', None):
            account_id = request.account.pk if request.account.account_type == ClubAccount.AccountType.PROVIDER else None
            segmented, amount = self._verified_person_credit(obj, account_id)
            if segmented:
                return amount
            from .segment_financial_capture import transfer_income_pending
            if transfer_income_pending(obj, account_id):
                return None
            if (request.account.account_type == ClubAccount.AccountType.PROVIDER
                    and obj.status == Order.Status.PENDING and obj.dispatch_route == Order.DispatchRoute.PUBLIC
                    and obj.provider_account_id is None and obj.provider_id is None):
                if obj.required_provider_count > 1:
                    return None
                state = self.context.get('_provider_read_state')
                if state:
                    profile = state[0]
                else:
                    if '_pool_income_profile' not in self.context:
                        from users.models import EscortProfile
                        self.context['_pool_income_profile'] = EscortProfile.objects.filter(account=request.account).select_related('level').first()
                    profile = self.context['_pool_income_profile']
                if not profile:
                    return None
                if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
                    from .models import OrderPlatformSubsidy
                    subsidy = OrderPlatformSubsidy.objects.filter(order_id=obj.pk).first()
                    if subsidy:
                        from .pricing import club_provider_specs
                        from .settlement import build_provider_shares
                        specs = club_provider_specs(obj, [profile], subsidy.basis)
                        if specs is None:
                            specs = [{'commission_type': obj.commission_type,
                                      'commission_rate': obj.commission_rate,
                                      'commission_fixed': obj.commission_fixed}]
                        return build_provider_shares(subsidy.basis, specs)[0].provider_income
                if obj.commission_policy != 'LEGACY' or obj.commission_type == 'FIXED' or obj.commission_override:
                    from .pricing import club_provider_specs
                    from .settlement import build_provider_shares
                    global_rate = None
                    if obj.commission_policy in ('REGULAR', 'FUN') and not (profile.level and profile.level.is_active):
                        if '_pool_global_commission_rate' not in self.context:
                            from wallet.models import get_commission_rate
                            self.context['_pool_global_commission_rate'] = get_commission_rate()
                        global_rate = self.context['_pool_global_commission_rate']
                    specs = club_provider_specs(obj, [profile], obj.amount, global_rate=global_rate)
                    return build_provider_shares(obj.amount, specs)[0].provider_income
                return obj.provider_income
            if hasattr(obj, 'requester_assignments'):
                assignment = next(iter(obj.requester_assignments), None)
            else:
                owner = Q(provider_account=request.account)
                legacy_user = getattr(request, 'legacy_user', None)
                if getattr(request.account, 'account_type', None) == 'PROVIDER' and legacy_user:
                    owner |= Q(provider_account__isnull=True, provider=legacy_user)
                assignment = obj.providers.filter(owner).first()
            if assignment:
                if getattr(request.account, 'account_type', None) == 'PROVIDER':
                    if not self._income_identity_matches(assignment.provider_id, assignment.provider_account_id):
                        return None
                if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
                    from .models import OrderPlatformSubsidyShare
                    funded = OrderPlatformSubsidyShare.objects.filter(assignment=assignment).first()
                    if funded:
                        return funded.amount
                return assignment.provider_income
            if getattr(request.account, 'account_type', None) == 'PROVIDER':
                if obj.required_provider_count > 1:
                    return None
                if not self._income_identity_matches(obj.provider_id, obj.provider_account_id):
                    return None
        return obj.provider_income

    def get_customer(self, obj):
        customer = obj.customer if obj.customer_id else obj.customer_account
        boss_type = customer.boss_type if customer else None
        boss_type_data = None
        if boss_type and boss_type.is_active:
            boss_type_data = {
                'name': boss_type.name,
                'color': boss_type.color,
                'discount_rate': boss_type.discount_rate,
            }
        return {
            'id': obj.customer_id,
            'nickname': (customer.nickname or customer.username) if customer else '',
            'phone': (customer.phone or '') if customer and self._customer_details_visible(obj) else '',
            'boss_type': boss_type_data,
        }

    def get_provider(self, obj):
        if not obj.provider_id and not obj.provider_account_id:
            return None
        from stores.display import provider_display_name
        identity = obj.provider if obj.provider_id else ClubAccount.objects.filter(
            pk=obj.provider_account_id, account_type=ClubAccount.AccountType.PROVIDER).only('nickname', 'username').first()
        name = (identity.nickname or identity.username) if identity else ''
        avatars = self._provider_avatars([obj.provider_account_id])
        return {
            'id': obj.provider_id,
            'account_id': obj.provider_account_id,
            'nickname': name,
            'display_name': provider_display_name(obj.provider_name_snapshot or name),
            'avatar_url': avatars.get(obj.provider_account_id, ''),
        }

    def get_support_contact(self, obj):
        if not obj.support_contact_id:
            return None
        return {
            'id': obj.support_contact_id,
            'name': obj.support_contact_name_snapshot or obj.support_contact.name,
            'avatar_url': obj.support_contact.avatar_url,
        }


class ExpectedServiceSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    price_unit = serializers.CharField(max_length=20)
    service_type = serializers.CharField(max_length=20)
    game_category = serializers.IntegerField(allow_null=True)
    billing_provider_count = serializers.IntegerField(min_value=1, max_value=2)
    fulfillment_provider_count = serializers.IntegerField(min_value=1, max_value=2, required=False)
    unit_price = serializers.IntegerField(min_value=0)
    # Optional for existing clients and saved retry intents. New checkouts
    # include the service terms that are visible on the confirmation page.
    gameplay = serializers.IntegerField(required=False, allow_null=True)
    platform = serializers.CharField(required=False, allow_blank=True, max_length=10)
    security_level = serializers.CharField(required=False, allow_blank=True, max_length=15)
    provider_kind = serializers.CharField(required=False, allow_blank=True, max_length=20)
    provider_grade = serializers.CharField(required=False, allow_blank=True, max_length=20)
    required_level = serializers.IntegerField(required=False, allow_null=True)
    service_category = serializers.IntegerField(required=False, allow_null=True)
    purchase_mode = serializers.CharField(required=False, max_length=10)
    is_addon = serializers.BooleanField(required=False)
    experience_kind = serializers.CharField(required=False, allow_blank=True, max_length=30)
    allows_discount_coupons = serializers.BooleanField(required=False)


class ExpectedQuoteSerializer(serializers.Serializer):
    original_amount = serializers.IntegerField(min_value=0)
    boss_discount = serializers.IntegerField(min_value=0)
    promo_discount = serializers.IntegerField(min_value=0)
    coupon_discount = serializers.IntegerField(min_value=0)


class CreateOrderSerializer(serializers.Serializer):
    dispatch_mode = serializers.ChoiceField(choices=['BOSS_SELECT', 'AUTO_GRAB'], default='AUTO_GRAB')
    dispatch_route = serializers.ChoiceField(choices=Order.DispatchRoute.choices, default=Order.DispatchRoute.PUBLIC)
    service_id = serializers.IntegerField(required=False, allow_null=True)
    product_id = serializers.IntegerField(required=False, allow_null=True)
    provider_id = serializers.IntegerField(required=False, allow_null=True)
    provider_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, max_length=2,
        allow_empty=True,
    )
    support_contact_id = serializers.IntegerField(required=False, allow_null=True)
    user_coupon_id = serializers.IntegerField(required=False, allow_null=True)
    discount_selection_token = serializers.CharField(required=False, max_length=8192, allow_blank=False)
    points_checkout_test_token = serializers.CharField(required=False, max_length=8192, allow_blank=False)
    game_rounds = serializers.IntegerField(required=False, min_value=1, default=1)
    expected_amount = serializers.IntegerField(required=False, min_value=0)
    gift_balance_amount = serializers.IntegerField(required=False, min_value=0, default=0)
    compensation_balance_amount = serializers.IntegerField(required=False, min_value=0, default=0)
    expected_quote = ExpectedQuoteSerializer(required=False)
    expected_timeout_minutes = serializers.IntegerField(required=False, min_value=1, max_value=1440)
    expected_service = ExpectedServiceSerializer(required=False)
    game_region = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    game_nickname = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    game_uid = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    provider_gender = serializers.ChoiceField(choices=Order.ProviderGender.choices, default=Order.ProviderGender.ANY)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')

    def validate(self, attrs):
        request = self.context['request']
        from django.conf import settings
        gift_amount = attrs['gift_balance_amount']
        compensation_amount = attrs['compensation_balance_amount']
        if gift_amount % 10 or compensation_amount % 10:
            raise serializers.ValidationError('赠送和赔付金额须为整数店币')
        if (gift_amount or compensation_amount) and not getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False):
            raise serializers.ValidationError('赠送和赔付余额选择暂未开放')
        account = request.account
        user = request.legacy_user
        if account.account_type not in (
            ClubAccount.AccountType.BOSS,
            ClubAccount.AccountType.STAFF,
        ):
            raise serializers.ValidationError('仅玩家或客服可下单')

        service_id = attrs.get('service_id') or attrs.get('product_id')
        if not service_id:
            raise serializers.ValidationError('请选择服务')

        try:
            service = ServiceItem.objects.get(id=service_id, is_active=True)
        except ServiceItem.DoesNotExist as exc:
            raise serializers.ValidationError('服务不存在或不可用') from exc

        if service.service_category and service.service_category.is_gift:
            raise serializers.ValidationError('礼物请从赠送礼物入口购买')

        from .gameplay import validate_service_gameplay
        # A purchased double package can enter the existing two-person
        # application flow, or be assigned to a complete selected team.
        validate_service_gameplay(service, attrs.get('game_rounds', 1),
                                  companion_assignment=service.fulfillment_provider_count == 2)
        attrs['service'] = service
        attrs['required_provider_count'] = service.fulfillment_provider_count
        if attrs['provider_gender'] != 'ANY' and service.service_type != 'COMPANION':
            raise serializers.ValidationError('仅陪玩服务可选择男陪或女陪')
        original_amount = service.price * attrs.get('game_rounds', 1) * service.billing_provider_count

        # 老板分级折扣：discount_rate=100 表示原价
        boss_type = account.boss_type
        if boss_type and boss_type.is_active:
            boss_rate = boss_type.discount_rate
        else:
            boss_rate = 100
        from coupons.benefits import effective_boss_rate
        boss_rate = effective_boss_rate(account)

        # A specified provider is a direct arrangement. Unspecified requests
        # enter the pool unless the buyer explicitly chooses voice audition.
        if ('dispatch_mode' not in self.initial_data
                and (attrs.get('provider_id') or attrs.get('provider_ids'))):
            attrs['dispatch_mode'] = 'BOSS_SELECT'
        if attrs['dispatch_route'] == Order.DispatchRoute.STAFF:
            attrs['dispatch_mode'] = 'AUTO_GRAB'

        if attrs['dispatch_mode'] == 'AUTO_GRAB' and service.fulfillment_provider_count != 1:
            from .grab_team_flow import enabled as team_grab_enabled
            if service.fulfillment_provider_count != 2:
                raise serializers.ValidationError('多人服务自动抢单尚未开放')
            if not team_grab_enabled():
                raise serializers.ValidationError('多人服务自动抢单尚未开放', code='GRAB_WINDOW_UNAVAILABLE')

        # 指定陪玩下单（可选）：需在解析抽成率前确定，等级抽成依赖陪玩
        provider_id = attrs.get('provider_id')
        selected_ids = attrs.get('provider_ids')
        if selected_ids is not None and provider_id:
            if selected_ids != [provider_id]:
                raise serializers.ValidationError('指定陪玩参数不一致，请重新选择')
        selected_ids = selected_ids if selected_ids is not None else ([provider_id] if provider_id else [])
        if selected_ids and attrs['dispatch_mode'] == 'AUTO_GRAB':
            raise serializers.ValidationError('指定陪玩和自动抢单不能同时选择')
        if attrs['dispatch_route'] == Order.DispatchRoute.STAFF:
            if selected_ids:
                raise serializers.ValidationError('客服派单不能同时指定陪玩')
            if attrs['dispatch_mode'] == 'AUTO_GRAB':
                if service.fulfillment_provider_count == 1:
                    from .grab_single_flow import enabled as grab_enabled
                else:
                    from .grab_team_flow import enabled as grab_enabled
                if not grab_enabled():
                    raise serializers.ValidationError('客服安排暂不可用，请稍后重试', code='GRAB_WINDOW_UNAVAILABLE')
        if len(set(selected_ids)) != len(selected_ids):
            raise serializers.ValidationError('不能重复选择同一位陪玩')
        if selected_ids and len(selected_ids) != service.fulfillment_provider_count:
            raise serializers.ValidationError(f'该套餐须选择{service.fulfillment_provider_count}位陪玩')
        providers, provider_accounts, provider_profiles = [], [], []
        for selected_id in selected_ids:
            try:
                provider_account = None
                # The array contract always addresses business accounts. The
                # historical scalar retains legacy-ID fixture compatibility.
                if 'provider_ids' not in attrs and getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False):
                    legacy_provider = get_user_model().objects.filter(
                        id=selected_id,
                        role='PROVIDER',
                    ).first()
                    if legacy_provider is not None:
                        provider_account = get_or_create_account_for_legacy_user(
                            legacy_provider,
                        )
                        link_legacy_relations(legacy_provider, provider_account)
                if provider_account is None:
                    provider_account = ClubAccount.objects.select_related(
                        'escort_profile__level',
                        'legacy_mapping',
                    ).filter(
                        id=selected_id,
                        account_type=ClubAccount.AccountType.PROVIDER,
                    ).first()
                if provider_account is None:
                    raise ClubAccount.DoesNotExist
                provider = get_user_model().objects.get(
                    id=provider_account.legacy_mapping.legacy_user_id,
                )
            except ObjectDoesNotExist as exc:
                raise serializers.ValidationError('指定的大神不存在') from exc
            profile = getattr(provider_account, 'escort_profile', None)
            reservation_conversion = self.context.get('reservation_conversion') is True
            if profile is None or profile.status == profile.Status.OFFLINE:
                raise serializers.ValidationError('指定的大神当前不可接单')
            reason = profile.order_acceptance_error(service, provider_gender=attrs["provider_gender"],
                                                     allow_refund_recovery=not reservation_conversion,
                reservation_confirmed_at=self.context.get('reservation_confirmations', {}).get(provider_account.pk))
            if reason:
                raise serializers.ValidationError(reason)
            from .services import count_capacity_orders
            from .capacity import current_capacity_limit
            if not reservation_conversion and count_capacity_orders(provider_account) >= current_capacity_limit():
                raise serializers.ValidationError('该大神接单名额已满，请重新选择')
            providers.append(provider)
            provider_accounts.append(provider_account)
            provider_profiles.append(profile)
        if len({account.pk for account in provider_accounts}) != len(provider_accounts):
            raise serializers.ValidationError('不能重复选择同一位陪玩')
        attrs['providers'] = providers
        attrs['provider_accounts'] = provider_accounts
        attrs['provider_profiles'] = provider_profiles
        attrs['provider'] = providers[0] if providers else None

        from support.assignment import selected_card
        attrs['support_contact'] = selected_card(attrs.get('support_contact_id'))

        from .purchase_benefits import is_experience, prepare_purchase
        from .pricing import resolve_commission_rate
        reservation_minutes = self.context.get('reservation_minutes')
        token = attrs.get('discount_selection_token')
        discount_enabled = getattr(settings, 'DISCOUNT_COMBINATIONS_PAYMENT_ENABLED', False)
        if token and not discount_enabled:
            raise serializers.ValidationError('优惠组合付款暂未开放')
        if token and attrs.get('user_coupon_id'):
            raise serializers.ValidationError('请通过优惠组合选择优惠券')
        if discount_enabled and not is_experience(service) and not token:
            raise serializers.ValidationError('请先读取并确认优惠组合')
        if token:
            from .discount_selection import preview_selected_discount
            from .pricing import DiscountResult
            selected, promotion, user_coupon = preview_selected_discount(account, service, attrs.get('game_rounds', 1), token, reservation_minutes)
            if 'user_coupon_id' in attrs and attrs['user_coupon_id'] is None and selected.coupon_id is not None:
                raise serializers.ValidationError('已选择不使用优惠券，请重新确认优惠组合')
            attrs['discount_option_key'] = selected.key
            result = DiscountResult(original_amount=selected.original_amount, boss_discount=selected.boss_discount,
                promo_discount=selected.promo_discount, coupon_discount=selected.coupon_discount, amount=selected.amount)
        else:
            result, promotion, user_coupon = prepare_purchase(account, service,
                attrs.get('game_rounds', 1), attrs.get('user_coupon_id'), reservation_minutes)
        attrs['promotion'] = promotion
        attrs['original_amount'] = result.original_amount
        attrs['boss_discount'] = result.boss_discount
        attrs['promo_discount'] = result.promo_discount
        attrs['coupon_discount'] = result.coupon_discount
        attrs['amount'] = result.amount
        attrs['user_coupon'] = user_coupon
        attrs['pricing'] = result

        # 拆账参数：抽成率 活动>陪玩等级>店铺；推荐人分佣按下单老板配置计提
        escort_profile = provider_profiles[0] if provider_profiles else None
        from .commission import resolve, snapshot
        from .pricing import PricingError
        commission_basis = result.amount
        if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            from .purchase_benefits import vip_safe_box_subsidy_basis
            commission_basis = (vip_safe_box_subsidy_basis(account, service, attrs.get('game_rounds', 1),
                                                           user_coupon, result) or result.amount)
        try:
            plan = resolve(service, commission_basis, profile=escort_profile, promotion=promotion)
        except PricingError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        attrs['commission_snapshot'] = snapshot(plan)
        attrs['commission_rate'] = plan['commission_rate']
        attrs['commission_allocation_pending'] = bool(plan.get('commission_allocation_pending'))
        if plan.get('game_commission_snapshot', {}).get('personal_rules') and provider_profiles:
            try:
                attrs['personal_game_snapshots'] = [resolve(service, commission_basis, profile=person,
                    promotion=promotion)['game_commission_snapshot'] for person in provider_profiles]
            except PricingError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        from .pricing import club_provider_specs
        specs = None if attrs['commission_allocation_pending'] else club_provider_specs(service, [escort_profile], commission_basis)
        attrs['commission_spec'] = ({'commission_type':'FIXED','commission_fixed':plan['commission_fixed'],'commission_rate':0}
            if plan['commission_type'] == 'FIXED' else specs[0] if specs else {'commission_rate': attrs['commission_rate']})
        if len(provider_profiles) == 2:
            from .commission import fixed_specs
            if plan['commission_type'] == 'FIXED':
                selected_specs = fixed_specs(result.amount, 2, plan['commission_fixed'])
            else:
                try:
                    selected_specs = club_provider_specs(service, provider_profiles, result.amount)
                except PricingError as exc:
                    raise serializers.ValidationError(str(exc)) from exc
                if selected_specs is None:
                    selected_specs = [
                        {'commission_rate': resolve(service, result.amount, profile=profile,
                                                    promotion=promotion)['commission_rate']}
                        for profile in provider_profiles
                    ]
            attrs['provider_specs'] = selected_specs
        from .inviter import buyer_inviter
        attrs['inviter'], attrs['inviter_account'], attrs['inviter_commission_rate'] = buyer_inviter(account, user)

        return attrs


class EvaluationSerializer(EntityNumberModelSerializer):
    customer_name = serializers.SerializerMethodField()
    customer_avatar = serializers.SerializerMethodField()
    provider_name = serializers.SerializerMethodField()
    provider_display_name = serializers.SerializerMethodField()
    service_name = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='order.order_no', read_only=True)
    avg_score = serializers.FloatField(read_only=True)

    class Meta:
        model = Evaluation
        fields = [
            'id', 'order', 'order_no', 'score',
            'skill_score', 'attitude_score', 'communication_score', 'avg_score',
            'content', 'is_anonymous',
            'customer_name', 'customer_avatar', 'provider_name', 'provider_display_name', 'service_name',
            'reply_content', 'reply_author', 'replied_at', 'reply_version', 'created_at',
        ]

    def get_customer_name(self, obj):
        if obj.is_anonymous:
            return '匿名用户'
        customer = obj.customer_account if obj.customer_account_id else obj.customer
        return customer.nickname or customer.username

    def get_customer_avatar(self, obj):
        if obj.is_anonymous:
            return ''
        customer = obj.customer_account if obj.customer_account_id else obj.customer
        return customer.avatar_url or ''

    def get_provider_name(self, obj):
        from .evaluation_targets import evaluation_provider_name
        return evaluation_provider_name(obj)

    def get_provider_display_name(self, obj):
        from stores.display import provider_display_name
        return provider_display_name(self.get_provider_name(obj))

    def get_service_name(self, obj):
        return obj.order.service_name_snapshot


class CreateEvaluationSerializer(serializers.Serializer):
    order_id = serializers.IntegerField(required=True)
    provider_account_id = serializers.IntegerField(required=False, min_value=1)
    provider_legacy_id = serializers.IntegerField(required=False, min_value=1)
    score = serializers.IntegerField(min_value=1, max_value=5)
    skill_score = serializers.IntegerField(min_value=1, max_value=5)
    attitude_score = serializers.IntegerField(min_value=1, max_value=5)
    communication_score = serializers.IntegerField(min_value=1, max_value=5)
    content = serializers.CharField(required=False, allow_blank=True, max_length=500, default='')
    is_anonymous = serializers.BooleanField(default=False)

    def validate(self, attrs):
        from .evaluation_targets import evaluation_targets
        targets = evaluation_targets(self.context['order'])
        account_id = attrs.get('provider_account_id')
        legacy_id = attrs.get('provider_legacy_id')
        if account_id and legacy_id:
            raise serializers.ValidationError('请选择一位陪玩')
        if account_id:
            matches = [item for item in targets if item['provider_account_id'] == account_id]
        elif legacy_id:
            matches = [item for item in targets if item['provider_legacy_id'] == legacy_id and not item['provider_account_id']]
        else:
            matches = targets if len(targets) == 1 else []
        if len(matches) != 1:
            raise serializers.ValidationError('请选择本单需要评价的陪玩')
        target = matches[0]
        if Evaluation.objects.filter(order=self.context['order'], provider_id=target['provider_legacy_id']).exists():
            raise serializers.ValidationError('该陪玩已评价，请查看订单详情')
        attrs['evaluation_target'] = target
        return attrs

    def validate_order_id(self, value):
        request = self.context['request']
        try:
            order = Order.objects.get(id=value)
        except Order.DoesNotExist as exc:
            raise serializers.ValidationError('订单不存在') from exc

        if order.status != Order.Status.COMPLETED:
            raise serializers.ValidationError('仅已完成订单可评价')
        if order.customer_account_id:
            owner = order.customer_account_id == request.account.pk
        else:
            owner = request.legacy_user is not None and order.customer_id == request.legacy_user.pk
        if not owner:
            raise serializers.ValidationError('仅订单所属玩家可评价')
        self.context['order'] = order
        return value
