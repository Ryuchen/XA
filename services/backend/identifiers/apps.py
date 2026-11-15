from django.apps import AppConfig


class IdentifiersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'identifiers'
    verbose_name = '门店实体编号'

    def ready(self):
        from . import signals  # noqa: F401
