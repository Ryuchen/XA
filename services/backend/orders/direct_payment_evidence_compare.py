"""Pure comparison of a stored payment with a second attributed observation."""

from django.utils import timezone

from .direct_payment_delivery_models import DirectPaymentOutbox
from .direct_payment_models import DirectPaymentIntent


def flag_payment_evidence_risk(intent):
    """Call only after locking order then intent and matching all identities."""
    if not intent.evidence_risk_at:
        intent.evidence_risk_at = timezone.now()
    if intent.status == DirectPaymentIntent.Status.PAID:
        intent.status = DirectPaymentIntent.Status.REVIEW
        intent.review_reason = 'QUERY_PAYMENT_EVIDENCE_RISK'
    intent.save(update_fields=['evidence_risk_at', 'status', 'review_reason', 'updated_at'])
    DirectPaymentOutbox.objects.filter(intent=intent).update(
        status=DirectPaymentOutbox.Status.REVIEW, last_error='PAYMENT_EVIDENCE_REVIEW',
        lease_token='', lease_until=None)


def contradicts_saved_payment(intent, evidence):
    return bool(intent.transaction_id and (
        intent.transaction_id != evidence.transaction_id or intent.paid_at != evidence.paid_at
        or intent.amount_fen != evidence.amount_fen or intent.currency != evidence.currency
        or (intent.payer_total_fen is not None and evidence.payer_total_fen is not None
            and intent.payer_total_fen != evidence.payer_total_fen)
        or (intent.payer_currency is not None and evidence.payer_currency is not None
            and intent.payer_currency != evidence.payer_currency)
        or (intent.promotion_detail is not None and evidence.promotion_detail is not None
            and intent.promotion_detail != evidence.promotion_detail)
        or (intent.promotion_detail is None and evidence.promotion_detail)))
