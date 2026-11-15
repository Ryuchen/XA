from unittest.mock import patch

from django.test import TestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.services import _credit_wallet
from orders.tests.factories import make_order, make_user
from stores.db import transaction
from wallet.models import Transaction


class CreditCommitLogTest(TestCase):
    def test_deferred_money_log_is_written_only_for_committed_credit(self):
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        order = make_order(user)
        args = dict(order=order, amount=100, tx_type=Transaction.TxType.INCOME,
                    remark='隔离测试入账', event='test.credit', trace_id='test-trace',
                    user_id=user.pk, account_id=account.pk, log_after_commit=True)
        with patch('orders.services.log_money_event') as log:
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        _credit_wallet(**args)
                        raise RuntimeError('回滚测试')
                except RuntimeError:
                    pass
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                with transaction.atomic():
                    credit = _credit_wallet(**args)
                log.assert_not_called()
            log.assert_called_once()
            self.assertEqual(log.call_args.args, ('test.credit',))
            self.assertEqual(log.call_args.kwargs['amount'], 100)
            self.assertEqual(Transaction.objects.get(order=order, tx_type='INCOME').pk, credit.pk)
