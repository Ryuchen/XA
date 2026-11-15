from django.apps import AppConfig


class StoresConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'stores'
    verbose_name = '门店与租户'

    def ready(self):
        from django.db.models.signals import post_migrate
        from .signals import ensure_default_store
        post_migrate.connect(ensure_default_store, sender=self, dispatch_uid='stores.ensure_default_store')
