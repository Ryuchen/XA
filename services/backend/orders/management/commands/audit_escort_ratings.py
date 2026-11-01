"""Compare stored escort rating summaries with their actual evaluation rows."""
from django.core.management.base import BaseCommand
from django.db import transaction

from orders.ratings import escort_rating_summary
from users.models import EscortProfile


class Command(BaseCommand):
    help = 'Audit escort ratings; optionally repair averages when review counts agree.'

    def add_arguments(self, parser):
        parser.add_argument('--details', action='store_true', help='Show mismatched profile IDs and values.')
        parser.add_argument('--repair-averages', action='store_true',
                            help='Recalculate differing averages only when stored and actual counts agree.')

    def handle(self, *args, **options):
        scanned = mismatched = missing_reviews = correctable = repaired = 0
        profiles = EscortProfile.objects.only('id', 'user_id', 'account_id', 'rating_count', 'rating_avg').order_by('id')
        for initial in profiles.iterator(chunk_size=200):
            scanned += 1
            with transaction.atomic():
                profile = (EscortProfile.objects.select_for_update().get(pk=initial.pk)
                           if options['repair_averages'] else initial)
                count, average = escort_rating_summary(profile)
                if (profile.rating_count, profile.rating_avg) == (count, average):
                    continue
                mismatched += 1
                if profile.rating_count and not count:
                    missing_reviews += 1
                can_repair = profile.rating_count == count
                if can_repair:
                    correctable += 1
                if options['details']:
                    self.stdout.write(f'profile={profile.pk} stored={profile.rating_count}/{profile.rating_avg} actual={count}/{average}')
                if options['repair_averages'] and can_repair:
                    profile.rating_avg = average
                    profile.save(update_fields=['rating_avg'])
                    repaired += 1
        self.stdout.write(f'scanned={scanned} mismatched={mismatched} stored_without_reviews={missing_reviews} '
                          f'average_repairable={correctable} repaired={repaired}')
