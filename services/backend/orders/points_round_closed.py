"""Read-only closed segments from already verified, processed postings."""
from rest_framework.exceptions import ValidationError


def from_postings(postings):
    result = {}
    for posting in postings:
        if not posting.termination_processed_at:
            raise ValidationError('尚未结案的退款不能关闭履约段')
        chosen = posting.proposal.affected_segment_ids
        if chosen is None:
            participants = posting.participants
            entries = (participants.get('facts', {}).get('segments', []) if isinstance(participants, dict)
                       else participants if isinstance(participants, list) else [])
            grouped = {account: [] for account in posting.proposal.affected_accounts}
            for item in entries:
                account = item.get('account_id')
                if item.get('started_at') and account in grouped:
                    grouped[account].append(item.get('id') if isinstance(participants, dict) else item.get('segment_id'))
            if any(len(ids) != 1 for ids in grouped.values()):
                raise ValidationError('历史退款未能唯一证明受影响履约段')
            chosen = [ids[0] for ids in grouped.values()]
        if (not isinstance(chosen, list) or not chosen or len(set(chosen)) != len(chosen)
                or any(type(pk) is not int or pk <= 0 or pk in result for pk in chosen)):
            raise ValidationError('已终止履约段凭证异常或重复')
        result.update({pk: posting.pk for pk in chosen})
    return result
