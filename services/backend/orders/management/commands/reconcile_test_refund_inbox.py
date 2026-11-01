"""Explicit isolated test recovery; never register as a production scheduler."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from orders.direct_refund_reconcile import scan_test_refund_inbox


class Command(BaseCommand):
    help = 'Process saved refund notices using an explicitly configured isolated test gateway.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        gateway = getattr(settings, 'WECHAT_DIRECT_REFUND_TEST_GATEWAY', None)
        try:
            results = scan_test_refund_inbox(gateway=gateway, limit=options['limit'])
        except (ValueError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc
        counts = {state: sum(1 for _, result in results if result == state)
                  for state in ('DONE', 'PENDING', 'REVIEW', 'retry')}
        self.stdout.write(' '.join(f'{state}={count}' for state, count in counts.items()))
