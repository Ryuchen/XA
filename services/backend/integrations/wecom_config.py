"""Read only the current store's configured robot, never an environment recipient."""
from dataclasses import dataclass, field
from stores.context import current_store
from .kook_config import decrypt_token
from .wecom import valid_webhook


@dataclass(frozen=True)
class WecomRuntimeConfig:
    enabled: bool = False
    webhook: str = field(default='', repr=False)
    auto_new_orders: bool = False
    auto_rejected_orders: bool = False

    @property
    def ready(self):
        return self.enabled and valid_webhook(self.webhook)


def get_wecom_config():
    if current_store() is None:
        return WecomRuntimeConfig()
    from console.models import WecomDispatchConfig
    row = WecomDispatchConfig.objects.filter(pk=1).first()
    if row is None:
        return WecomRuntimeConfig()
    return WecomRuntimeConfig(row.enabled, decrypt_token(row.webhook_ciphertext),
                              row.auto_new_orders, row.auto_rejected_orders)
