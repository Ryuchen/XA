import hashlib
import secrets
import uuid
from collections.abc import Mapping
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection, transaction
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView, exception_handler as drf_exception_handler

from .models import Room, Participant, Pool, Entry, EntryGrant, Draw, DrawBatch, Award, BonusAward
from .engine import (catalog_data, participant_data, draw_data, draw, grant_entries,
                     batch_data, draw_batch, history_data, remaining_only, require_admissions_open)

TOKEN_PREFIX = 'room.v1.'
TOKEN_SALT = 'anniversary-room-session-v1'


def digest(raw):
    return hashlib.sha256(raw.strip().encode()).hexdigest()


def exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(exc.messages)
    response = drf_exception_handler(exc, context)
    if response is not None:
        def first(value):
            if isinstance(value, dict):
                return first(next(iter(value.values()), '操作失败'))
            if isinstance(value, list):
                return first(value[0] if value else '操作失败')
            return str(value)
        response.data = {'code': 1, 'message': first(response.data)}
        response['Cache-Control'] = 'no-store'
    return response


class RoomAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return 'Bearer realm="lottery-room"'

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        try:
            if len(parts) != 2 or parts[0].lower() != b'bearer':
                raise ValueError()
            token = parts[1].decode('ascii')
            if not token.startswith(TOKEN_PREFIX):
                raise ValueError()
            payload = signing.loads(token[len(TOKEN_PREFIX):], salt=TOKEN_SALT, max_age=43200)
            if not isinstance(payload, dict) or type(payload.get('participant')) is not int:
                raise ValueError()
            participant = Participant.objects.select_related('room').get(
                pk=payload['participant'], enabled=True)
            if (payload.get('digest') != participant.code_digest
                    or payload.get('room') not in (None, participant.room.code)):
                raise ValueError()
        except (ValueError, UnicodeDecodeError, signing.BadSignature, Participant.DoesNotExist):
            raise AuthenticationFailed('房间登录已失效，请重新输入参与码') from None
        request.participant = participant
        return AnonymousUser(), payload


class IsParticipant(BasePermission):
    def has_permission(self, request, view):
        return bool(getattr(request, 'participant', None))


class IsInParticipantRoom(BasePermission):
    message = '请从自己的抽奖房间进入；页面更新后请刷新再试'

    def has_permission(self, request, view):
        participant = getattr(request, 'participant', None)
        current_room = request.headers.get('X-Lottery-Room', '').strip()
        return bool(participant and current_room == participant.room.code)


class LoginThrottle(SimpleRateThrottle):
    scope = 'room_login'
    rate = '8/min'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class DrawThrottle(SimpleRateThrottle):
    scope = 'room_draw'
    rate = '20/min'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': request.participant.pk}

    def allow_request(self, request, view):
        data = request.data
        key = data.get('client_request_id') if isinstance(data, Mapping) else None
        if isinstance(key, str) and 8 <= len(key) <= 64 and all(c.isascii() and (c.isalnum() or c in '-_') for c in key):
            is_batch = getattr(view, 'batch_count', 1) == 10
            model, prefix = (DrawBatch, 'batch') if is_batch else (Draw, 'draw')
            if model.objects.filter(event_key=f'{prefix}:{request.participant.pk}:{key}').exists():
                return True
        return super().allow_request(request, view)


def success(data):
    private_keys = {'probability', 'probability_percent', 'probabilities',
                    'probability_denominator', 'thanks_probability',
                    'retry_probability', 'reward_probability', 'weight',
                    'row_weights', 'column_weights', 'weights', 'row_ticket',
                    'column_ticket', 'random_ticket', 'second_random_ticket',
                    'joint_probability', 'probability_snapshot', 'denominator', 'phase',
                    'ordinary_dry_streak', 'soft_phase', 'cash_phase',
                    'cell_weights', 'joint_denominator', 'initial_row', 'initial_column',
                    'retention_numerator', 'retention_denominator', 'retention_ticket', 'thanks_ticket'}
    def public(value):
        if isinstance(value, dict):
            return {key: public(item) for key, item in value.items() if key not in private_keys}
        if isinstance(value, (list, tuple)):
            return [public(item) for item in value]
        return value
    response = Response({'code': 0, 'data': public(data)})
    response['Cache-Control'] = 'no-store'
    return response


class CatalogView(APIView):
    def get(self, request):
        code = request.query_params.get('room')
        room = get_object_or_404(Room, code=code) if code else None
        data = catalog_data(room)
        return success(data)


class LoginView(APIView):
    throttle_classes = [LoginThrottle]

    def post(self, request):
        raw = serializers.CharField(min_length=16, max_length=128).run_validation(request.data.get('code'))
        room_code = serializers.CharField(max_length=32).run_validation(request.data.get('room'))
        participant = Participant.objects.select_related('room').filter(
            room__code=room_code, code_digest=digest(raw), enabled=True).first()
        if participant is None:
            raise ValidationError('参与码或房间号不正确，请向管理员核对')
        if not participant.room.enabled:
            raise ValidationError('房间已暂停，请联系管理员')
        token = TOKEN_PREFIX + signing.dumps({'participant': participant.pk,
            'digest': participant.code_digest, 'room': participant.room.code}, salt=TOKEN_SALT, compress=True)
        return success({'token': token, 'nickname': participant.nickname,
            'account_id': participant.pk, 'business_cycle': 1,
            'room': participant.room.code})


class MeView(APIView):
    authentication_classes = [RoomAuthentication]
    permission_classes = [IsParticipant, IsInParticipantRoom]

    def get(self, request):
        participant = request.participant
        data = participant_data(participant)
        data.update(account_id=participant.pk, business_cycle=1, nickname=participant.nickname,
                    room=participant.room.code, room_name=participant.room.name)
        return success(data)


class DrawRequestSerializer(serializers.Serializer):
    campaign = serializers.ChoiceField(choices=['small', 'red', 'grand'])
    version = serializers.IntegerField(min_value=1)
    client_request_id = serializers.RegexField(r'^[A-Za-z0-9_-]{8,64}$')


class DrawView(APIView):
    authentication_classes = [RoomAuthentication]
    permission_classes = [IsParticipant, IsInParticipantRoom]
    throttle_classes = [DrawThrottle]

    def post(self, request):
        serializer = DrawRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        slug, version, key = (serializer.validated_data[name] for name in ('campaign', 'version', 'client_request_id'))
        try:
            result = draw(request.participant, slug, key, version)
        except OperationalError:
            return Response({'code': 1, 'message': '抽奖正在确认中，请保留本次请求并重试'}, status=503)
        return success(draw_data(result))


class DrawBatchView(APIView):
    authentication_classes = [RoomAuthentication]
    permission_classes = [IsParticipant, IsInParticipantRoom]
    throttle_classes = [DrawThrottle]
    batch_count = 10

    def post(self, request):
        serializer = DrawRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        slug, version, key = (serializer.validated_data[name] for name in ('campaign', 'version', 'client_request_id'))
        try:
            result = draw_batch(request.participant, slug, key, version)
        except OperationalError:
            return Response({'code': 1, 'message': '十连抽正在确认中，请保留本次请求并重试'}, status=503)
        return success(batch_data(result))


class HistoryView(APIView):
    authentication_classes = [RoomAuthentication]
    permission_classes = [IsParticipant, IsInParticipantRoom]

    def get(self, request):
        page = serializers.IntegerField(min_value=1, max_value=2_147_483_647).run_validation(request.query_params.get('page', 1))
        page_size = serializers.IntegerField(min_value=1, max_value=30).run_validation(request.query_params.get('page_size', 10))
        before_id = None
        if 'before_id' in request.query_params:
            before_id = serializers.IntegerField(min_value=0, max_value=9_223_372_036_854_775_807).run_validation(request.query_params['before_id'])
        return success(history_data(request.participant, page=page, page_size=page_size, before_id=before_id))


def index(request, room_code=''):
    if room_code:
        get_object_or_404(Room, code=room_code)
    return render(request, 'lottery/index.html', {'room_code': room_code,
        'api_base': settings.PREFIX + '/api/', 'room_base': settings.PREFIX + '/r/',
        'remaining_only': remaining_only()})


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return JsonResponse({'status': 'ok', 'service': 'anniversary-lottery'})
    except Exception:
        return JsonResponse({'status': 'unavailable'}, status=503)


def count_value(value, *, maximum=10000):
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        raise ValidationError('次数必须是整数') from None
    if not 0 <= number <= maximum:
        raise ValidationError(f'次数必须在 0 到 {maximum} 之间')
    return number


@never_cache
@user_passes_test(lambda user: user.is_active and user.is_superuser)
def control(request):
    selected = request.GET.get('room', '')
    issued = None
    if request.method == 'POST':
        selected = request.POST.get('room', '')
        try:
            with transaction.atomic():
                action = request.POST.get('action')
                if action in {'create_room', 'add_participant', 'grant'}:
                    require_admissions_open()
                if action == 'create_room':
                    name = serializers.CharField(max_length=100).run_validation(request.POST.get('name'))
                    def date_value(key, default):
                        value = parse_datetime(request.POST.get(key, '')) if request.POST.get(key) else default
                        if value is None:
                            raise ValidationError('请选择有效的房间起止时间')
                        return timezone.make_aware(value) if timezone.is_naive(value) else value
                    start = date_value('starts_at', timezone.now())
                    end = date_value('ends_at', start + timedelta(days=10))
                    if end <= start:
                        raise ValidationError('结束时间必须晚于开始时间')
                    room = Room.objects.create(code=secrets.token_hex(4), name=name,
                        starts_at=start, ends_at=end, created_by=request.user)
                    selected = room.code
                    messages.success(request, '房间已开设。添加老板并发放次数后，即可分享参与链接。')
                else:
                    room = get_object_or_404(Room, code=selected)
                    nonce = serializers.RegexField(r'^[A-Za-z0-9_-]{8,64}$').run_validation(request.POST.get('request_id'))
                    if action == 'toggle_room':
                        room = Room.objects.select_for_update().get(pk=room.pk)
                        room.enabled = not room.enabled
                        room.save(update_fields=['enabled'])
                    elif action == 'add_participant':
                        nickname = serializers.CharField(max_length=50).run_validation(request.POST.get('nickname'))
                        counts = {pool.slug: count_value(request.POST.get(pool.slug)) for pool in Pool.objects.all()}
                        raw = secrets.token_urlsafe(18)
                        person, created = Participant.objects.get_or_create(creation_key='participant:' + nonce,
                            defaults={'room': room, 'nickname': nickname, 'code_digest': digest(raw)})
                        if not created:
                            original = {row.entry.pool.slug: row.count for row in EntryGrant.objects.filter(
                                entry__participant=person, event_key__in=['grant:' + nonce + '-' + slug for slug in counts]
                            ).select_related('entry__pool')}
                            if person.room_id != room.pk or person.nickname != nickname or any(
                                    original.get(slug, 0) != count for slug, count in counts.items()):
                                raise ValidationError('同一操作凭证不能添加不同老板或次数')
                            messages.info(request, '老板已添加，次数未重复发放；如需参与码请点击重置参与码。')
                        else:
                            for slug, count in counts.items():
                                if count:
                                    grant_entries(person, slug, count, nonce + '-' + slug,
                                        '管理员首次发放', request.user)
                            issued = {'nickname': person.nickname, 'code': raw, 'room': room.code}
                            messages.success(request, '老板已添加，参与码仅在本次页面显示。')
                    elif action in {'grant', 'reset_code', 'toggle_participant'}:
                        person = get_object_or_404(Participant, pk=request.POST.get('participant'), room=room)
                        if action == 'grant':
                            reason = serializers.CharField(max_length=255).run_validation(request.POST.get('reason'))
                            slug = serializers.ChoiceField(choices=['small','red','grand']).run_validation(request.POST.get('pool'))
                            count = count_value(request.POST.get('count'))
                            if count < 1:
                                raise ValidationError('本次添加次数至少为 1')
                            grant_entries(person, slug, count, nonce, reason, request.user)
                            messages.success(request, f'已为 {person.nickname} 添加 {count} 次，发放记录已保存。')
                        elif action == 'reset_code':
                            person = Participant.objects.select_for_update().get(pk=person.pk)
                            if person.last_reset_key == nonce:
                                messages.info(request, '此参与码重置已经处理；如需再次获取，请重新点击重置参与码。')
                            else:
                                raw = secrets.token_urlsafe(18)
                                person.code_digest = digest(raw)
                                person.last_reset_key = nonce
                                person.save(update_fields=['code_digest', 'last_reset_key'])
                                issued = {'nickname': person.nickname, 'code': raw, 'room': room.code}
                                messages.success(request, '参与码已重置，旧参与码和旧登录已失效。')
                        else:
                            person.enabled = not person.enabled
                            person.save(update_fields=['enabled'])
                    elif action in {'fulfill', 'fulfill_bonus'}:
                        award_model = BonusAward if action == 'fulfill_bonus' else Award
                        award = get_object_or_404(award_model.objects.select_for_update(),
                            pk=request.POST.get('award'), draw__room=room)
                        note = serializers.CharField(max_length=255).run_validation(request.POST.get('note'))
                        if award.status != 'PENDING':
                            raise ValidationError('该奖品已标记交付，请勿重复处理')
                        award.status = 'FULFILLED'
                        award.delivery_note = note
                        award.fulfilled_by = request.user
                        award.fulfilled_at = timezone.now()
                        award.save(update_fields=['status','delivery_note','fulfilled_by','fulfilled_at'])
                        messages.success(request, '已记录交付人、时间和交付说明。')
                    else:
                        raise ValidationError('不支持的操作')
        except (ValidationError, DjangoValidationError) as error:
            messages.error(request, str(error.detail if isinstance(error, ValidationError) else '；'.join(error.messages)))
        if issued is None:
            return redirect(reverse('control') + ('?room=' + selected if selected else ''))
    rooms = list(Room.objects.order_by('-created_at'))
    room = next((row for row in rooms if row.code == selected), rooms[0] if rooms else None)
    participants = []
    draws = []
    grants = []
    if room:
        for person in Participant.objects.filter(room=room).order_by('-id').prefetch_related('entries__pool'):
            counts = {entry.pool.slug: entry.remaining for entry in person.entries.all()}
            rolls = {entry.pool.slug: entry.total_rolls for entry in person.entries.all()}
            participants.append({'person': person, 'small': counts.get('small',0),
                'red': counts.get('red',0), 'grand': counts.get('grand',0),
                'small_rolls': rolls.get('small',0), 'red_rolls': rolls.get('red',0),
                'grand_rolls': rolls.get('grand',0)})
        draws = Draw.objects.filter(room=room).select_related('participant','pool',
            'award__fulfilled_by').prefetch_related('bonuses__fulfilled_by').order_by('-id')[:60]
        grants = EntryGrant.objects.filter(entry__participant__room=room).select_related(
            'entry__participant','entry__pool','operator').order_by('-id')[:30]
    return render(request, 'lottery/control.html', {'rooms': rooms, 'room': room,
        'participants': participants, 'draws': draws, 'grants': grants, 'issued': issued,
        'remaining_only': remaining_only(),
        'nonce': uuid.uuid4().hex, 'pools': Pool.objects.all(),
        'share_url': request.build_absolute_uri(reverse('room', kwargs={'room_code': room.code})) if room else '',
        'default_end': (timezone.localtime() + timedelta(days=10)).strftime('%Y-%m-%dT%H:%M')})
