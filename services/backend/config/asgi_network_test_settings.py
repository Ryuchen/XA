"""Opt-in file-backed database shared with an isolated ASGI test process."""
import os
from pathlib import Path

from .migration_test_settings import *  # noqa: F403

_database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
if not _database:
    raise RuntimeError('XA_ASGI_NETWORK_TEST_DB must name an isolated SQLite file')
_path = Path(_database).resolve()
_test_dir = (Path(BASE_DIR).parent.parent / '.tools').resolve()  # noqa: F405
if _path.parent != _test_dir or not _path.name.startswith('asgi-reservation-') or _path.suffix != '.sqlite3':
    raise RuntimeError('ASGI network database must use .tools/asgi-reservation-*.sqlite3')
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': _database,
                         'TEST': {'NAME': _database}}}
_media_root = os.environ.get('XA_ASGI_NETWORK_TEST_MEDIA_ROOT')
if _media_root:
    _media_path = Path(_media_root).resolve()
    if not _media_path.is_dir() or not _media_path.name.startswith('xa-asgi-media-'):
        raise RuntimeError('ASGI test media must use an existing xa-asgi-media-* directory')
    MEDIA_ROOT = _media_path
LEGACY_FORCE_AUTH_COMPAT = False
KOOK_ENABLED = False
WECOM_DISPATCH_ENABLED = False
# Only the explicitly opted-in isolated browser test enables this unfinished
# payment flow; it is not a deployment configuration switch.
BOSS_FUNDING_SELECTION_ENABLED = (os.environ.get('XA_SERVICE_FUNDING_BROWSER') == '1'
                                  or os.environ.get('XA_NEGOTIATION_EXECUTION_BROWSER') == '1')
PRESTART_HANDOFF_ENABLED = os.environ.get('XA_PRESTART_HANDOFF_BROWSER') == '1'
GAME_COMMISSION_PAYMENT_ENABLED = (os.environ.get('XA_PRESTART_LEGACY_BROWSER') == '1'
                                   or os.environ.get('XA_SEGMENT_GAME_TRANSFER_BROWSER') == '1')
PRESTART_SCOPE_SNAPSHOT_ENABLED = (os.environ.get('XA_SEGMENT_GAME_TRANSFER_BROWSER') == '1'
                                   or os.environ.get('XA_SEGMENT_STANDARD_TRANSFER_BROWSER') == '1')
SEGMENT_GAME_TRANSFER_ENABLED = os.environ.get('XA_SEGMENT_GAME_TRANSFER_BROWSER') == '1'
SEGMENT_STANDARD_TRANSFER_ENABLED = os.environ.get('XA_SEGMENT_STANDARD_TRANSFER_BROWSER') == '1'
ORIGINAL_ATTRIBUTION_ENABLED = os.environ.get('XA_ORIGINAL_TRANSFER_BROWSER') == '1'
SEGMENT_ECONOMICS_CAPTURE_ENABLED = os.environ.get('XA_SEGMENT_FINANCE_BROWSER') == '1'
SEGMENT_REPORT_ENABLED = os.environ.get('XA_SEGMENT_REPORT_BROWSER') == '1'
SEGMENT_SETTLEMENT_ENABLED = os.environ.get('XA_SEGMENT_SETTLEMENT_BROWSER') == '1'
SEGMENT_REFUND_PREVIEW_ENABLED = os.environ.get('XA_SEGMENT_REFUND_PREVIEW_BROWSER') == '1'
NEGOTIATION_PROPOSAL_ENABLED = (os.environ.get('XA_NEGOTIATION_PROPOSAL_BROWSER') == '1'
                                or os.environ.get('XA_NEGOTIATION_EXECUTION_BROWSER') == '1')
NEGOTIATION_REFUND_EXECUTION_ENABLED = os.environ.get('XA_NEGOTIATION_EXECUTION_BROWSER') == '1'
NEGOTIATION_ZERO_EXECUTION_ENABLED = os.environ.get('XA_NEGOTIATION_EXECUTION_BROWSER') == '1'
NEGOTIATION_FOLLOWUP_ENABLED = os.environ.get('XA_NEGOTIATION_FOLLOWUP_BROWSER') == '1'
NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED = os.environ.get('XA_NEGOTIATION_COMPLETED_BROWSER') == '1'
NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING = os.environ.get('XA_NEGOTIATION_COMPLETED_BROWSER') == '1'
SERVICE_ORDER_POINTS_CANCEL_TESTING = os.environ.get('XA_SERVICE_POINTS_CANCEL_BROWSER') == '1'
if SEGMENT_ECONOMICS_CAPTURE_ENABLED:
    ORDER_SETTLEMENT_MODE = 'REPORT_APPROVAL'
DISCOUNT_OPTIONS_PREVIEW_ENABLED = os.environ.get('XA_DISCOUNT_BROWSER') == '1'
DISCOUNT_COMBINATIONS_PAYMENT_ENABLED = os.environ.get('XA_DISCOUNT_BROWSER') == '1'
VIP_MONTHLY_DECISIONS_ENABLED = os.environ.get('XA_VIP_TERMS_BROWSER') == '1'
