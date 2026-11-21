from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from orders.aftersale import AfterSaleViewSet


class PointsTransferFirstFundingPreviewTests(SimpleTestCase):
    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=False)
    def test_first_transfer_funding_requires_the_formal_path_and_enough_recovery(self):
        order = Mock(pk=1)
        order.refund_records.exists.return_value = False
        case = SimpleNamespace(pk=2, order=order, client_request_id='transfer-case', addon_payment_id=None)
        request = SimpleNamespace(query_params={'refund_units': '150'}, account=object())
        view = AfterSaleViewSet()
        calculations = {'coin_units': 120, 'points_hundredths': 100,
                        'conditional_funding_if_recovered': {'remaining_units': 80}}
        with (patch.object(view, 'get_object', return_value=case),
              patch('stores.db.Atomic.__enter__'), patch('stores.db.Atomic.__exit__', return_value=False),
              patch('orders.points_business_access.case_refund_scope', return_value=nullcontext()),
              patch('orders.negotiation_proposals._lock_case', return_value=(order, case)),
              patch('orders.negotiation_proposals.enabled', return_value=True),
              patch('orders.points_service_payment.needs_integrated_processing', return_value=True),
              patch('orders.models.OrderOriginalShareTransfer.objects.filter') as transfers,
              patch('orders.points_business_access.permits') as permits,
              patch('orders.points_negotiation_termination.available_for_order') as available,
              patch('orders.refunds.authorized_refund', return_value=True),
              patch('orders.points_transfer_refund_preview.preview_locked') as preview):
            transfers.return_value.exists.return_value = True
            for label, formal, enabled, people, deposit, short, expected in (
                ('formal with outstanding store funding', True, True, 1, 0, 0, (True, False, True)),
                ('formal switch disabled', True, False, 1, 0, 0, (True, True, False)),
                ('provider amount short', True, True, 1, 0, 30, (True, False, False)),
                ('deposit recovery disabled', True, True, 1, 30, 0, (True, False, False)),
                ('three affected providers', True, False, 3, 0, 0, (True, True, False)),
                ('isolated preview', False, True, 1, 0, 0, (None, True, False)),
            ):
                with self.subTest(label=label):
                    permits.side_effect = lambda current, *, phase, kind: (
                        formal and current is order and phase == 'refund' and kind == 'transfer')
                    available.return_value = enabled
                    preview.return_value = {**calculations, 'estimated_sufficient': not short,
                        'provider_recovery_preview': [{'account_id': 3 + i, 'pending_units': 150 // people - deposit - short,
                            'wallet_units': 0, 'deposit_units': deposit, 'shortfall_units': short} for i in range(people)]}
                    result = view.negotiation_funding(request).data
                    data = result['data']
                    self.assertEqual((data.get('execution_check_required'), data['read_only_points'],
                                      data['executable']), expected)
                    self.assertEqual(data['book_amounts_sufficient'], not short)
                    self.assertEqual((data['refund_coin_units'], data['refund_points_hundredths']), (120, 100))
                    if people > 2:
                        self.assertIn('最多支持两名', result['msg'])
