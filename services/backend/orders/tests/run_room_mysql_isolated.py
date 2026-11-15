"""Run room lock tests on a new disposable MySQL container, never an app DB.

Explicit opt-in: XA_ROOM_MYSQL_ISOLATED=1.  Uses only a randomly named,
labelled container with tmpfs data and a random loopback port and password.
"""
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path


def main():
    # A Windows console may default to GBK while Django emits Unicode (or a
    # replacement character from a subprocess); never lose the test result.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    if os.environ.get('XA_ROOM_MYSQL_ISOLATED') != '1':
        raise SystemExit('Requires XA_ROOM_MYSQL_ISOLATED=1')
    docker = ['wsl.exe', '-d', 'XA-Ubuntu-24.04', '-u', 'root', '--', 'docker']
    token = secrets.token_hex(6)
    name, user, database = 'xa-room-mysql-'+token, 'xa_test_room', 'xa_test_room_'+token
    root_password, user_password = secrets.token_hex(16), secrets.token_hex(16)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    container_id = None
    try:
        print('Starting disposable MySQL container', flush=True)
        created = subprocess.run(docker + ['run', '-d', '--rm', '--pull', 'never', '--name', name,
            '--label', 'xa-isolated-test='+token, '--tmpfs', '/var/lib/mysql:rw,size=1g', '--memory', '1g',
            '-p', f'127.0.0.1:{port}:3306', '-e', 'MYSQL_ROOT_PASSWORD='+root_password,
            '-e', 'MYSQL_DATABASE='+database, '-e', 'MYSQL_USER='+user, '-e', 'MYSQL_PASSWORD='+user_password,
            'mysql:8.0', '--innodb-buffer-pool-size=128M', '--max-connections=30'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', creationflags=flags)
        if created.returncode:
            raise RuntimeError('Could not start the disposable MySQL container')
        container_id = created.stdout.replace('\x00', '').strip()
        if not re.fullmatch(r'[a-f0-9]{64}', container_id):
            raise RuntimeError('Unexpected container identifier; refusing further operations')
        print('Waiting for isolated MySQL initialization', flush=True)
        import pymysql
        for _ in range(240):
            try:
                # The entrypoint's temporary initialization server may already
                # have created the user yet still be about to restart. The
                # published TCP endpoint is available only on the final server.
                with pymysql.connect(host='127.0.0.1', port=port, user=user, password=user_password,
                                     database=database, connect_timeout=1) as probe_connection:
                    probe_connection.ping()
                break
            except (pymysql.MySQLError, OSError):
                time.sleep(.5)
        else:
            raise RuntimeError('Isolated MySQL did not become ready')
        print('Isolated MySQL is ready; running migration and lock tests', flush=True)
        # This grant exists only in the newly created disposable server. Django
        # needs CREATE/DROP for its own unique test database in that server.
        granted = subprocess.run(docker + ['exec', '-e', 'MYSQL_PWD='+root_password, container_id,
            'mysql', '-uroot', '-e', "GRANT ALL PRIVILEGES ON *.* TO 'xa_test_room'@'%';"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        if granted.returncode:
            raise RuntimeError('Could not grant permissions in the disposable MySQL container')
        env = {**os.environ, 'XA_TEST_MYSQL_DATABASE': database, 'XA_TEST_MYSQL_USER': user,
               'XA_TEST_MYSQL_PASSWORD': user_password, 'XA_TEST_MYSQL_HOST': '127.0.0.1',
               'XA_TEST_MYSQL_PORT': str(port), 'DJANGO_SETTINGS_MODULE': 'config.mysql_test_settings'}
        runner = ("import pymysql,sys; pymysql.install_as_MySQLdb(); "
                  "from django.core.management import execute_from_command_line; "
                  "execute_from_command_line(sys.argv)")
        command = [sys.executable, '-c', runner, 'test',
            'orders.tests.test_room_boss_confirmation_mysql.RoomBossConfirmationMySQLTest',
            'orders.tests.test_room_completion_review_mysql.RoomCompletionReviewMySQLTest',
            'orders.tests.test_room_image_recovery_mysql.RoomImageRecoveryMySQLTest',
            'orders.tests.test_room_negotiation_continuation.RoomNegotiationContinuationTest.test_all_participants_negotiated_termination_finishes_room_without_second_boss_confirmation',
            'orders.tests.test_room_negotiation_continuation.RoomNegotiationContinuationTest.test_zero_refund_agreed_termination_finishes_room_without_extra_confirmation',
            'orders.tests.test_room_negotiation_continuation.RoomNegotiationContinuationTest.test_both_players_terminate_room_through_linked_negotiations',
            'orders.tests.test_room_negotiation_continuation.RoomNegotiationContinuationTest.test_zero_followup_invalidates_pending_confirmation',
            'orders.tests.test_room_negotiation_continuation.RoomNegotiationContinuationTest.test_full_negotiated_refund_closes_and_links_room_objection',
            'orders.tests.test_room_negotiation_continuation.RoomNegotiationContinuationTest.test_zero_negotiated_termination_also_closes_room_objection',
            'orders.tests.test_room_negotiation_continuation.RoomNegotiationContinuationTest.test_refund_audit_failure_restores_room_objection_and_confirmation',
            'orders.tests.test_room_negotiation_continuation.RoomNegotiationContinuationTest.test_partial_negotiated_refund_keeps_room_objection_open',
            'orders.tests.test_legacy_room_completion.LegacyRoomCompletionTests',
            'orders.tests.test_legacy_room_completion.LegacyRoomCompletionMoneyFlowTest',
            'orders.tests.test_legacy_room_completion_mysql.LegacyRoomCompletionMySQLTest',
            'orders.tests.test_legacy_clock_cutover.LegacyClockCutoverTests',
            'orders.tests.test_legacy_clock_cutover.LegacyClockCutoverMoneyFlowTest',
            'orders.tests.test_legacy_clock_cutover_mysql.LegacyClockCutoverMySQLTest',
            'orders.tests.test_legacy_negotiation_guard.LegacyNegotiationGuardTest',
            'orders.tests.test_safebox_settlement_clocks.SafeBoxSettlementClocksTest',
            'orders.tests.test_safebox_settlement_clocks.SafeBoxSettlementApiTest',
            'orders.tests.test_safebox_settlement_clocks.SafeBoxSettlementRaceTest',
            'orders.tests.test_subsidy_agreement.SubsidyAgreementTest',
            'orders.tests.test_subsidy_agreement.SubsidyAgreementApiTest',
            'orders.tests.test_subsidy_agreement.SubsidyAgreementRaceTest',
            'orders.tests.test_subsidy_room_termination.SubsidyRoomTerminationTest',
            'orders.tests.test_subsidy_room_termination.SubsidyRoomTerminationRaceTest',
            'orders.tests.test_safebox_segment_capture.SafeBoxSegmentCaptureTest',
            'orders.tests.test_safebox_segment_capture.SafeBoxGameTransferTest',
            'orders.tests.test_safebox_segment_settlement.SafeBoxSegmentSettlementTest',
            'orders.tests.test_safebox_segment_settlement.SafeBoxSegmentSettlementRaceTest',
            'orders.tests.test_safebox_transfer_negotiation_preview.SafeBoxTransferNegotiationPreviewTest',
            'orders.tests.test_safebox_transfer_agreement_evidence.SafeBoxTransferAgreementEvidenceTest',
            'orders.tests.test_safebox_transfer_agreement_evidence.SafeBoxTransferAgreementEvidenceRaceTest',
            'orders.tests.test_safebox_transfer_agreement_plan.SafeBoxTransferAgreementPlanTest',
            'orders.tests.test_safebox_transfer_agreement_execution.SafeBoxTransferAgreementExecutionTest',
            'orders.tests.test_safebox_transfer_agreement_execution.SafeBoxTransferAgreementExecutionRaceTest',
            'orders.tests.test_safebox_transfer_agreement_settlement.SafeBoxTransferAgreementSettlementTest',
            'orders.tests.test_safebox_transfer_agreement_settlement.SafeBoxTransferAgreementSettlementRaceTest',
            'orders.tests.test_transfer_limit.TransferLimitApiTest',
            'orders.tests.test_transfer_limit.TransferLimitDoubleApiTest',
            'orders.tests.test_transfer_limit.TransferLimitRaceTest',
            'orders.tests.test_safebox_transfer_agreement_api.SafeBoxTransferAgreementApiTest',
            'orders.tests.test_safebox_transfer_agreement_api.SafeBoxTransferAgreementApiRaceTest',
            'wallet.test_deposit_clearance.DepositClearanceEvidenceTest',
            'wallet.test_deposit_clearance.DepositClearanceRefundApiTest',
            'wallet.test_deposit_clearance.CashDepositClearanceApiTest',
            'wallet.test_deposit_clearance.WalletDepositClearanceApiTest',
            'wallet.test_deposit_clearance.DepositClearanceRefundRaceTest',
            'wallet.test_deposit_refund_eligibility.DepositRefundEligibilityTest.test_transferred_out_provider_waits_until_entire_order_ends',
            'wallet.test_deposit_refund_eligibility.DepositRefundEligibilityTest.test_verified_zero_income_report_does_not_remain_pending_forever',
            'orders.tests.test_negotiation_execution.NegotiationExecutionPostingTests.test_deposit_clock_excludes_only_verified_unaffected_teammate',
            'wallet.test_historical_income_aftersale.HistoricalIncomeAftersaleTest',
            'wallet.test_historical_income_aftersale.HistoricalIncomeAftersaleRaceTest',
            'wallet.test_realtime.WalletMutationRealtimeTest.test_approval_and_rejection_notify_after_commit_without_duplicate_money',
            'wallet.test_realtime.WalletMutationRealtimeTest.test_audit_rollback_does_not_publish_or_change_frozen_funds',
            'wallet.test_realtime.WalletMutationRealtimeTest.test_report_and_aftersale_clocks_overlap_and_invalidate_once_at_settlement',
            'orders.tests.test_room_auto_confirmation.RoomAutoConfirmationMoneyFlowTest', '--noinput']
        expected_tests = 175
        if os.environ.get('XA_ROOM_MYSQL_SUITE') == 'grab_opening':
            command = [sys.executable, '-c', runner, 'test',
                       'orders.tests.test_grab_opening_intent.GrabOpeningIntentMySQLTest',
                       'orders.tests.test_grab_opening_intent.GrabOpeningIntentTests.test_rolled_back_payment_cannot_produce_an_opening_intent', '--noinput']
            expected_tests = 2
        elif os.environ.get('XA_ROOM_MYSQL_SUITE') == 'grab_opening_payment':
            command = [sys.executable, '-c', runner, 'test',
                       'orders.tests.test_grab_opening_payment_flow.GrabOpeningPaymentFlowTest',
                       'orders.tests.test_grab_opening_intent.GrabOpeningIntentMySQLTest', '--noinput']
            expected_tests = 5
        elif os.environ.get('XA_ROOM_MYSQL_SUITE') == 'provider_lifecycle':
            command = [sys.executable, '-c', runner, 'test',
                       'club_accounts.test_provider_lifecycle.ProviderLifecycleMySQLTest',
                       'club_accounts.test_provider_lifecycle.ProviderLifecycleGuardTest', '--noinput']
            expected_tests = 6
        elif os.environ.get('XA_ROOM_MYSQL_SUITE') == 'provider_reentry':
            command = [sys.executable, '-c', runner, 'test',
                       'stores.test_license_provider_rejoin_mysql.ProviderReentryQuotaMySQLTest', '--noinput']
            expected_tests = 1
        elif os.environ.get('XA_ROOM_MYSQL_SUITE') == 'pass_points':
            command = [sys.executable, '-c', runner, 'test',
                       'users.test_pass_points_mysql.PassPointsPurchaseMySQLTest',
                       'users.test_pass_points_payment.PassPointsPaymentTests.test_purchase_and_idempotent_retry_preserve_both_ledgers', '--noinput']
            expected_tests = 2
        elif os.environ.get('XA_ROOM_MYSQL_SUITE') == 'locks':
            command = [sys.executable, '-c', runner, 'test',
                       'orders.tests.test_room_boss_confirmation_mysql.RoomBossConfirmationMySQLTest',
                       'orders.tests.test_room_completion_review_mysql.RoomCompletionReviewMySQLTest', '--noinput']
            expected_tests = 3
        elif os.environ.get('XA_ROOM_MYSQL_SUITE') == 'addon':
            command = [sys.executable, '-c', runner, 'test',
                       'orders.tests.test_addon_offers.OrderAddonOffersTest',
                       'orders.tests.test_addon_configuration.AddonConfigurationTests',
                       'orders.tests.test_addon_intents.AddonIntentTests',
                       'orders.tests.test_addon_intents.AddonIntentRaceTests',
                       'orders.tests.test_addon_payment.AddonPaymentTests',
                       'orders.tests.test_addon_payment.AddonPaymentRaceTests', '--noinput']
            expected_tests = 30
        elif os.environ.get('XA_ROOM_MYSQL_SUITE') == 'addon_payment':
            command = [sys.executable, '-c', runner, 'test',
                       'orders.tests.test_addon_payment.AddonPaymentTests',
                       'orders.tests.test_addon_payment.AddonPaymentRaceTests', '--noinput']
            expected_tests = 10
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], env=env, creationflags=flags,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace')
        output = result.stdout.replace(root_password, '[redacted]').replace(user_password, '[redacted]')
        print(output[-8000:], flush=True)
        print(f'Isolated MySQL test exit code: {result.returncode}', flush=True)
        expected_summary = f"Ran {expected_tests} test{'s' if expected_tests != 1 else ''}"
        if result.returncode == 0 and (expected_summary not in output or 'OK' not in output or 'skipped=' in output):
            print(f'Expected {expected_tests} executed MySQL tests without skips; result is not accepted', file=sys.stderr)
            return 1
        return result.returncode
    finally:
        if container_id and re.fullmatch(r'[a-f0-9]{64}', container_id):
            label = subprocess.run(docker + ['inspect', '--format', '{{index .Config.Labels "xa-isolated-test"}}', container_id],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', creationflags=flags)
            if label.returncode == 0 and label.stdout.replace('\x00', '').strip() == token:
                subprocess.run(docker + ['stop', container_id], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=flags)
                print('Stopped isolated MySQL container', flush=True)
            elif label.returncode == 0:
                print('Container label mismatch; did not stop it', file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
