"""Two fresh disposable SQLite files shared with the browser ASGI process."""
from .platform_store_browser_settings import *  # noqa: F403

DATABASES['store_new']['TEST'] = {'NAME': DATABASES['store_new']['NAME']}  # noqa: F405
