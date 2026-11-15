"""Avoid treating multiple independently unverified reviews as one proof."""


def is_only_pending(account_id, business_cycle, *, kind, review_id):
    from .models import LicenseAddonBenefitsReview, LicenseRefundVipReview, LicenseGiftVipReview
    queries = {
        'addon': LicenseAddonBenefitsReview.objects.filter(consumption__account_id=account_id,
            consumption__business_cycle=business_cycle, vip_recalculation_pending=True),
        'refund': LicenseRefundVipReview.objects.filter(decision__account_id=account_id,
            decision__business_cycle=business_cycle, vip_recalculation_pending=True),
        'gift': LicenseGiftVipReview.objects.filter(decision__account_id=account_id,
            decision__business_cycle=business_cycle, vip_recalculation_pending=True),
    }
    return all(list(query.values_list('pk', flat=True)[:2]) == ([review_id] if name == kind else [])
               for name, query in queries.items())
