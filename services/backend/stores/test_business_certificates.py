from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connections
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from site_messages.models import LicenseNoticeDelivery, Message
from .context import store_context
from .models import (LicenseNotice, PlatformAccount, PlatformAuditEvent, PlatformLicenseNotice,
                     PlatformRoleGrant, Store, StoreBusinessCertificate, StoreLicenseActivatorGrant,
                     StoreProvisioningClaim)
from .platform_auth import issue_tokens
from .tests_foundation import admin


@override_settings(PLATFORM_IDENTITY_ENABLED=True, STORE_FIRST_OPEN_ENABLED=True)
class BusinessCertificateTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.branch = Store.objects.create(slug='xinan', name='证照隔离店', database_alias='store_b',
                                          initialized_at=timezone.now(), status=Store.Status.CONFIGURING)
        self.other = Store.objects.get(slug='shenglang')
        self.actor, self.owner = self.platform('certificate-platform-owner', 'SUPER_ADMIN')
        self.base = f'/api/admin/platform/store-management/{self.branch.pk}'
        self.certificates = self.base + '/business-certificates'

    def platform(self, username, role):
        account = PlatformAccount.objects.create(username=username, phone='13800001111', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=account, role=role, scope_slug='*')
        token, _ = issue_tokens(account, 'SMS', account.phone)
        return account, APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)

    def image(self, color='navy'):
        data = BytesIO()
        Image.new('RGB', (32, 24), color).save(data, format='PNG')
        return data.getvalue()

    def upload(self, *, client=None, request_id=None, expected='', expires='', color='navy', long_term=None):
        return (client or self.owner).post(self.certificates, {
            'document': SimpleUploadedFile('certificate.png', self.image(color), content_type='image/png'),
            'expires_on': expires, 'expected_certificate_id': expected,
            'long_term': not expires if long_term is None else long_term,
            'client_request_id': str(request_id or uuid4()), 'confirm_verified': True,
        }, format='multipart')

    def readiness(self):
        response = self.owner.get(self.base + '/setup/readiness')
        self.assertEqual(response.status_code, 200, response.content)
        return next(check for check in response.data['data']['checks'] if check['key'] == 'business_certificate')

    @staticmethod
    def unavailable_certificate_query(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith('SELECT') and 'stores_storebusinesscertificate' in sql:
            raise DatabaseError('test certificate read unavailable')
        return execute(sql, params, many, context)

    @override_settings(STORE_CERTIFICATE_ENFORCEMENT_ENABLED=True)
    def test_expiry_blocks_new_accounts_and_benefits_until_replacement(self):
        from club_accounts.license_quota import LicenseAdmissionError
        from club_accounts.models import ClubAccount, LicenseQuotaLock
        from .business_certificates import CERTIFICATE_TIMEZONE
        from .license_benefits import check_new_benefits

        now = timezone.now()
        day = timezone.localdate(now, CERTIFICATE_TIMEZONE)
        first = self.upload(expires=day.isoformat())
        self.assertEqual(first.status_code, 201, first.content)
        self.branch.status = Store.Status.ACTIVE
        self.branch.save(update_fields=['status'])
        with store_context(self.branch):
            person = ClubAccount.objects.create_account(username='existing-certificate-boss', account_type='BOSS')
            self.assertEqual(check_new_benefits('PROMOTIONS')[0], 'ALLOW')
            with patch('django.utils.timezone.now', return_value=now + timedelta(days=1)):
                for category in ('BOSS', 'PROVIDER'):
                    with self.assertRaisesMessage(LicenseAdmissionError, '证照已过期'):
                        ClubAccount.objects.create_account(username=category, account_type=category)
                self.assertIsNotNone(issue_account_tokens(person)[0])
                self.assertEqual(LicenseQuotaLock.objects.get(account_type='BOSS').active_count, 1)
                self.assertFalse(ClubAccount.objects.filter(username__in=['BOSS', 'PROVIDER']).exists())
                outcome, evidence = check_new_benefits('PROMOTIONS')
                self.assertEqual((outcome, evidence['business_certificate']['status']), ('DENY', 'EXPIRED'))
                self.assertEqual(check_new_benefits('AUDIT_LOG_VIEW')[0], 'ALLOW')

        updated = self.upload(expected=first.data['data']['id'], color='teal')
        self.assertEqual(updated.status_code, 201, updated.content)
        with patch('django.utils.timezone.now', return_value=now + timedelta(days=1)):
            with store_context(self.branch):
                self.assertEqual(check_new_benefits('PROMOTIONS')[0], 'ALLOW')
                ClubAccount.objects.create_account(username='renewal-provider', account_type='PROVIDER')
            with store_context(self.other):
                self.assertEqual(check_new_benefits('PROMOTIONS')[0], 'DENY')

    @override_settings(STORE_CERTIFICATE_ENFORCEMENT_ENABLED=True)
    def test_unavailable_certificate_is_unknown_and_late_renewal_cannot_prove_original_award(self):
        from club_accounts.license_quota import LicenseAdmissionUnavailable
        from club_accounts.models import ClubAccount
        from rest_framework.exceptions import ValidationError
        from .business_certificates import CERTIFICATE_TIMEZONE
        from .certificate_admission import require_original_certificate
        from .license_benefits import LicenseDecisionUnavailable, check_new_benefits

        now = timezone.now()
        expiry = timezone.localdate(now, CERTIFICATE_TIMEZONE)
        first = self.upload(expires=expiry.isoformat())
        self.assertEqual(first.status_code, 201, first.content)
        self.branch.status = Store.Status.ACTIVE
        self.branch.save(update_fields=['status'])
        with store_context(self.branch), connections['default'].execute_wrapper(self.unavailable_certificate_query):
            outcome, valid_then = check_new_benefits()
            self.assertEqual((outcome, valid_then['status']), ('UNKNOWN', 'CONTROL_UNAVAILABLE'))
            with patch('django.utils.timezone.now', return_value=now + timedelta(days=1)):
                outcome, expired_then = check_new_benefits()
                self.assertEqual((outcome, expired_then['status']), ('UNKNOWN', 'CONTROL_UNAVAILABLE'))
                with self.assertRaises(LicenseAdmissionUnavailable):
                    ClubAccount.objects.create_account(username='unverified-certificate-boss', account_type='BOSS')
                with self.assertRaises(LicenseDecisionUnavailable):
                    require_original_certificate(expired_then)
            self.assertFalse(ClubAccount.objects.filter(username='unverified-certificate-boss').exists())

        with patch('django.utils.timezone.now', return_value=now + timedelta(days=2)):
            updated = self.upload(expected=first.data['data']['id'], color='teal')
            self.assertEqual(updated.status_code, 201, updated.content)
            with store_context(self.branch):
                self.assertEqual(check_new_benefits()[0], 'ALLOW')
                require_original_certificate(valid_then)
                with self.assertRaisesMessage(ValidationError, '原业务发生时缺少有效的本店证照'):
                    require_original_certificate(expired_then)
            with store_context(self.other), self.assertRaisesMessage(ValidationError, '门店归属'):
                require_original_certificate(valid_then)

    @override_settings(STORE_CERTIFICATE_ENFORCEMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
        BOSS_FUNDING_SELECTION_ENABLED=True, ORDINARY_SERVICE_FUNDING_ENABLED=True,
        ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
        PLATFORM_PASS_REPAYMENT_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
        KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
    def test_certificate_expiry_keeps_held_coupon_order_funding_and_service_start(self):
        from coupons.models import Coupon, UserCoupon
        from orders.models import Order
        from orders.tests.test_service_funding_api import ServiceFundingApiTest
        from promotions.models import Promotion
        from wallet.models import Transaction
        from .business_certificates import CERTIFICATE_TIMEZONE

        now = timezone.now()
        first = self.upload(expires=timezone.localdate(now, CERTIFICATE_TIMEZONE).isoformat())
        self.assertEqual(first.status_code, 201, first.content)
        self.branch.status = Store.Status.ACTIVE
        self.branch.save(update_fields=['status'])
        with store_context(self.branch):
            ServiceFundingApiTest.setUp(self)
            coupon = Coupon.objects.create(name='已有券', discount_type='DIRECT', amount=100)
            other_coupon = Coupon.objects.create(name='待领券', discount_type='DIRECT', amount=50)
            Promotion.objects.create(title='到期后暂停的新促销', discount_rate=80, scope='ALL',
                start_at=now - timedelta(days=1), end_at=now + timedelta(days=3))
            boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.provider)[0])
        claim_url = f'/api/coupons/{coupon.pk}/claim/'
        original = boss.post(claim_url, {}, format='json')
        self.assertEqual(original.status_code, 200, original.data)
        owned_id = original.data['data']['id']

        with patch('stores.certificate_admission.timezone.now', return_value=now + timedelta(days=1)):
            denied = boss.post(f'/api/coupons/{other_coupon.pk}/claim/', {}, format='json')
            self.assertEqual(denied.status_code, 400, denied.data)
            replay = boss.post(claim_url, {}, format='json')
            self.assertEqual((replay.data['data']['id'], replay.data['data']['replayed']), (owned_id, True))
            preview = boss.post('/api/orders/discount-options-preview/', {'service_id': self.service.pk}, format='json')
            self.assertEqual(preview.data['code'], 0, preview.data)
            options = preview.data['data']['options']
            self.assertFalse(any(row['promotion_id'] for row in options))
            self.assertTrue(any(row['coupon_id'] == owned_id for row in options))
            payload = ServiceFundingApiTest.payload(self, user_coupon_id=owned_id)
            result = boss.post('/api/orders/create/', payload, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            order_id = result.data['data']['id']
            started = player.post(f'/api/orders/orders/{order_id}/start/', {}, format='json')
            self.assertEqual(started.data['code'], 0, started.data)

        with store_context(self.branch):
            order = Order.objects.get(pk=order_id)
            self.assertEqual((order.amount, order.status), (900, 'IN_SERVICE'))
            debit = Transaction.objects.get(order=order, tx_type='PAY')
            self.assertEqual((debit.balance_before, debit.amount, debit.balance_after), (1700, -900, 800))
            self.assertFalse(UserCoupon.objects.filter(account=self.account, coupon=other_coupon).exists())

    @override_settings(STORE_CERTIFICATE_ENFORCEMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
        BOSS_FUNDING_SELECTION_ENABLED=True, ORDINARY_SERVICE_FUNDING_ENABLED=True,
        ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
        PLATFORM_PASS_REPAYMENT_ENABLED=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
    def test_award_review_cannot_use_certificate_registered_after_confirmation(self):
        from engagement.models import LicenseConsumptionPointsDecision, LicenseRefundVipDecision, LicenseRefundVipReview, PointLedger
        from orders.models import Order
        from orders.tests.test_service_funding_api import ServiceFundingApiTest
        from .business_certificates import CERTIFICATE_TIMEZONE

        now = timezone.now()
        first = self.upload(expires=timezone.localdate(now, CERTIFICATE_TIMEZONE).isoformat())
        self.assertEqual(first.status_code, 201, first.content)
        self.branch.status = Store.Status.ACTIVE
        self.branch.save(update_fields=['status'])
        with store_context(self.branch):
            ServiceFundingApiTest.setUp(self)
            self.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            result = self.boss.post('/api/orders/create/', ServiceFundingApiTest.payload(self), format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            order = Order.objects.get(pk=result.data['data']['id'])
            payment_time = order.created_at

        # Advance beyond the settlement waiting period, then let only the
        # certificate SELECT fail during confirmation.
        with (store_context(self.branch), patch('django.utils.timezone.now', return_value=now + timedelta(days=5)),
              connections['default'].execute_wrapper(self.unavailable_certificate_query)):
            self.assertFalse(ServiceFundingApiTest.mature(self, order))
            decision = LicenseConsumptionPointsDecision.objects.get(order=order)
            vip_decision = LicenseRefundVipDecision.objects.get(order=order)
            order.refresh_from_db()
            self.assertEqual(decision.outcome, 'UNKNOWN')
            self.assertGreater(decision.decided_at, payment_time)
            self.assertEqual(order.funding_benefits_resolved_at, vip_decision.decided_at)
            self.assertFalse(PointLedger.objects.filter(order=order).exists())

        with patch('django.utils.timezone.now', return_value=now + timedelta(days=6)):
            renewed = self.upload(expected=first.data['data']['id'], color='teal')
            self.assertEqual(renewed.status_code, 201, renewed.content)
            url = self.base + f'/license-vip-review/{vip_decision.pk}/resolve'
            request_id = str(uuid4())
            response = self.owner.post(url, {'request_id': request_id, 'outcome': 'ALLOW',
                'reason': '隔离测试复核原证照'}, format='json')
            self.assertEqual(response.status_code, 409, response.data)
            self.assertIn('原业务发生时缺少有效的本店证照', response.data['msg'])
            with store_context(self.branch):
                self.assertFalse(LicenseRefundVipReview.objects.filter(decision=vip_decision).exists())
                self.assertFalse(PointLedger.objects.filter(order=order).exists())

    @override_settings(STORE_CERTIFICATE_ENFORCEMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False)
    def test_gift_expiry_after_money_lock_rolls_back_single_and_basket(self):
        from orders.financial_lock import lock_order_money
        from orders.models import Order
        from orders.tests.test_gift_basket import GiftBasketTests
        from wallet.models import IdempotencyKey, Transaction
        from .business_certificates import CERTIFICATE_TIMEZONE

        now = timezone.now()
        result = self.upload(expires=timezone.localdate(now, CERTIFICATE_TIMEZONE).isoformat())
        self.assertEqual(result.status_code, 201, result.content)
        self.branch.status = Store.Status.ACTIVE
        self.branch.save(update_fields=['status'])
        self.client = APIClient()
        with store_context(self.branch):
            GiftBasketTests.setUp(self)
            self.provider.escort_profile.account = self.recipient
            self.provider.escort_profile.save(update_fields=['account'])
            buyer = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            single = {'gift_service_id': self.one.pk, 'quantity': 1, 'expected_amount': self.one.price,
                'provider_id': self.recipient.pk, 'naming_title': '证照到期测试', 'client_request_id': 'certificate-single'}
            basket = GiftBasketTests.payload(self, 'certificate-basket')
        current_time = now + timedelta(seconds=1)

        def cross_deadline_after_lock():
            nonlocal current_time
            lock_order_money()
            current_time = now + timedelta(days=1)

        for payload in (single, basket):
            current_time = now + timedelta(seconds=1)
            with (patch('django.utils.timezone.now', side_effect=lambda: current_time),
                  patch('orders.financial_lock.lock_order_money', side_effect=cross_deadline_after_lock)):
                refused = buyer.post('/api/orders/gifts/send/', payload, format='json')
            self.assertEqual(refused.status_code, 403, refused.data)
            with store_context(self.branch):
                self.wallet.refresh_from_db()
                self.assertEqual(self.wallet.balance, 100000)
                self.assertFalse(Order.objects.filter(customer_account=self.account).exists())
                self.assertFalse(Transaction.objects.filter(tx_type='GIFT').exists())
                self.assertFalse(IdempotencyKey.objects.exists())

        with patch('django.utils.timezone.now', return_value=now + timedelta(days=2)):
            renewed = self.upload(expected=result.data['data']['id'], color='teal')
            self.assertEqual(renewed.status_code, 201, renewed.content)
            sent = buyer.post('/api/orders/gifts/send/', basket, format='json')
            self.assertEqual(sent.data['code'], 0, sent.data)
            with store_context(self.branch):
                self.wallet.refresh_from_db()
                self.assertEqual(self.wallet.balance, 90600)
                self.assertEqual(Order.objects.filter(customer_account=self.account).count(), 2)

    @override_settings(STORE_CERTIFICATE_ENFORCEMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
        PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
    def test_pass_expiry_at_final_balance_lock_preserves_coin_and_points(self):
        from engagement.models import PointLedger
        from users.models import ProviderPassPurchase
        from users.test_pass_points_payment import PassPointsPaymentTests
        from wallet.models import IdempotencyKey, Transaction, get_platform_wallet
        from .business_certificates import CERTIFICATE_TIMEZONE

        now = timezone.now()
        result = self.upload(expires=timezone.localdate(now, CERTIFICATE_TIMEZONE).isoformat())
        self.assertEqual(result.status_code, 201, result.content)
        self.branch.status = Store.Status.ACTIVE
        self.branch.save(update_fields=['status'])
        self.client = APIClient(HTTP_HOST='xa.esports.club')
        with store_context(self.branch):
            PassPointsPaymentTests.setUp(self)
        quote = PassPointsPaymentTests.quote(self)
        current_time = now + timedelta(seconds=1)

        def cross_deadline_after_lock(*args, **kwargs):
            nonlocal current_time
            wallet = get_platform_wallet(*args, **kwargs)
            current_time = now + timedelta(days=1)
            return wallet

        with (patch('django.utils.timezone.now', side_effect=lambda: current_time),
              patch('users.views.get_platform_wallet', side_effect=cross_deadline_after_lock)):
            refused = PassPointsPaymentTests.buy(self, quote, request_id='certificate-pass')
        self.assertEqual(refused.status_code, 403, refused.data)
        with store_context(self.branch):
            self.wallet.refresh_from_db()
            self.points.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.points.points_balance), (1000, 30000))
            self.assertFalse(ProviderPassPurchase.objects.exists())
            self.assertFalse(Transaction.objects.filter(tx_type__in=['PASS_PURCHASE', 'PASS_INCOME']).exists())
            self.assertFalse(PointLedger.objects.filter(kind='PASS_POINTS').exists())
            self.assertFalse(IdempotencyKey.objects.exists())

    def test_private_document_and_replacement_history_are_bound_to_the_store(self):
        _, finance = self.platform('certificate-finance', 'FINANCE')
        self.assertEqual(finance.get(self.certificates).status_code, 403)
        self.assertEqual(self.upload(client=finance).status_code, 403)
        self.assertEqual(self.upload(long_term=False).status_code, 400)
        self.assertEqual(self.readiness()['details']['status'], 'MISSING')

        first_request_id = uuid4()
        created = self.upload(request_id=first_request_id)
        self.assertEqual(created.status_code, 201, created.content)
        first = created.data['data']['id']
        replacement = self.upload(expected=first, color='teal')
        self.assertEqual(replacement.status_code, 201, replacement.content)
        second = replacement.data['data']['id']
        retry = self.upload(request_id=first_request_id)
        self.assertEqual((retry.status_code, retry.data['data']['id']), (200, first))
        self.assertEqual(self.upload(request_id=first_request_id, color='red').status_code, 409)
        self.assertEqual(self.upload(expected=first, color='red').status_code, 409)

        history = self.owner.get(self.certificates)
        self.assertEqual([row['id'] for row in history.data['data']['results']], [second, first])
        self.assertEqual(history.data['data']['current']['id'], second)
        self.assertNotIn('document', history.data['data']['results'][0])
        self.assertEqual(self.readiness()['status'], 'READY')
        self.assertEqual(PlatformAuditEvent.objects.filter(account=self.actor, action='store.certificate.recorded').count(), 2)

        endpoint = f'{self.certificates}/{first}/document'
        self.assertEqual(finance.get(endpoint).status_code, 403)
        wrong = f'/api/admin/platform/store-management/{self.other.pk}/business-certificates/{first}/document'
        self.assertEqual(self.owner.get(wrong).status_code, 404)
        document = self.owner.get(endpoint)
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document['Cache-Control'], 'private, no-store')
        self.assertEqual(document.content, self.image())

    def test_expired_certificate_blocks_opening_and_failed_audit_rolls_back(self):
        expires = timezone.localdate() - timedelta(days=1)
        result = self.upload(expires=expires.isoformat())
        self.assertEqual(result.status_code, 201, result.content)
        first = result.data['data']['id']
        self.assertEqual(self.readiness()['details']['status'], 'EXPIRED')
        refused = self.owner.post(self.base + '/setup/readiness', {'confirm': True}, format='json')
        self.assertEqual(refused.status_code, 409, refused.content)
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.status, Store.Status.CONFIGURING)

        with patch('stores.business_certificates.PlatformAuditEvent.objects.create',
                   side_effect=DatabaseError('audit unavailable')):
            failed = self.upload(expected=first, color='teal')
        self.assertEqual(failed.status_code, 503, failed.content)
        recovery = self.owner.get(self.certificates).data['data']
        self.assertEqual(recovery['current']['id'], first)
        self.assertEqual(len(recovery['results']), 1)

    def test_document_digest_mismatch_blocks_opening_and_download(self):
        result = self.upload()
        self.assertEqual(result.status_code, 201, result.content)
        identifier = result.data['data']['id']
        StoreBusinessCertificate.objects.filter(pk=identifier).update(document=b'damaged test document')
        check = self.readiness()
        self.assertEqual((check['status'], check['details']['status']), ('BLOCKED', 'DOCUMENT_UNAVAILABLE'))
        unavailable = self.owner.get(f'{self.certificates}/{identifier}/document')
        self.assertEqual(unavailable.status_code, 503, unavailable.content)
        self.assertFalse(PlatformAuditEvent.objects.filter(account=self.actor, action='store.certificate.downloaded').exists())

    def test_due_dates_use_both_notice_centers_and_deliver_once_per_threshold(self):
        from .license_tasks import deliver_license_notices

        with store_context(self.branch):
            owner = admin('certificate-owner', super_admin=True)
            delegate = admin('license-only-delegate')
        StoreLicenseActivatorGrant.objects.create(store=self.branch, account_id=delegate.pk,
            account_no=delegate.account_no, is_active=True, updated_by=self.actor)
        now = timezone.now()
        expiry = timezone.localdate(now) + timedelta(days=31)
        result = self.upload(expires=expiry.isoformat())
        self.assertEqual(result.status_code, 201, result.content)
        certificate_id = result.data['data']['id']
        with override_settings(LICENSE_NOTICE_DISPATCH_ENABLED=True), patch('stores.license_notices._wake_after_commit'):
            for day in (1, 24, 30, 31, 32):
                with patch('django.utils.timezone.now', return_value=now+timedelta(days=day)):
                    deliver_license_notices.apply().get(propagate=True)
                    deliver_license_notices.apply().get(propagate=True)
        notices = LicenseNotice.objects.filter(business_certificate_id=certificate_id)
        self.assertEqual(set(notices.values_list('kind', flat=True)), {'D30', 'D7', 'D1'})
        self.assertEqual(PlatformLicenseNotice.objects.filter(notice__in=notices, account=self.actor).count(), 3)
        center = self.owner.get('/api/admin/platform/license-notices').data['data']
        self.assertEqual(center['unread'], 3)
        self.assertTrue(all(row['source'] == 'BUSINESS_CERTIFICATE' and expiry.isoformat() in row['detail']
                            for row in center['results']))
        notice_id = center['results'][0]['id']
        self.assertEqual(self.owner.post(f'/api/admin/platform/license-notices/{notice_id}/read').status_code, 200)
        self.assertEqual(self.owner.get('/api/admin/platform/license-notices').data['data']['unread'], 2)
        StoreProvisioningClaim.objects.create(store=self.branch, database_fingerprint='a' * 64,
                                               admin_username=owner.username)
        with store_context(self.branch):
            self.assertEqual(LicenseNoticeDelivery.objects.filter(account=owner).count(), 3)
            self.assertFalse(LicenseNoticeDelivery.objects.filter(account=delegate).exists())
            self.assertEqual(Message.objects.filter(title='门店营业执照即将到期').count(), 3)
            owner.is_phone_verified = True
            owner.save(update_fields=['is_phone_verified'])
            token, _ = issue_account_tokens(owner, auth_method='password_sms')
        staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        site_messages = staff_client.get('/api/messages/')
        self.assertEqual(site_messages.status_code, 200, site_messages.content)
        self.assertEqual([row['title'] for row in site_messages.data['data']['messages']].count('门店营业执照即将到期'), 3)
        with store_context(self.other):
            self.assertFalse(Message.objects.filter(title='门店营业执照即将到期').exists())

    def test_replacement_stops_pending_old_delivery_after_platform_received_it(self):
        from .license_notices import deliver_notice

        with store_context(self.branch):
            admin('certificate-owner', super_admin=True)
        expiry = timezone.localdate() + timedelta(days=20)
        original = self.upload(expires=expiry.isoformat())
        self.assertEqual(original.status_code, 201, original.content)
        original_id = original.data['data']['id']
        notice = LicenseNotice.objects.get(business_certificate_id=original_id, kind='D30')
        with patch('stores.dynamic_database.store_connection_ready', return_value=False):
            deliver_notice(notice.pk)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.platform_delivered_at)
        self.assertIsNone(notice.store_delivered_at)

        replacement = self.upload(expected=original_id, color='teal')
        self.assertEqual(replacement.status_code, 201, replacement.content)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.suppressed_at)
        deliver_notice(notice.pk)
        self.assertEqual(PlatformLicenseNotice.objects.filter(notice=notice).count(), 1)
        with store_context(self.branch):
            self.assertFalse(LicenseNoticeDelivery.objects.filter(notice_id=notice.pk).exists())
