"""Keep historical RunPython ORM calls on the schema editor's target database.

Django's --database selects schema_editor, but historical migrations often use
Model.objects without .using(). Routing them through the same context is
required both for deployment and Django's multi-database test DB creation.
"""
from types import SimpleNamespace

from django.core.management.commands.migrate import Command as DjangoMigrateCommand

from stores.context import current_store, store_context


class Command(DjangoMigrateCommand):
    def handle(self, *args, **options):
        alias = options.get('database', 'default')
        store = current_store()
        if store is None or store.database_alias != alias:
            # No directory query here: the directory may not have been created
            # yet while migrating an empty primary database.
            store = SimpleNamespace(database_alias=alias,
                                    slug='shenglang' if alias == 'default' else alias.replace('_', '-'),
                                    pk=None, id=None, name='声浪电竞' if alias == 'default' else alias,
                                    status='ACTIVE', is_active=True)
        with store_context(store):
            return super().handle(*args, **options)
