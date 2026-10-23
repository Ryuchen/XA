"""KOOK-owned identity proof and durable, exactly-once points accrual."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone as dt_timezone
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import KookBinding, KookBindingChallenge, KookPointEvent, KookPointsConfig
from .services import change_points, config, locked_profile


def enabled_config():
    return KookPointsConfig.objects.filter(pk=1, enabled=True).first()


def allowed_account(account):
    return account.is_active and account.can_login and account.can_view and account.account_type in ['BOSS', 'PROVIDER']


@transaction.atomic
def challenge(account):
    if not enabled_config():
        raise ValidationError('KOOK积分尚未开启')
    if KookBinding.objects.filter(account=account).exists():
        raise ValidationError('此账号已绑定KOOK')
    token='XA'+secrets.token_hex(8).upper()
    KookBindingChallenge.objects.update_or_create(account=account, defaults={
        'code_hash':hashlib.sha256(token.encode()).hexdigest(), 'expires_at':timezone.now()+timedelta(minutes=10)})
    return token


def _accrue(binding, now):
    if binding.channel_id and binding.accounted_at and allowed_account(binding.account):
        elapsed=int((now-binding.accounted_at).total_seconds()*1000)
        # A disconnected listener never fabricates online time. Connection
        # heartbeats checkpoint every 30s; gaps require fresh KOOK presence.
        if 0 < elapsed <= 90000:
            binding.online_milliseconds+=elapsed
        total=binding.online_milliseconds//7200000
        if total>binding.awarded_points and config().points_enabled:
            profile=locked_profile(binding.account)
            change_points(profile,(total-binding.awarded_points)*100,
                f'KOOK_ONLINE_TOTAL:{binding.pk}:{total}','KOOK_ONLINE','KOOK语音累计在线，每2小时1积分')
            binding.awarded_points=total
    binding.accounted_at=now


@transaction.atomic
def checkpoint(now=None, status='已连接，自动计分中'):
    now=now or timezone.now()
    for binding in KookBinding.objects.select_for_update().filter(channel_id__gt='').select_related('account').order_by('id'):
        _accrue(binding,now)
        binding.save(update_fields=['accounted_at','online_milliseconds','awarded_points'])
    KookPointsConfig.objects.filter(pk=1,enabled=True).update(heartbeat_at=now,status=status)


@transaction.atomic
def reset_presence(presence, now=None):
    """Seed only current presence. Never award time from before this connection."""
    now=now or timezone.now()
    for binding in KookBinding.objects.select_for_update().select_related('account').order_by('id'):
        binding.channel_id=presence.get(binding.user_id,'')
        binding.accounted_at=now
        binding.event_at=now
        binding.save(update_fields=['channel_id','accounted_at','event_at'])


def event_time(milliseconds):
    try:
        return datetime.fromtimestamp(int(milliseconds)/1000,tz=dt_timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        raise ValidationError('KOOK事件时间不合法') from None


@transaction.atomic
def process_event(event, now=None):
    now=now or timezone.now();cfg=enabled_config()
    if not cfg or not config().points_enabled:return False
    timestamp=event_time(event.get('msg_timestamp'))
    if timestamp<cfg.started_at or timestamp>now+timedelta(seconds=60):return False
    extra=event.get('extra') or {};kind=extra.get('type');body=extra.get('body') or {}
    is_voice=event.get('type')==255 and kind in ['joined_channel','exited_channel']
    if is_voice:
        if str(event.get('target_id'))!=cfg.guild_id or str(body.get('channel_id')) not in cfg.voice_channel_ids:return False
        user_id=str(body.get('user_id',''))
    else:
        if event.get('channel_type')!='GROUP' or event.get('type') not in [1,9] or str(event.get('target_id'))!=cfg.checkin_channel_id:return False
        if str(extra.get('guild_id'))!=cfg.guild_id or (extra.get('author') or {}).get('bot'):return False
        user_id=str(event.get('author_id',''))
    event_id=str(event.get('msg_id',''))
    if not user_id or not event_id or len(event_id)>100:return False
    if not KookPointEvent.objects.get_or_create(event_id=event_id)[1]:return False
    if is_voice:
        binding=KookBinding.objects.select_for_update().select_related('account').filter(user_id=user_id).first()
        if not binding or not allowed_account(binding.account):return False
        if binding.event_at and timestamp<=binding.event_at:return False
        # Late events cannot move a checkpoint backwards or double-count.
        at=max(timestamp,binding.accounted_at or timestamp)
        _accrue(binding,at)
        if kind=='joined_channel':binding.channel_id=str(body['channel_id'])
        elif binding.channel_id==str(body['channel_id']):binding.channel_id=''
        binding.event_at=timestamp;binding.save()
        return True
    content=str(event.get('content','')).strip()
    match=re.fullmatch(r'绑定\s+(XA[0-9A-F]{16})',content,re.I)
    if match:
        proof=KookBindingChallenge.objects.select_for_update().select_related('account').filter(
            code_hash=hashlib.sha256(match[1].upper().encode()).hexdigest(),expires_at__gte=now).first()
        if not proof or not allowed_account(proof.account):return False
        if KookBinding.objects.filter(user_id=user_id).exclude(account=proof.account).exists():return False
        if KookBinding.objects.filter(account=proof.account).exists():return False
        try:
            with transaction.atomic():
                KookBinding.objects.create(account=proof.account,user_id=user_id,nickname=str((extra.get('author') or {}).get('username',''))[:100])
        except IntegrityError:return False
        proof.delete();return True
    if content not in ['签到','/签到','打卡','/打卡']:return False
    binding=KookBinding.objects.select_for_update().select_related('account').filter(user_id=user_id).first()
    if not binding or not allowed_account(binding.account):return False
    day=timezone.localdate(timestamp)
    profile=locked_profile(binding.account)
    change_points(profile,500,f'KOOK_CHECKIN:{binding.account_id}:{day.isoformat()}',
        'KOOK_CHECKIN','KOOK签到大厅每日签到5积分')
    return True
