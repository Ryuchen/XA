"""Re-read the source and effective terms of post-reemployment late wages."""
from rest_framework.exceptions import ValidationError

from .payroll_calculation import aftersale_cycles, round_ratio
from .payroll_models import PayrollReemploymentPlan


def verify_late_reemployment_activity(activity):
    if activity.kind not in ('AFTERSALE', 'EXAM') or not activity.recognition_month:
        return
    snapshot = activity.source_snapshot
    terms = snapshot.get('late_compensation') if isinstance(snapshot, dict) else None
    if not isinstance(terms, dict) or not terms.get('reemployment_id'):
        return
    plan = PayrollReemploymentPlan.objects.select_related('reemployment__separation__final_run').filter(
        pk=terms.get('reemployment_plan_id'), reemployment_id=terms['reemployment_id']).first()
    if not plan:
        raise ValidationError('重入薪资方案来源缺失')
    reemployment = plan.reemployment
    latest = PayrollReemploymentPlan.objects.filter(reemployment=reemployment,
        effective_at__lte=activity.performed_at).order_by('-effective_at', '-pk').first()
    if (reemployment.account_id != activity.account_id or not latest or latest.pk != plan.pk
            or reemployment.created_at > plan.effective_at or plan.effective_at > activity.performed_at
            or terms.get('plan_effective_at') != plan.effective_at.isoformat()
            or terms.get('original_run_id') != reemployment.separation.final_run_id
            or terms.get('original_month') != reemployment.separation.final_run.month.strftime('%Y-%m')
            or terms.get('recognition_month') != activity.recognition_month.strftime('%Y-%m')
            or terms.get('plan_snapshot') != plan.plan_snapshot):
        raise ValidationError('重入薪资方案或生效时间与业绩不一致')
    if activity.kind == 'AFTERSALE':
        if not activity.aftersale_id:
            raise ValidationError('售后业绩来源缺失')
        cycles = [cycle for cycle in aftersale_cycles(activity.aftersale)
                  if activity.source_key == f"AFTERSALE:{activity.aftersale_id}:{cycle['cycle_key']}"]
        if (len(cycles) != 1 or any(snapshot.get(key) != cycles[0].get(key)
                for key in ('cycle_key', 'performed_at', 'status', 'operator_id'))):
            raise ValidationError('售后业绩与原结案周期不一致')
        from rest_framework import serializers
        if serializers.DateTimeField().run_validation(cycles[0]['performed_at']) != activity.performed_at:
            raise ValidationError('售后业绩时间与原结案周期不一致')
        gross, deduction = plan.plan_snapshot['aftersale_fee_fen'], 0
    else:
        if (not activity.client_request_id or activity.source_key != f'EXAM:{activity.client_request_id}'
                or not activity.request_fingerprint or not activity.created_by_id
                or snapshot.get('verified_manually') is not True or snapshot.get('count') != 1):
            raise ValidationError('考试业绩原核实记录不完整')
        gross = plan.plan_snapshot['exam_fee_fen']
        deduction = round_ratio(gross, plan.plan_snapshot.get('exam_commission_bps', 0), 10000)
    if (terms.get('gross_fen'), terms.get('deduction_fen'), terms.get('amount_fen')) != (
            gross, deduction, gross - deduction):
        raise ValidationError('重入工资补发金额与原方案不一致')
