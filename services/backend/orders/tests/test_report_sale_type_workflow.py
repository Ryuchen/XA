"""A catalog import cannot reclassify a completed service or gift order."""
import io
import json
import subprocess
from pathlib import Path

from django.test import override_settings
from rest_framework.test import APITestCase
from common.mysql_test_support import call_test_catalog_import

from orders.management.commands.seed_club_catalog import CATALOG
from orders.models import Order
from orders.tests.factories import make_service, make_service_category
from orders.tests import test_release_flow as release_flow


@override_settings(DEBUG=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False)
class ReportSaleTypeWorkflowTest(APITestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge
    new_order = release_flow.ReleaseFlowTest.new_order
    finish = release_flow.ReleaseFlowTest.finish
    settle_after_window = release_flow.ReleaseFlowTest.settle_after_window

