"""Publish immutable, text-only H5 snapshots from existing store content sources."""
from __future__ import annotations

import hashlib
import json
import re
from html import escape
from html.parser import HTMLParser

from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response

from club_accounts.models import ClubAccount
from common.compliance import (
    SERVICE_DETAILS, SERVICE_RULES, STORE_INTRODUCTION, STORE_NAME,
    actor_label_for_account, check_content, record_content_receipt,
)
from orders.catalog import visible_service_q
from orders.models import ServiceItem
from orders.purchase_benefits import account_service_q
from stores.context import current_store
from stores.db import transaction
from wallet.models import IdempotencyKey
from wallet.receipts import fingerprint_for, fund_success
from wallet.services import DERIVED_KEY_PREFIX, claim_idempotency_key, normalize_request_id

from .models import ClubContent, H5Page, H5PageVersion
from .public_content import current_content


PUBLISH_SCOPE = 'h5.page.publish'
WITHDRAW_SCOPE = 'h5.page.withdraw'
MAX_NOTICE_SECTIONS = 50
MAX_SNAPSHOT_CHARS = 500000
_DROP_TAGS = {'script', 'style', 'iframe', 'object', 'embed', 'svg', 'math', 'template'}
_BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'ul', 'ol',
               'blockquote', 'section', 'article', 'table', 'tr'}


class H5SourceUnavailable(APIException):
    status_code = 400
    default_code = 'H5_SOURCE_UNAVAILABLE'
    default_detail = 'H5 页面来源内容尚不可发布'


class H5RevisionConflict(APIException):
    status_code = 409
    default_code = 'H5_REVISION_CONFLICT'
    default_detail = 'H5 页面版本已变化，请刷新后重试'


class H5NotPublished(APIException):
    status_code = 409
    default_code = 'H5_PAGE_NOT_PUBLISHED'
    default_detail = 'H5 页面当前没有已发布版本'


class H5RequestConflict(APIException):
    status_code = 409
    default_code = 'REQUEST_ID_CONFLICT'
    default_detail = '请求编号已用于其他操作，请核对原操作记录'


class H5ResultUnavailable(APIException):
    status_code = 409
    default_code = 'H5_RESULT_UNAVAILABLE'
    default_detail = '该请求已受理但回执不可用，请使用原请求编号重试或联系管理员核对'


class _PlainText(HTMLParser):
    """Extract visible text and reject inline images that have no image review."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked = []
        self.raw_nesting = 0
        self.has_image = False

    def handle_starttag(self, tag, attrs):
        if self.blocked:
            # Non-raw dropped elements can contain nested dropped elements.
            if tag in _DROP_TAGS:
                self.blocked.append(tag)
            return
        if tag in _DROP_TAGS:
            self.blocked.append(tag)
            return
        if tag == 'img':
            self.has_image = True
        elif tag == 'br':
            self.parts.append('\n')
        elif tag in _BLOCK_TAGS:
            self.parts.append('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _DROP_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.blocked:
            if tag == self.blocked[-1]:
                if self.raw_nesting:
                    # HTMLParser reports script/style bodies as one raw data
                    # event. A nested opener in that body means this closing
                    # tag is not the outer block's closing tag.
                    self.raw_nesting -= 1
                else:
                    self.blocked.pop()
            return
        if tag in _BLOCK_TAGS:
            self.parts.append('\n')

    def handle_data(self, data):
        if self.blocked:
            if self.blocked[-1] in ('script', 'style'):
                tag = re.escape(self.blocked[-1])
                self.raw_nesting += len(re.findall(r'<\s*' + tag + r'(?=[\s/>])', data, re.IGNORECASE))
            return
        self.parts.append(data)

    def text(self):
        value = ''.join(self.parts).replace('\r\n', '\n').replace('\r', '\n')
        lines = [re.sub(r'[^\S\n]+', ' ', line).strip() for line in value.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def plain_text(value):
    parser = _PlainText()
    parser.feed(str(value or ''))
    parser.close()
    return parser.text(), parser.has_image


def snapshot_digest(snapshot):
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def format_price(units, currency_name):
    whole, fraction = divmod(int(units), 10)
    amount = str(whole) if not fraction else f'{whole}.{fraction}'
    return f'{amount} {currency_name}'.strip()


def _base_snapshot(kind, store, now):
    return {
        'schemaVersion': 1,
        'kind': kind,
        'title': store.get_kind_display() if hasattr(store, 'get_kind_display') else dict(H5Page.Kind.choices)[kind],
        'store': {'name': store.name, 'currencyName': store.currency_name},
        'generatedAt': now.isoformat(),
    }


def _text_field(value, label, issues, *, required=False):
    text, has_image = plain_text(value)
    if has_image:
        issues.append(f'{label}包含图片；当前 H5 发布仅支持已核对文字内容，请先移除图片')
    if required and not text:
        issues.append(f'{label}不能为空')
    return text


def _price_snapshot(store, now):
    snapshot = _base_snapshot(H5Page.Kind.PRICE_LIST, store, now)
    snapshot['items'] = []
    issues = []
    services = (ServiceItem.objects.filter(visible_service_q(), is_addon=False)
                .exclude(service_category__is_gift=True)
                .filter(account_service_q(None), price__gt=0)
                .select_related('game_category', 'service_category', 'gameplay')
                .order_by('sort_order', 'id'))
    for service in services:
        label = f'服务“{service.name}”'
        rules = _text_field(service.rules, label + '规则', issues)
        raw_details = service.details_html or (service.gameplay.details_html if service.gameplay_id else '')
        details = _text_field(raw_details, label + '详情', issues)
        if service.rules_images:
            issues.append(label + '的规则图片尚未完成图片内容合规核对，请先改为纯文字规则')
        if not service.name.strip():
            issues.append('存在未填写名称的价目项目')
        full_price = service.price * service.billing_provider_count
        snapshot['items'].append({
            'id': service.pk,
            'name': service.name,
            'description': service.description,
            'categoryName': service.service_category.name if service.service_category_id else '',
            'gameName': service.game_category.name if service.game_category_id else '',
            'priceUnits': full_price,
            'unitPriceUnits': service.price,
            'priceText': format_price(full_price, store.currency_name),
            'unitPriceText': format_price(service.price, store.currency_name),
            'priceUnit': service.price_unit,
            'billingProviderCount': service.billing_provider_count,
            'purchaseNote': service.purchase_note,
            'rules': rules,
            'details': details,
        })
    if not snapshot['items']:
        issues.append('本店暂无公开且有标价的陪玩服务')
    return snapshot, issues


def _notice_snapshot(store, now):
    snapshot = _base_snapshot(H5Page.Kind.ORDER_NOTICE, store, now)
    snapshot['sections'] = []
    issues = []
    rows = list(current_content(now).filter(kind=ClubContent.Kind.ORDER_RULES)
                .order_by('-sort_order', '-id')[:MAX_NOTICE_SECTIONS + 1])
    if len(rows) > MAX_NOTICE_SECTIONS:
        issues.append(f'当前下单须知超过 {MAX_NOTICE_SECTIONS} 条，请先精简后再发布')
    for row in rows[:MAX_NOTICE_SECTIONS]:
        if row.image_url:
            issues.append(f'须知“{row.title}”包含图片；当前 H5 发布仅支持已核对文字内容，请先移除图片')
        content = _text_field(row.content, f'须知“{row.title}”', issues, required=True)
        if not row.title.strip():
            issues.append('存在未填写标题的下单须知')
        snapshot['sections'].append({'id': row.pk, 'title': row.title, 'content': content})
    if not snapshot['sections']:
        issues.append('本店暂无生效中的下单须知')
    return snapshot, issues


def _introduction_snapshot(store, now):
    snapshot = _base_snapshot(H5Page.Kind.INTRODUCTION, store, now)
    issues = []
    snapshot['body'] = _text_field(store.introduction, '门店介绍', issues, required=True)
    return snapshot, issues


def build_draft(kind):
    store = current_store()
    if store is None:
        raise H5SourceUnavailable('无法确定门店，不能生成 H5 页面')
    now = timezone.now()
    if kind == H5Page.Kind.PRICE_LIST:
        snapshot, issues = _price_snapshot(store, now)
    elif kind == H5Page.Kind.ORDER_NOTICE:
        snapshot, issues = _notice_snapshot(store, now)
    elif kind == H5Page.Kind.INTRODUCTION:
        snapshot, issues = _introduction_snapshot(store, now)
    else:
        raise ValidationError('不支持的 H5 页面类型')
    if len(json.dumps(snapshot, ensure_ascii=False)) > MAX_SNAPSHOT_CHARS:
        raise H5SourceUnavailable('H5 页面文字内容过大，请精简后再发布')
    return snapshot, list(dict.fromkeys(issues))


def preview(kind):
    snapshot, issues = build_draft(kind)
    return {
        'kind': kind,
        'publishable': not issues,
        'issues': issues,
        'draftDigest': snapshot_digest(snapshot),
        'snapshot': snapshot,
    }


def _compliance_values(snapshot):
    yield STORE_NAME, snapshot['store']['name']
    if snapshot['kind'] == H5Page.Kind.INTRODUCTION:
        yield STORE_INTRODUCTION, snapshot['body']
    elif snapshot['kind'] == H5Page.Kind.ORDER_NOTICE:
        value = '\n'.join(section['title'] + '\n' + section['content']
                          for section in snapshot['sections'])
        if value.strip():
            yield SERVICE_RULES, value
    else:
        rules, details = [], []
        for item in snapshot['items']:
            rules.append('\n'.join(filter(None, [item['name'], item['description'],
                item['purchaseNote'], item['rules']])))
            if item['details']:
                details.append(item['name'] + '\n' + item['details'])
        if any(value.strip() for value in rules):
            yield SERVICE_RULES, '\n'.join(rules)
        if details:
            yield SERVICE_DETAILS, '\n'.join(details)


def _validate_payload(data, allowed):
    if not isinstance(data, dict):
        raise ValidationError('请求正文必须是 JSON 对象')
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ValidationError({'unknown_fields': unknown})
    request_id = data.get('client_request_id')
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValidationError({'client_request_id': '不能为空'})
    if len(request_id.strip()) > 64:
        raise ValidationError({'client_request_id': '长度不能超过 64'})
    if normalize_request_id(request_id).startswith(DERIVED_KEY_PREFIX):
        raise ValidationError({'client_request_id': '不能使用服务端保留前缀'})
    expected = data.get('expected_version')
    if type(expected) is not int or expected < 0:
        raise ValidationError({'expected_version': '必须是非负整数'})
    return normalize_request_id(request_id), expected


def _receipt_replay(request, scope):
    request_id = normalize_request_id(request.data.get('client_request_id'))
    receipt = IdempotencyKey.objects.filter(account=request.account, request_id=request_id).first()
    if receipt is None:
        return None
    if receipt.scope != scope or receipt.request_fingerprint != fingerprint_for(request.path, request.data):
        raise H5RequestConflict()
    if not receipt.request_fingerprint or receipt.response is None:
        raise H5ResultUnavailable()
    body = dict(receipt.response)
    data = dict(body.get('data') or {})
    data['replayed'] = True
    body['data'] = data
    request._request._business_audit_recorded = True
    return Response(body, status=200)


def _lock_account(request):
    account = ClubAccount.objects.select_for_update().filter(pk=request.account.pk).first()
    if account is None:
        raise PermissionDenied('后台账号不可用，请重新登录')
    return account


def _locked_page(kind, *, create):
    if create:
        page, _ = H5Page.objects.get_or_create(kind=kind)
    else:
        page = H5Page.objects.filter(kind=kind).first()
        if page is None:
            return None
    return H5Page.objects.select_for_update().get(pk=page.pk)


def _check_revision(page, expected):
    revision = page.revision if page is not None else 0
    if revision != expected:
        raise H5RevisionConflict()


def page_metadata(page, version=None, *, replayed=False):
    token = str(page.public_token)
    current = version or page.current_version
    return {
        'kind': page.kind,
        'publicToken': token,
        'publicPath': f'/h5/pages/{token}/',
        'jsonPath': f'/api/club/h5-pages/{token}/',
        'revision': page.revision,
        'published': current is not None,
        'currentVersion': current.version if current is not None else None,
        'version': current.version if current is not None else None,
        'contentSha256': current.content_sha256 if current is not None else None,
        'publishedAt': current.published_at.isoformat() if current is not None else None,
        'complianceStatus': current.compliance_status if current is not None else None,
        'copyrightConfirmed': current.copyright_confirmed if current is not None else None,
        'replayed': replayed,
    }


def version_metadata(version):
    return {
        'version': version.version,
        'contentSha256': version.content_sha256,
        'complianceStatus': version.compliance_status,
        'complianceReceiptIds': list(version.compliance_receipt_ids),
        'copyrightConfirmed': version.copyright_confirmed,
        'publishedAt': version.published_at.isoformat(),
    }


def record_audit(request, *, resource, page, body):
    from console.models import AdminAuditLog
    account = request.account
    audit = AdminAuditLog.objects.create(
        operator=getattr(request, 'legacy_user', None),
        operator_account=account,
        operator_name=actor_label_for_account(account),
        method=request.method,
        path=request.path[:255],
        resource=resource,
        object_id=str(page.pk),
        status_code=200,
        request_body=body,
        ip=request.META.get('REMOTE_ADDR'),
    )
    request._business_audit_recorded = True
    request._request._business_audit_recorded = True
    return audit


def publish_page(request, kind):
    _, expected = _validate_payload(request.data, {'client_request_id', 'expected_version', 'copyright_confirmed'})
    if request.data.get('copyright_confirmed') is not True:
        raise ValidationError({'copyright_confirmed': '发布前须确认页面文字素材权利'})
    with transaction.atomic():
        _lock_account(request)
        replay = _receipt_replay(request, PUBLISH_SCOPE)
        if replay is not None:
            return replay
        page = _locked_page(kind, create=True)
        _check_revision(page, expected)
        snapshot, issues = build_draft(kind)
        if issues:
            raise H5SourceUnavailable('；'.join(issues))
        checks = [(field, value, check_content(field, value)) for field, value in _compliance_values(snapshot)]
        store = current_store()
        disabled = not getattr(store, 'content_compliance_enabled', True)
        if not claim_idempotency_key(account=request.account,
                                     request_id=normalize_request_id(request.data.get('client_request_id')),
                                     scope=PUBLISH_SCOPE):
            replay = _receipt_replay(request, PUBLISH_SCOPE)
            if replay is not None:
                return replay
            raise H5RequestConflict()
        receipt_ids = []
        for field, _value, result in checks:
            if result is not None:
                receipt = record_content_receipt('h5_page', page.pk, field, result,
                                                 actor_account=request.account)
                receipt_ids.append(receipt.pk)
        version = H5PageVersion.objects.create(
            page=page,
            version=page.revision + 1,
            snapshot=snapshot,
            content_sha256=snapshot_digest(snapshot),
            compliance_status=(H5PageVersion.ComplianceStatus.DISABLED if disabled
                               else H5PageVersion.ComplianceStatus.CHECKED),
            compliance_receipt_ids=receipt_ids,
            publisher=request.account,
            copyright_confirmed=True,
        )
        page.current_version = version
        page.revision = version.version
        page.save(update_fields=['current_version', 'revision', 'updated_at'])
        metadata = page_metadata(page, version)
        record_audit(request, resource='h5-page-publish', page=page, body={
            'clientRequestId': normalize_request_id(request.data.get('client_request_id')),
            'kind': kind,
            'expectedVersion': expected,
            'version': version.version,
            'contentSha256': version.content_sha256,
            'complianceStatus': version.compliance_status,
            'complianceReceiptIds': receipt_ids,
            'copyrightConfirmed': True,
        })
        response = fund_success(request, PUBLISH_SCOPE, data=metadata, msg='H5 页面已发布')
        response.status_code = 201
        return response


def withdraw_page(request, kind):
    request_id, expected = _validate_payload(request.data, {'client_request_id', 'expected_version', 'reason'})
    reason = request.data.get('reason')
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
        raise ValidationError({'reason': '请填写 255 字以内的撤回原因'})
    reason = reason.strip()
    with transaction.atomic():
        _lock_account(request)
        replay = _receipt_replay(request, WITHDRAW_SCOPE)
        if replay is not None:
            return replay
        page = _locked_page(kind, create=True)
        _check_revision(page, expected)
        if page.current_version_id is None:
            raise H5NotPublished()
        withdrawn = page.current_version
        if not claim_idempotency_key(account=request.account, request_id=request_id, scope=WITHDRAW_SCOPE):
            replay = _receipt_replay(request, WITHDRAW_SCOPE)
            if replay is not None:
                return replay
            raise H5RequestConflict()
        page.current_version = None
        page.save(update_fields=['current_version', 'updated_at'])
        metadata = page_metadata(page)
        metadata['withdrawnVersion'] = withdrawn.version
        metadata['withdrawnAt'] = timezone.now().isoformat()
        record_audit(request, resource='h5-page-withdraw', page=page, body={
            'clientRequestId': request_id,
            'kind': kind,
            'expectedVersion': expected,
            'withdrawnVersion': withdrawn.version,
            'withdrawnContentSha256': withdrawn.content_sha256,
            'reason': reason,
        })
        return fund_success(request, WITHDRAW_SCOPE, data=metadata, msg='H5 页面已撤回')


def render_html(version):
    """Render only escaped snapshot text; no source HTML, scripts or remote assets."""
    snapshot = version.snapshot
    store_name = escape(snapshot['store']['name'])
    kind = snapshot['kind']

    def text_block(value):
        return '<p>' + escape(value).replace('\n', '<br>') + '</p>'

    body = []
    if kind == H5Page.Kind.PRICE_LIST:
        for item in snapshot['items']:
            body.append('<article class="item">')
            body.append('<h2>' + escape(item['name']) + '</h2>')
            body.append('<p class="price">' + escape(item['priceText']) + ' / ' + escape(item['priceUnit']) + '</p>')
            labels = [item.get('categoryName', ''), item.get('gameName', '')]
            label = ' · '.join(value for value in labels if value)
            if label:
                body.append('<p class="muted">' + escape(label) + '</p>')
            for field, label_text in (('description', '简介'), ('rules', '规则'), ('details', '详情'), ('purchaseNote', '下单说明')):
                if item.get(field):
                    body.append('<h3>' + label_text + '</h3>')
                    body.append(text_block(item[field]))
            body.append('</article>')
    elif kind == H5Page.Kind.ORDER_NOTICE:
        for section in snapshot['sections']:
            body.append('<section><h2>' + escape(section['title']) + '</h2>')
            body.append(text_block(section['content']))
            body.append('</section>')
    else:
        body.append(text_block(snapshot['body']))
    published = version.published_at
    footer = (f'<footer>© {published.year} {store_name}。页面文字素材权利由发布门店确认。'
              f'<br>版本 v{version.version} · 发布于 {escape(published.isoformat())}</footer>')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="referrer" content="no-referrer"><title>'
            + escape(snapshot['title']) + ' - ' + store_name + '</title>'
            '<style>body{max-width:760px;margin:0 auto;padding:24px;font:16px/1.7 system-ui,sans-serif;'
            'color:#17202a;background:#fff}header{border-bottom:1px solid #ddd;margin-bottom:24px}'
            '.price{font-size:22px;font-weight:700;color:#2457d6}.muted,footer{color:#667085}'
            'article,section{margin:20px 0;padding-bottom:16px;border-bottom:1px solid #eee}'
            'h2{font-size:20px}h3{font-size:16px;margin-bottom:4px}footer{margin-top:32px;font-size:13px}'
            '</style></head><body><header><h1>' + escape(snapshot['title']) + '</h1><p>'
            + store_name + '</p></header><main>' + ''.join(body) + '</main>' + footer
            + '</body></html>')
