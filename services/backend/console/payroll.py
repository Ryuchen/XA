"""Permission-scoped payroll, provenance, operating costs and ROI endpoints."""
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError
from stores.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from club_accounts.models import ClubAccount
from orders.models import AfterSale, Order, OrderStatusLog
from orders.refunds import PAID_STATES
from wallet.models import RechargeRecord
from .dictionary_models import BusinessDictionaryItem
from .mixins import EnvelopeViewSetMixin
from .permissions import IsConsoleUser, get_account_permissions
from .pagination import ConsolePagination
from .payroll_calculation import (aftersale_cycles, calculate_payroll, eligible_recharges, fingerprint, month_bounds, name_of, operating_roi,
                                  parse_month, plan_snapshot, raw_to_fen, recharge_source_amount_fen,
                                  refund_accounting_enabled, round_ratio)
from .payroll_models import (OperatingCost, PayrollConfig, PayrollLock, PayrollRun,
                            SalaryActivity, SalaryAssignment, SalaryPlan)


class PayrollConflict(APIException):
    status_code = 409
    default_code = 'PAYROLL_CONFLICT'


def require(request, permission):
    if permission not in get_account_permissions(request.account):
        raise PermissionDenied('无操作权限')


def costing_lock():
    PayrollLock.objects.get_or_create(pk=1)
    return PayrollLock.objects.select_for_update().get(pk=1)


def assert_open(month):
    if PayrollRun.objects.filter(month=month).exists():
        raise PayrollConflict('该月薪资已确认，人员安排及业绩归属已冻结；历史快照不能改写')


def late_activity_terms(month, account_id, kind, *, source_amount_fen=None, order=None, performed=None):
    """Use the confirmed original plan and the first unconfirmed payroll period."""
    run = PayrollRun.objects.filter(month=month).first()
    if not run:
        return None
    from .payroll_models import PayrollReemployment, PayrollReemploymentPlan
    reemployment = (PayrollReemployment.objects.filter(account_id=account_id,
        separation__final_run__month=month, created_at__lte=performed).order_by('-created_at', '-pk').first()
        if performed else None)
    new_plan = None
    if reemployment:
        new_plan = PayrollReemploymentPlan.objects.filter(reemployment=reemployment,
            effective_at__lte=performed).order_by('-effective_at', '-pk').first()
        if not new_plan:
            raise PayrollConflict('该业绩属于重新入职后的已确认月份，发生时薪资方案待核对')
    if kind == 'DISPATCH' and not refund_accounting_enabled():
        raise PayrollConflict('已确认月份的此类业绩补发尚未接通')
    if kind not in ('EXAM', 'AFTERSALE', 'RECHARGE', 'DISPATCH'):
        raise PayrollConflict('已确认月份的此类业绩补发尚未接通')
    original = next((line for line in run.snapshot.get('lines', []) if line['account'] == account_id), None)
    if not original:
        raise PayrollConflict('原月已确认工资没有该员工方案，请先核对')
    plan = new_plan.plan_snapshot if new_plan else original['plan_snapshot']
    if new_plan:
        original = {**original, 'plan_snapshot': plan, 'dispatch_day_amount_fen': 0,
                    'dispatch_night_amount_fen': 0, 'performance_basis_fen': 0, 'recharge_amount_fen': 0}
    dispatch_terms = None
    if kind == 'DISPATCH':
        from .payroll_late_dispatch import initial_terms, reemployment_night_config
        dispatch_terms = initial_terms(run, original, order, performed,
            reemployment_plan_id=new_plan.pk if new_plan else None,
            night_config=reemployment_night_config(new_plan) if new_plan else None)
        gross = 0
        deduction = 0
    elif kind == 'RECHARGE':
        prior = SalaryActivity.objects.filter(account_id=account_id, kind='RECHARGE', is_void=False,
                                              recognition_month__isnull=False).order_by('pk')
        previous = sum(row.source_snapshot['late_compensation']['source_amount_fen'] for row in prior
                       if row.source_snapshot.get('late_compensation', {}).get('original_run_id') == run.pk
                       and row.source_snapshot.get('late_compensation', {}).get('reemployment_plan_id') == (new_plan.pk if new_plan else None))
        basis = original['recharge_amount_fen'] + previous
        rate = plan['recharge_bps']
        gross = (round_ratio(basis + source_amount_fen, rate, 10000)
                 - round_ratio(basis, rate, 10000))
        deduction = 0
    else:
        gross = plan['exam_fee_fen'] if kind == 'EXAM' else plan['aftersale_fee_fen']
        deduction = round_ratio(gross, plan.get('exam_commission_bps', 0), 10000) if kind == 'EXAM' else 0
    current = timezone.localdate().replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    target = max(current, next_month)
    while PayrollRun.objects.filter(month=target).exists():
        target = (target.replace(day=28) + timedelta(days=4)).replace(day=1)
    result = {'original_month': month.strftime('%Y-%m'), 'recognition_month': target.strftime('%Y-%m'),
            'original_run_id': run.pk, 'gross_fen': gross, 'deduction_fen': deduction,
            'amount_fen': gross - deduction, 'plan_snapshot': plan}
    if new_plan:
        result.update(reemployment_id=reemployment.pk, reemployment_plan_id=new_plan.pk,
                      plan_effective_at=new_plan.effective_at.isoformat())
    if kind == 'RECHARGE':
        result.update(source_amount_fen=source_amount_fen, original_basis_fen=basis, rate_bps=rate)
    if dispatch_terms is not None:
        result.update(dispatch_terms)
    return result


def dispatch_source_amount_fen(order):
    """Show the paid coin amount only when its payment and refunds are proved."""
    from .payroll_late_dispatch import _verified_refund_total, payment_for_dispatch

    try:
        payment_for_dispatch(order)
        refunded = _verified_refund_total(order.refund_records.select_related('order'))
        if refunded > order.amount or (order.payment_status == 'REFUNDED' and refunded != order.amount):
            return None
        return raw_to_fen(order.amount - refunded)
    except serializers.ValidationError:
        return None


def employee_queryset():
    return ClubAccount.objects.filter(account_type='STAFF', is_active=True,
                                     admin_membership__is_active=True).order_by('id')


def employee_is_payroll_eligible(account, *, month=None):
    from .payroll_models import PayrollReemployment, PayrollSeparationSettlement
    last = PayrollSeparationSettlement.objects.filter(account=account).select_related('final_run').order_by('-confirmed_at', '-pk').first()
    if last:
        if not PayrollReemployment.objects.filter(account=account, separation=last).exists():
            return False
        if month is not None and month <= last.final_run.month:
            return False
    active = employee_queryset().filter(pk=account.pk).exists()
    if active and account.can_login:
        return True
    assignments = SalaryAssignment.objects.filter(account=account)
    if last:
        assignments = assignments.filter(month__gt=last.final_run.month)
    return account.account_type == 'STAFF' and assignments.exists()


def active_dictionary(item, kind, previous=None):
    if item.type != kind or (not item.is_active and item.pk != previous):
        raise serializers.ValidationError('请选择对应类型中启用的字典项')


class MonthField(serializers.Field):
    def to_internal_value(self, value):
        return parse_month(value)

    def to_representation(self, value):
        return value.strftime('%Y-%m')


class SalaryPlanSerializer(EntityNumberModelSerializer):
    position_name = serializers.CharField(source='position.name', read_only=True)
    revision = serializers.IntegerField(min_value=1, required=False)

    class Meta:
        model = SalaryPlan
        fields = ['id', 'position', 'position_name', 'name', 'base_fen', 'performance_fen', 'target_fen',
                  'target_scope', 'dispatch_day_bps', 'dispatch_night_bps', 'recharge_bps',
                  'aftersale_fee_fen', 'exam_fee_fen', 'exam_commission_bps', 'remark', 'is_active',
                  'revision', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']
        extra_kwargs = {field: {'min_value': 0, 'max_value': 1000000000000} for field in
                        ('base_fen', 'performance_fen', 'target_fen', 'aftersale_fee_fen', 'exam_fee_fen')}
        extra_kwargs.update({field: {'min_value': 0, 'max_value': 10000} for field in
                             ('dispatch_day_bps', 'dispatch_night_bps', 'recharge_bps', 'exam_commission_bps')})

    def validate(self, attrs):
        item = attrs.get('position', getattr(self.instance, 'position', None))
        if item:
            active_dictionary(item, 'SALARY_POSITION', getattr(self.instance, 'position_id', None))
        performance = attrs.get('performance_fen', getattr(self.instance, 'performance_fen', 0))
        target = attrs.get('target_fen', getattr(self.instance, 'target_fen', 0))
        if performance and not target:
            raise serializers.ValidationError({'target_fen': '有绩效时须设置正数业绩目标'})
        return attrs


class SalaryPlanViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = SalaryPlan.objects.select_related('position').all()
    serializer_class = SalaryPlanSerializer
    default_perm = 'salary:view'
    required_perms = {action: 'salary:edit' for action in ('create', 'update', 'partial_update', 'destroy')}

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            from stores.license_payroll import require_payroll_configuration
            require_payroll_configuration(account=request.account)

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('keyword'):
            qs = qs.filter(Q(name__icontains=self.request.query_params['keyword']) |
                           Q(position__name__icontains=self.request.query_params['keyword']))
        if 'is_active' in self.request.query_params:
            value = self.request.query_params['is_active']
            if value not in ('true', 'false'):
                raise serializers.ValidationError({'is_active': '须为true或false'})
            qs = qs.filter(is_active=value == 'true')
            if value == 'true':
                qs = qs.filter(position__is_active=True)
        return qs

    @transaction.atomic
    def perform_update(self, serializer):
        costing_lock()
        row = SalaryPlan.objects.select_for_update().get(pk=serializer.instance.pk)
        if serializer.validated_data.pop('revision', None) != row.revision:
            raise PayrollConflict('薪资方案已更新，请刷新后核对再保存')
        serializer.instance = row
        serializer.save(revision=row.revision + 1)

    def perform_create(self, serializer):
        serializer.validated_data.pop('revision', None)
        serializer.save(created_by=self.request.account, created_by_system=False)


class SalaryAssignmentSerializer(EntityNumberModelSerializer):
    month = MonthField()
    account_name = serializers.CharField(source='account.nickname', read_only=True)
    plan_name = serializers.CharField(source='plan.name', read_only=True)
    position_name = serializers.CharField(source='plan.position.name', read_only=True)
    revision = serializers.IntegerField(min_value=1, required=False)
    adopt_current_plan = serializers.BooleanField(write_only=True, required=False, default=False)
    expected_plan_revision = serializers.IntegerField(write_only=True, required=False, min_value=1)

    class Meta:
        model = SalaryAssignment
        fields = ['id', 'month', 'account', 'account_name', 'plan', 'plan_name', 'position_name',
                  'plan_snapshot', 'adopt_current_plan', 'expected_plan_revision', 'revision', 'created_at', 'updated_at']
        read_only_fields = ['plan_snapshot', 'created_at', 'updated_at']
        validators = []

    def validate(self, attrs):
        account = attrs.get('account', getattr(self.instance, 'account', None))
        month = attrs.get('month', getattr(self.instance, 'month', None))
        if account and not employee_is_payroll_eligible(account, month=month):
            if not self.instance or account.pk != self.instance.account_id:
                raise serializers.ValidationError({'account': '请选择在职后台员工；薪资职位独立于权限角色'})
        plan = attrs.get('plan', getattr(self.instance, 'plan', None))
        if plan and (not plan.is_active or not plan.position.is_active):
            if not self.instance or plan.pk != self.instance.plan_id:
                raise serializers.ValidationError({'plan': '请选择启用的薪资方案'})
        return attrs


class SalaryAssignmentViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = SalaryAssignment.objects.select_related('account', 'plan__position').all()
    serializer_class = SalaryAssignmentSerializer
    default_perm = 'salary:view'
    required_perms = {action: 'salary:edit' for action in ('create', 'update', 'partial_update', 'destroy')}

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('month'):
            qs = qs.filter(month=parse_month(self.request.query_params['month']))
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        costing_lock()
        month, account = serializer.validated_data['month'], serializer.validated_data['account']
        if not employee_is_payroll_eligible(account, month=month):
            raise PayrollConflict('该员工已结清或不再符合薪资核算条件')
        assert_open(month)
        if SalaryAssignment.objects.filter(month=month, account=account).exists():
            raise PayrollConflict('同一员工同一月份只能安排一个薪资方案；兼岗请使用明确汇总的方案')
        plan = SalaryPlan.objects.select_for_update().select_related('position').get(pk=serializer.validated_data['plan'].pk)
        serializer.validated_data.pop('revision', None)
        serializer.validated_data.pop('adopt_current_plan', None)
        expected_plan = serializer.validated_data.pop('expected_plan_revision', None)
        if expected_plan is not None and expected_plan != plan.revision:
            raise PayrollConflict('所选薪资方案已更新，请刷新核对最新金额后再安排')
        serializer.save(created_by=self.request.account, plan_snapshot=plan_snapshot(plan))

    @transaction.atomic
    def perform_update(self, serializer):
        costing_lock()
        row = SalaryAssignment.objects.select_for_update().get(pk=serializer.instance.pk)
        assert_open(row.month)
        month = serializer.validated_data.get('month', row.month)
        account = serializer.validated_data.get('account', row.account)
        if not employee_is_payroll_eligible(account, month=month):
            raise PayrollConflict('该员工已结清或不再符合薪资核算条件')
        assert_open(month)
        if serializer.validated_data.pop('revision', None) != row.revision:
            raise PayrollConflict('人员薪资安排已更新，请刷新后核对')
        if SalaryAssignment.objects.filter(month=month, account=account).exclude(pk=row.pk).exists():
            raise PayrollConflict('该员工在该月已有薪资安排')
        serializer.instance = row
        # Only an explicitly chosen plan refreshes a personal arrangement's
        # terms; merely editing names elsewhere never rewrites its contract.
        kwargs = {'revision': row.revision + 1}
        adopt = serializer.validated_data.pop('adopt_current_plan', False)
        selected = serializer.validated_data.get('plan', row.plan)
        expected_plan = serializer.validated_data.pop('expected_plan_revision', None)
        if selected.pk != row.plan_id or adopt:
            selected = SalaryPlan.objects.select_for_update().select_related('position').get(pk=selected.pk)
            if expected_plan is not None and selected.revision != expected_plan:
                raise PayrollConflict('所选薪资方案已更新，请刷新核对最新金额后再采纳')
            kwargs['plan_snapshot'] = plan_snapshot(selected)
        serializer.save(**kwargs)

    @transaction.atomic
    def perform_destroy(self, instance):
        costing_lock()
        row = SalaryAssignment.objects.select_for_update().get(pk=instance.pk)
        assert_open(row.month)
        expected = serializers.IntegerField(min_value=1).run_validation(self.request.data.get('revision'))
        if expected != row.revision:
            raise PayrollConflict('人员安排已更新，请刷新核对当前方案后再删除')
        row.delete()


def dispatch_time(order):
    if order.is_gift_snapshot:
        return None
    # Never infer a night shift from a user-entered timestamp. Prefer the
    # original staff creation event; for self-created orders use first actual
    # staff assignment, not a later transfer.
    logs = order.status_logs.filter(operator_account__account_type='STAFF')
    created = logs.filter(action=OrderStatusLog.Action.CREATE).order_by('created_at', 'id').first()
    assigned = logs.filter(action=OrderStatusLog.Action.ASSIGN).order_by('created_at', 'id').first()
    event = created or assigned
    return event.created_at if event else None


class SalaryActivitySerializer(EntityNumberModelSerializer):
    account_name = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='order.order_no', read_only=True, default='')
    source_amount_fen = serializers.SerializerMethodField()
    source_id = serializers.IntegerField(min_value=1, write_only=True, required=False)
    cycle_key = serializers.CharField(max_length=64, write_only=True, required=False)
    client_request_id = serializers.CharField(max_length=64, required=False, allow_blank=False)

    class Meta:
        model = SalaryActivity
        fields = ['id', 'kind', 'account', 'account_name', 'source_id', 'cycle_key', 'source_key',
                  'performed_at', 'reason', 'order_no', 'source_amount_fen', 'source_snapshot',
                  'recognition_month', 'client_request_id', 'created_at', 'is_void']
        read_only_fields = ['source_key', 'order_no', 'source_snapshot', 'recognition_month', 'created_at', 'is_void']
        extra_kwargs = {'performed_at': {'required': False}, 'reason': {'min_length': 2}}
        validators = []

    def get_account_name(self, obj):
        return name_of(obj.account)

    def get_source_amount_fen(self, obj):
        if obj.kind == 'DISPATCH':
            row = Order.objects.filter(pk=obj.order_id, payment_status__in=(*PAID_STATES, 'REFUNDED')).first()
            return dispatch_source_amount_fen(row) if row else None
        if obj.kind == 'RECHARGE':
            return recharge_source_amount_fen(obj)
        return None

    def validate(self, attrs):
        if not employee_is_payroll_eligible(attrs['account']):
            raise serializers.ValidationError({'account': '请选择可核算工资的后台员工'})
        kind = attrs['kind']
        if kind == 'EXAM':
            if not attrs.get('performed_at') or not attrs.get('client_request_id'):
                raise serializers.ValidationError('人工核实考试须填写实际完成时间及操作凭证')
            if attrs['performed_at'] > timezone.now():
                raise serializers.ValidationError('考试完成时间不能晚于现在')
        elif 'performed_at' in self.initial_data:
            raise serializers.ValidationError({'performed_at': '该时间取自真实业务，不接受手动指定'})
        elif not attrs.get('source_id'):
            raise serializers.ValidationError({'source_id': '请选择真实业务来源'})
        return attrs


class SalaryActivityViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    http_method_names = ['get', 'post', 'delete', 'head', 'options']
    queryset = SalaryActivity.objects.filter(is_void=False).select_related('account', 'order', 'recharge').all()
    serializer_class = SalaryActivitySerializer
    default_perm = 'salary:view'
    required_perms = {'create': 'salary:edit', 'destroy': 'salary:edit'}

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('kind'):
            qs = qs.filter(kind=self.request.query_params['kind'])
        if self.request.query_params.get('month'):
            month = parse_month(self.request.query_params['month'])
            start, end = month_bounds(month)
            if refund_accounting_enabled():
                # The source for an add-on or refund remains its verified
                # dispatch, even when the original order was paid in another
                # month. Use the same verified events as the payroll itself.
                from .payroll_refund_performance import monthly_payment_refund_events
                order_ids = {event['order_id'] for event in monthly_payment_refund_events(month)
                             if event['account_id'] is not None}
                ordinary = (Q(kind='DISPATCH', order_id__in=order_ids)
                            | (Q(recognition_month__isnull=True, performed_at__gte=start, performed_at__lt=end)
                               & ~Q(kind='DISPATCH')))
                qs = qs.filter(Q(recognition_month=month) | ordinary).distinct()
            else:
                qs = qs.filter(Q(recognition_month=month) | Q(recognition_month__isnull=True,
                               performed_at__gte=start, performed_at__lt=end))
        return qs

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        locked_order = None
        if data['kind'] == 'DISPATCH' and getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            # Shared barrier excludes month closeout; the order row is also
            # necessary because refund writers hold the shared barrier too.
            # Take the order before PayrollLock, matching refund writers and
            # avoiding inversion with an exclusive month closeout.
            from .payroll_event_barrier import lock_payment_refund_events
            lock_payment_refund_events()
            locked_order = get_object_or_404(Order.objects.select_for_update().filter(
                direct_payment__isnull=True), pk=data.get('source_id'))
        costing_lock()
        if not employee_is_payroll_eligible(data['account']):
            raise PayrollConflict('该员工已结清或不再符合薪资核算条件')
        request_id = data.get('client_request_id')
        intent = fingerprint({key: (value.pk if isinstance(value, ClubAccount) else value) for key, value in data.items()})
        if request_id:
            old = SalaryActivity.objects.filter(client_request_id=request_id).first()
            if old:
                if old.request_fingerprint != intent or old.is_void:
                    raise PayrollConflict('该操作凭证已有不同或已撤销的核实记录，请核对后再操作')
                return Response({'code': 0, 'data': self.get_serializer(old).data})
        kind, source_id = data['kind'], data.pop('source_id', None)
        cycle_key = data.pop('cycle_key', None)
        values = {'source_snapshot': {}}
        if kind == 'DISPATCH':
            order = locked_order or get_object_or_404(Order.objects.filter(direct_payment__isnull=True), pk=source_id)
            performed = dispatch_time(order)
            refund_accounting = refund_accounting_enabled()
            if not performed or (not refund_accounting and order.payment_status not in PAID_STATES):
                raise serializers.ValidationError('该订单没有可核实的客服派单事件或有效已付净消费')
            if refund_accounting:
                from .payroll_late_dispatch import payment_for_dispatch
                payment = payment_for_dispatch(order)
            values.update(order=order, source_key=f'ORDER:{order.pk}', performed_at=performed,
                          source_snapshot={'order_no': order.order_no, 'time_basis': '真实客服创建/首次派单事件'})
        elif kind == 'RECHARGE':
            recharge = get_object_or_404(eligible_recharges().select_related('recharge_tx'), pk=source_id)
            values.update(recharge=recharge, source_key=f'RECHARGE:{recharge.pk}', performed_at=recharge.recharge_tx.created_at,
                          source_snapshot={'trade_no': recharge.trade_no, 'gift_excluded': True,
                                           'recharge_tx_id': recharge.recharge_tx_id,
                                           'source_amount_raw': recharge.amount,
                                           'buyer_account_id': recharge.account_id,
                                           'time_basis': '本金钱包到账时间'})
        elif kind == 'AFTERSALE':
            row = get_object_or_404(AfterSale.objects.select_related('order').filter(order__direct_payment__isnull=True), pk=source_id)
            cycle = next((item for item in aftersale_cycles(row) if item['cycle_key'] == cycle_key), None)
            if not cycle:
                raise serializers.ValidationError('请选择真实已完成售后周期，不能将处理中售后计次')
            moment = serializers.DateTimeField().run_validation(cycle['performed_at'])
            values.update(aftersale=row, order=row.order, source_key=f'AFTERSALE:{row.pk}:{cycle_key}',
                          performed_at=moment, source_snapshot=cycle)
        else:
            values.update(source_key=f'EXAM:{request_id}', performed_at=data['performed_at'],
                          source_snapshot={'verified_manually': True, 'count': 1})
        month_moment = payment.created_at if kind == 'DISPATCH' and refund_accounting else values['performed_at']
        month = timezone.localdate(month_moment).replace(day=1)
        terms = late_activity_terms(month, data['account'].pk, kind,
            source_amount_fen=raw_to_fen(recharge.amount) if kind == 'RECHARGE' else None,
            order=order if kind == 'DISPATCH' else None,
            performed=performed if kind == 'DISPATCH' else values['performed_at'])
        if terms:
            values['recognition_month'] = parse_month(terms['recognition_month'])
            values['source_snapshot']['late_compensation'] = terms
        old = SalaryActivity.objects.filter(source_key=values['source_key']).first()
        if old:
            raise PayrollConflict('该业务来源已归属，不能重复计入；请核对现有记录')
        row = SalaryActivity.objects.create(**{**data, **values}, created_by=request.account,
                                            request_fingerprint=intent)
        if terms and kind == 'DISPATCH':
            from .payroll_late_dispatch import current_terms
            row.source_snapshot['late_compensation'] = current_terms(row, cutoff=timezone.now())
            row.save(update_fields=['source_snapshot'])
        return Response({'code': 0, 'data': self.get_serializer(row).data}, status=201)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        costing_lock()
        row = SalaryActivity.objects.select_for_update().get(pk=self.get_object().pk)
        from .payroll_late_dispatch import activity_payroll_month
        assert_open(activity_payroll_month(row))
        if row.kind in ('RECHARGE', 'DISPATCH') and row.recognition_month:
            terms = row.source_snapshot['late_compensation']
            later = SalaryActivity.objects.filter(account_id=row.account_id, kind=row.kind, is_void=False,
                                                   recognition_month__isnull=False, pk__gt=row.pk)
            if any(item.source_snapshot.get('late_compensation', {}).get('original_run_id') == terms['original_run_id']
                   for item in later):
                raise PayrollConflict('该原月已有后续同类补发，请先撤销后续记录')
        row.is_void = True
        row.source_snapshot = {**row.source_snapshot, 'voided_source_key': row.source_key,
                               'voided_by': request.account.pk, 'voided_at': timezone.now().isoformat()}
        row.source_key = f'VOID:{row.pk}'
        row.save(update_fields=['is_void', 'source_snapshot', 'source_key'])
        return Response({'code': 0, 'msg': '核实记录已撤销；原操作凭证保留防止迟到重试'})


class OperatingCostSerializer(EntityNumberModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_code = serializers.CharField(source='category.code', read_only=True)
    channel_name = serializers.CharField(source='channel.name', read_only=True, default='')
    revision = serializers.IntegerField(min_value=1, required=False)
    amount_fen = serializers.IntegerField(min_value=0, max_value=1000000000000)
    client_request_id = serializers.CharField(max_length=64, required=False, allow_blank=False)

    class Meta:
        model = OperatingCost
        fields = ['id', 'day', 'category', 'category_name', 'category_code', 'channel', 'channel_name',
                  'amount_fen', 'note', 'revision', 'client_request_id', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']
        validators = []

    def validate(self, attrs):
        category = attrs.get('category', getattr(self.instance, 'category', None))
        channel = attrs.get('channel', getattr(self.instance, 'channel', None))
        if category:
            active_dictionary(category, 'COST_CATEGORY', getattr(self.instance, 'category_id', None))
        if channel:
            active_dictionary(channel, 'AD_CHANNEL', getattr(self.instance, 'channel_id', None))
        if category and category.code == 'AD_SPEND' and not channel:
            raise serializers.ValidationError({'channel': '投流成本须选择渠道，未知来源可选未知/未归因'})
        if not self.instance and not attrs.get('client_request_id'):
            raise serializers.ValidationError({'client_request_id': '新增成本须携带稳定操作凭证'})
        return attrs


class OperatingCostViewSet(EnvelopeViewSetMixin, ModelViewSet):
    queryset = OperatingCost.objects.filter(is_void=False).select_related('category', 'channel').all()
    serializer_class = OperatingCostSerializer
    default_perm = 'cost:view'
    required_perms = {action: 'cost:edit' for action in ('create', 'update', 'partial_update', 'destroy')}

    def get_queryset(self):
        qs = super().get_queryset()
        for name, lookup in (('start_date', 'day__gte'), ('end_date', 'day__lte')):
            if self.request.query_params.get(name):
                qs = qs.filter(**{lookup: serializers.DateField().run_validation(self.request.query_params[name])})
        if self.request.query_params.get('category'):
            qs = qs.filter(category_id=serializers.IntegerField(min_value=1).run_validation(self.request.query_params['category']))
        return qs

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        costing_lock()
        data = dict(serializer.validated_data)
        data.pop('revision', None)
        intent = fingerprint({key: (value.pk if isinstance(value, BusinessDictionaryItem) else value)
                              for key, value in data.items()})
        old = OperatingCost.objects.filter(client_request_id=data['client_request_id']).first()
        if old:
            if old.request_fingerprint != intent or old.is_void:
                raise PayrollConflict('该成本操作凭证已有不同或已撤销的记录，请先核对')
            return Response({'code': 0, 'data': self.get_serializer(old).data})
        row = OperatingCost.objects.create(**data, created_by=request.account, request_fingerprint=intent)
        return Response({'code': 0, 'data': self.get_serializer(row).data}, status=201)

    @transaction.atomic
    def perform_update(self, serializer):
        costing_lock()
        row = OperatingCost.objects.select_for_update().get(pk=serializer.instance.pk)
        if row.is_void:
            raise PayrollConflict('该成本记录已撤销，请刷新列表')
        if serializer.validated_data.pop('revision', None) != row.revision:
            raise PayrollConflict('成本记录已更新，请刷新后核对再保存')
        serializer.validated_data.pop('client_request_id', None)
        serializer.instance = row
        serializer.save(revision=row.revision + 1)

    @transaction.atomic
    def perform_destroy(self, instance):
        costing_lock()
        row = OperatingCost.objects.select_for_update().get(pk=instance.pk)
        expected = serializers.IntegerField(min_value=1).run_validation(self.request.data.get('revision'))
        if expected != row.revision:
            raise PayrollConflict('成本记录已更新，请刷新核对当前金额后再撤销')
        row.is_void = True
        row.revision += 1
        row.save(update_fields=['is_void', 'revision', 'updated_at'])


class PayrollConfigSerializer(EntityNumberModelSerializer):
    revision = serializers.IntegerField(min_value=0)
    night_start = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'], allow_null=True)
    night_end = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'], allow_null=True)

    class Meta:
        model = PayrollConfig
        fields = ['night_start', 'night_end', 'revision']

    def validate(self, attrs):
        start, end = attrs['night_start'], attrs['night_end']
        if (start is None) != (end is None) or (start is not None and start == end):
            raise serializers.ValidationError('夜班起止须同时填写且不能相同；均为空表示未配置')
        return attrs


class PayrollConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        require(request, 'salary:view')
        row = PayrollConfig.objects.filter(pk=1).first()
        return Response({'code': 0, 'data': PayrollConfigSerializer(row).data if row else
                         {'night_start': None, 'night_end': None, 'revision': 0}})

    @transaction.atomic
    def put(self, request):
        require(request, 'salary:edit')
        from stores.license_payroll import require_payroll_configuration
        require_payroll_configuration(account=request.account)
        serializer = PayrollConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        costing_lock()
        row = PayrollConfig.objects.select_for_update().filter(pk=1).first()
        expected = serializer.validated_data.pop('revision')
        if expected != (row.revision if row else 0):
            raise PayrollConflict('夜班配置已更新，请刷新后核对')
        if row is None:
            row = PayrollConfig.objects.create(pk=1, **serializer.validated_data)
        else:
            row.night_start, row.night_end = serializer.validated_data['night_start'], serializer.validated_data['night_end']
            row.revision += 1
            row.save()
        return Response({'code': 0, 'data': PayrollConfigSerializer(row).data})


class PayrollPreviewView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        require(request, 'salary:view')
        return Response({'code': 0, 'data': calculate_payroll(parse_month(request.query_params.get('month')))})


def confirm_payroll_locked(month, supplied, *, local_actor=None, platform_id=None, request_id=None, request_hash=''):
    """Confirm one immutable payroll within the caller's tenant transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('工资确认须在门店事务内执行')
    if not refund_accounting_enabled() or not getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        raise PayrollConflict('工资确认需要启用退款工资核算和工资事件屏障，请先完成门店配置')
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from .payroll_event_barrier import lock_payroll_events
        lock_payroll_events()
    costing_lock()
    if platform_id is not None and PayrollRun.objects.filter(platform_request_id=request_id).exclude(month=month).exists():
        raise PayrollConflict('请求编号已用于其他工资月份')
    existing = PayrollRun.objects.filter(month=month).first()
    if existing:
        if existing.fingerprint != supplied:
            raise PayrollConflict('该月已有不同的确认快照，请查看历史结果')
        if platform_id is not None and (existing.confirmed_by_platform_id != platform_id
                or existing.platform_request_id != request_id or existing.platform_request_hash != request_hash):
            raise PayrollConflict('该月工资已由其他请求确认，请查看历史结果')
        return existing, calculate_payroll(month), False
    start, end = month_bounds(month)
    # The financial services lock orders too. Hold the actual source rows
    # while checking the preview fingerprint and taking the snapshot.
    if refund_accounting_enabled():
        sources = (Q(salary_activities__is_void=False) | Q(refund_records__created_at__lt=end)
                   | Q(created_at__gte=start, created_at__lt=end)
                   | Q(payment_status__in=(*PAID_STATES, 'REFUNDED'),
                       direct_payment__isnull=True, is_gift_snapshot=False))
    else:
        sources = (Q(salary_activities__is_void=False, salary_activities__performed_at__gte=start,
                     salary_activities__performed_at__lt=end) | Q(created_at__gte=start, created_at__lt=end))
    list(Order.objects.select_for_update().filter(sources).order_by('id').values_list('id', flat=True).distinct())
    from wallet.models import Transaction
    recharge_ids = SalaryActivity.objects.filter(is_void=False, kind='RECHARGE').filter(
        Q(recognition_month=month) | Q(recognition_month__isnull=True, performed_at__gte=start,
                                       performed_at__lt=end)).values_list('recharge_id', flat=True)
    locked_recharges = list(RechargeRecord.objects.select_for_update().filter(pk__in=recharge_ids)
                            .values_list('recharge_tx_id', flat=True))
    list(Transaction.objects.select_for_update().filter(pk__in=locked_recharges).values_list('pk', flat=True))
    data = calculate_payroll(month)
    if supplied != data['fingerprint']:
        raise PayrollConflict('业绩、退款或薪资配置已变化，请重新预览核对后确认')
    if not data['can_confirm']:
        raise PayrollConflict('薪资仍有待核对项，不能按不完整金额确认')
    row = PayrollRun.objects.create(month=month, fingerprint=supplied, snapshot=data,
        confirmed_by=local_actor, confirmed_by_platform_id=platform_id,
        platform_request_id=request_id, platform_request_hash=request_hash)
    if refund_accounting_enabled():
        from .payroll_refund_ledger import attach_refund_deductions
        for line in data['lines']:
            attach_refund_deductions(row, line['account'])
    return row, {**data, 'status': 'CONFIRMED', 'can_confirm': False,
                 'run_id': row.pk, 'confirmed_at': row.confirmed_at.isoformat()}, True


class PayrollConfirmView(APIView):
    permission_classes = [IsConsoleUser]

    @transaction.atomic
    def post(self, request):
        require(request, 'salary:confirm')
        month = parse_month(request.data.get('month'))
        supplied = serializers.CharField(min_length=64, max_length=64).run_validation(request.data.get('fingerprint'))
        _, result, _ = confirm_payroll_locked(month, supplied, local_actor=request.account)
        return Response({'code': 0, 'data': result})


class PayrollRunsView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        require(request, 'salary:view')
        qs = PayrollRun.objects.select_related('confirmed_by').all()
        if request.query_params.get('month'):
            qs = qs.filter(month=parse_month(request.query_params['month']))
        rows = [{'id': row.pk, 'month': row.month.strftime('%Y-%m'), 'fingerprint': row.fingerprint,
                 'confirmed_at': row.confirmed_at.isoformat(),
                 'confirmed_by_name': name_of(row.confirmed_by) if row.confirmed_by_id else f'平台账号 {row.confirmed_by_platform_id}',
                 'snapshot': row.snapshot} for row in qs[:120]]
        return Response({'code': 0, 'data': {'list': rows, 'total': qs.count()}})


class PayrollEmployeesView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        require(request, 'salary:view')
        qs = employee_queryset()
        if request.query_params.get('include_inactive') == '1':
            from .payroll_models import PayrollSeparationSettlement
            unreturned = PayrollSeparationSettlement.objects.filter(account_id=OuterRef('pk'),
                reemployment__isnull=True)
            qs = ClubAccount.objects.filter(account_type='STAFF', salary_assignments__isnull=False).exclude(
                Exists(unreturned)).distinct().order_by('id')
        keyword = request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(username__icontains=keyword) | Q(nickname__icontains=keyword) | Q(account_no__icontains=keyword))
        paginator = ConsolePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        rows = [{'id': row.pk, 'name': name_of(row), 'username': row.username, 'account_no': row.account_no,
                 'is_active': row.is_active and row.can_login} for row in page]
        return paginator.get_paginated_response(rows)


class PayrollSeparationSettlementView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        require(request, 'salary:view')
        if request.query_params.get('kind') == 'settlements':
            from .payroll_models import PayrollSeparationSettlement
            state = request.query_params.get('state', 'pending_reemployment')
            if state not in ('pending_reemployment', 'reemployed', 'all'):
                raise serializers.ValidationError({'state': '不支持的结清状态'})
            after_id = serializers.IntegerField(min_value=0).run_validation(request.query_params.get('after_id', 0))
            qs = PayrollSeparationSettlement.objects.filter(pk__gt=after_id).select_related(
                'account', 'final_run', 'confirmed_by', 'reemployment').order_by('pk')
            if request.query_params.get('account_id'):
                account_id = serializers.IntegerField(min_value=1).run_validation(request.query_params['account_id'])
                qs = qs.filter(account_id=account_id)
            if state != 'all':
                qs = qs.filter(reemployment__isnull=state == 'pending_reemployment')
            rows = list(qs[:101])
            items = []
            for row in rows[:100]:
                reemployment = getattr(row, 'reemployment', None)
                items.append({'id': row.pk, 'account_id': row.account_id,
                              'account_name': name_of(row.account), 'account_no': row.account.account_no,
                              'final_run_id': row.final_run_id,
                              'month': row.final_run.month.strftime('%Y-%m'),
                              'settled_fen': row.settled_fen, 'reference': row.reference,
                              'note': row.note, 'confirmed_by_id': row.confirmed_by_id,
                              'confirmed_at': row.confirmed_at,
                              'reemployment_id': reemployment.pk if reemployment else None,
                              'reemployed_at': reemployment.created_at if reemployment else None})
            return Response({'code': 0, 'data': {'list': items, 'has_more': len(rows) > 100,
                'next_after_id': rows[99].pk if len(rows) > 100 else None}})
        if request.query_params.get('kind') == 'reemployment_refund':
            from .payroll_models import PayrollReemploymentRefundWaiver
            settlement_id = serializers.IntegerField(min_value=1).run_validation(request.query_params.get('settlement_id'))
            after_id = serializers.IntegerField(min_value=0).run_validation(request.query_params.get('after_id', 0))
            rows = list(PayrollReemploymentRefundWaiver.objects.filter(settlement_id=settlement_id,
                pk__gt=after_id).select_related('settlement').order_by('pk')[:101])
            return Response({'code': 0, 'data': {'list': [{'id': row.pk, 'settlement_id': row.settlement_id,
                'account_id': row.settlement.account_id, 'plan_id': row.plan_id, 'source_run_id': row.source_run_id,
                'refund_id': row.refund_id, 'addon_refund_id': row.addon_refund_id,
                'source_payment_id': row.source_payment_id, 'waived_performance_fen': None,
                'reason': row.reason, 'created_at': row.created_at} for row in rows[:100]],
                'has_more': len(rows) > 100, 'next_after_id': rows[99].pk if len(rows) > 100 else None}})
        from .payroll_models import PayrollPerformanceSeparationWaiver
        settlement_id = serializers.IntegerField(min_value=1).run_validation(request.query_params.get('settlement_id'))
        after_id = serializers.IntegerField(min_value=0).run_validation(request.query_params.get('after_id', 0))
        rows = list(PayrollPerformanceSeparationWaiver.objects.filter(
            settlement_id=settlement_id, pk__gt=after_id).select_related(
                'adjustment', 'settlement').order_by('pk')[:101])
        data = [{'id': row.pk, 'settlement_id': row.settlement_id,
                 'account_id': row.settlement.account_id, 'original_run_id': row.adjustment.original_run_id,
                 'refund_id': row.adjustment.refund_id, 'addon_refund_id': row.adjustment.addon_refund_id,
                 'amount_fen': row.amount_fen, 'reason': row.reason, 'created_at': row.created_at}
                for row in rows[:100]]
        return Response({'code': 0, 'data': {'list': data, 'has_more': len(rows) > 100,
            'next_after_id': rows[99].pk if len(rows) > 100 else None}})

    def post(self, request):
        require(request, 'salary:confirm')
        if request.data.get('action') == 'set_reemployment_plan':
            from .payroll_refund_ledger import register_reemployment_plan
            entry = register_reemployment_plan(
                reemployment_id=serializers.IntegerField(min_value=1).run_validation(request.data.get('reemployment_id')),
                plan_id=serializers.IntegerField(min_value=1).run_validation(request.data.get('plan_id')),
                revision=serializers.IntegerField(min_value=1).run_validation(request.data.get('revision')),
                actor=request.account)
            return Response({'code': 0, 'data': {'id': entry.pk, 'reemployment_id': entry.reemployment_id,
                'plan_id': entry.plan_id, 'plan_revision': entry.plan_revision,
                'plan_snapshot': entry.plan_snapshot, 'effective_at': entry.effective_at}})
        if request.data.get('action') == 'reemploy':
            from .payroll_refund_ledger import register_payroll_reemployment
            account_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('account_id'))
            separation_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('settlement_id'))
            entry = register_payroll_reemployment(account_id=account_id, separation_id=separation_id,
                actor=request.account, reason=request.data.get('reason'))
            return Response({'code': 0, 'data': {'id': entry.pk, 'account_id': entry.account_id,
                'settlement_id': entry.separation_id, 'reason': entry.reason, 'created_at': entry.created_at}})
        from .payroll_refund_ledger import record_offline_separation_settlement
        row = record_offline_separation_settlement(run_id=request.data.get('run_id'),
            account_id=request.data.get('account_id'), actor=request.account, settled_fen=request.data.get('settled_fen'),
            reference=request.data.get('reference', ''), note=request.data.get('note', ''),
            all_wages_settled=request.data.get('all_wages_settled'))
        return Response({'code': 0, 'data': {'id': row.pk, 'account_id': row.account_id,
            'final_run_id': row.final_run_id, 'settled_fen': row.settled_fen,
            'waived_fen': sum(row.writeoffs.values_list('amount_fen', flat=True)), 'confirmed_at': row.confirmed_at}})


def _payroll_source_pages(query):
    """Read the full source in its existing newest-ID-first order."""
    cursor = None
    while True:
        current = query if cursor is None else query.filter(pk__lt=cursor)
        page = list(current.order_by('-pk')[:256])
        if not page:
            return
        yield from page
        cursor = page[-1].pk


class PayrollSourcesView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        require(request, 'salary:view')
        kind = serializers.ChoiceField(choices=['DISPATCH', 'RECHARGE', 'AFTERSALE']).run_validation(request.query_params.get('kind'))
        keyword = request.query_params.get('keyword', '').strip()
        source_id = request.query_params.get('source_id')
        source_id = serializers.IntegerField(min_value=1).run_validation(source_id) if source_id else None
        attributed = set(SalaryActivity.objects.filter(is_void=False).values_list('source_key', flat=True))
        rows = []
        if kind == 'DISPATCH':
            states = (*PAID_STATES, 'REFUNDED') if refund_accounting_enabled() else PAID_STATES
            qs = Order.objects.filter(payment_status__in=states, direct_payment__isnull=True,
                                      is_gift_snapshot=False)
            if keyword:
                qs = qs.filter(Q(order_no__icontains=keyword) | Q(customer_account__nickname__icontains=keyword))
            if source_id:
                qs = qs.filter(pk=source_id)
            for row in _payroll_source_pages(qs):
                moment = dispatch_time(row)
                if moment:
                    rows.append({'id': row.pk, 'label': f'{row.order_no} · {row.service_name_snapshot}',
                                 'performed_at': moment.isoformat(), 'source_amount_fen': dispatch_source_amount_fen(row),
                                 'attributed': f'ORDER:{row.pk}' in attributed})
        elif kind == 'RECHARGE':
            qs = eligible_recharges().select_related('account', 'recharge_tx')
            if keyword:
                qs = qs.filter(Q(trade_no__icontains=keyword) | Q(account__nickname__icontains=keyword))
            if source_id:
                qs = qs.filter(pk=source_id)
            for row in _payroll_source_pages(qs):
                rows.append({'id': row.pk, 'label': f'充值#{row.pk} · {name_of(row.account) if row.account else "历史老板"} · {row.trade_no}',
                             'performed_at': row.recharge_tx.created_at.isoformat(), 'source_amount_fen': raw_to_fen(row.amount),
                             'attributed': f'RECHARGE:{row.pk}' in attributed})
        else:
            qs = AfterSale.objects.filter(order__direct_payment__isnull=True).select_related('order')
            if keyword:
                qs = qs.filter(order__order_no__icontains=keyword)
            if source_id:
                qs = qs.filter(pk=source_id)
            for row in _payroll_source_pages(qs):
                for cycle in aftersale_cycles(row):
                    rows.append({'id': row.pk, 'label': f'售后#{row.pk} · {row.order.order_no} · {cycle["status"]}',
                                 **cycle, 'source_amount_fen': None,
                                 'attributed': f'AFTERSALE:{row.pk}:{cycle["cycle_key"]}' in attributed})
        return Response({'code': 0, 'data': {'list': rows, 'total': len(rows),
                                           'note': '可用编号或关键词查找。归属是人工核实业绩，不改原资金和售后状态。'}})


class OperatingRoiView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        require(request, 'roi:view')
        first = serializers.DateField().run_validation(request.query_params.get('start_date'))
        last = serializers.DateField().run_validation(request.query_params.get('end_date'))
        if first > last or (last - first).days > 366:
            raise serializers.ValidationError('日期范围须按先后排列且不超过367天')
        return Response({'code': 0, 'data': operating_roi(first, last)})
