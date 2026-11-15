"""Existing financial regression suites on isolated MySQL.

Their fixtures issue role JWTs directly and predate the OTP credential fixture.
OTP is therefore outside this suite; it is exercised by the separate real-OTP
acceptance server and authentication tests. This setting is test-runner only.
"""
import sys
if 'test' not in sys.argv:
    raise RuntimeError('fronted_business_test_settings is only for Django tests')
from .fronted_mysql_test_settings import *  # noqa: F403

LOGIN_OTP_REQUIRED = False
