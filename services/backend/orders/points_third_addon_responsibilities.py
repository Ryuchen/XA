"""Unpaid add-on shares after an actual third main refund."""
from rest_framework.exceptions import ValidationError


def current(payment, *, lock=False, before=None, with_share_id=False, exclude_refund_id=None):
    from .addon_handover import current_responsibilities
    handover, rows = current_responsibilities(payment, lock=lock, before=before,
        with_share_id=True, exclude_refund_id=exclude_refund_id, include_main_third=True)
    rows = [dict(row) for row in rows]
    if not with_share_id:
        for row in rows:
            row.pop('share_id', None)
    return handover, [row for row in rows if row['basis'] or row['income']]
