from collections import Counter

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from identifiers.services import ensure_entity_number, entity_prefix_for, normalize_entity_numbers, numbered_model
from stores.context import store_context
from stores.models import Store


class Command(BaseCommand):
    help = 'Backfill stable prefixed entity numbers in one isolated store database.'

    def add_arguments(self, parser):
        parser.add_argument('--store', default='shenglang')
        parser.add_argument('--database')

    def handle(self, *args, **options):
        store = Store.objects.filter(slug=options['store']).first()
        if store is None:
            raise CommandError('Store not found')
        alias = options['database'] or store.database_alias
        counts = Counter()
        with store_context(store):
            prefix = entity_prefix_for(store)
            normalize_entity_numbers(prefix, alias)
            for model in apps.get_models():
                if not numbered_model(model):
                    continue
                manager = model._meta.default_manager.using(alias)
                for instance in manager.all().iterator(chunk_size=200):
                    code = ensure_entity_number(instance, using=alias, prefix=prefix)
                    if code:
                        counts[model._meta.label_lower] += 1
        self.stdout.write(self.style.SUCCESS(str({
            'store': store.slug,
            'database': alias,
            'prefix': prefix,
            'processed': sum(counts.values()),
            'models': len(counts),
        })))
