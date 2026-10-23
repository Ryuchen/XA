"""Receive KOOK events through a single supervised, read-only gateway client."""
import asyncio
import json
import logging
import random
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from engagement.models import KookBinding, KookPointsConfig
from engagement.kook_points import checkpoint, enabled_config, process_event, reset_presence
from integrations.kook_config import get_kook_config

logger=logging.getLogger(__name__)


class KookAPIError(RuntimeError):
    def __init__(self,path,code):
        self.code=code
        super().__init__(f'KOOK {path} 读取失败，代码{code}')


def read_api(token,path,**params):
    req=Request(settings.KOOK_API_BASE_URL+'/'+path+'?'+urlencode(params),
                headers={'Authorization':'Bot '+token,'Accept-Language':'zh-CN'})
    with urlopen(req,timeout=8) as response:body=json.load(response)
    if body.get('code')!=0:raise KookAPIError(path,body.get('code'))
    return body['data']


def voice_channels(token,guild):
    result=[]
    for page in range(1,51):
        data=read_api(token,'channel/list',guild_id=guild,type=2,page=page,page_size=100)
        result.extend(str(row['id']) for row in data['items'] if row.get('type')==2 and not row.get('is_category'))
        if page>=data.get('meta',{}).get('page_total',1):return result
    raise RuntimeError('KOOK语音频道列表分页不完整')


def seed_presence(token,channels):
    def load(channel):
        try:rows=read_api(token,'channel/user-list',channel_id=channel)
        except KookAPIError as exc:
            if exc.code!=40300:raise
            return channel,None
        return channel,[str(row['id']) for row in rows if not row.get('bot')]
    result={};unreadable=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for channel,users in pool.map(load,channels):
            if users is None:
                unreadable.append(channel);continue
            for user in users:result[user]=channel
    return result,unreadable


def connection_status(channels,unreadable):
    if unreadable:return f'已连接，{len(channels)-len(unreadable)}/{len(channels)}语音频道可读，{len(unreadable)}频道待查看权限'
    return '已连接，自动计分中'


def db_call(function,*args):
    close_old_connections()
    try:return function(*args)
    finally:close_old_connections()


def attach_new_presence(presence):
    now=timezone.now()
    for user,channel in presence.items():
        KookBinding.objects.filter(user_id=user,accounted_at__isnull=True).update(
            channel_id=channel,accounted_at=now,event_at=now)


def attach_verified_presence(presence):
    now=timezone.now()
    for user,channel in presence.items():
        KookBinding.objects.filter(user_id=user,channel_id='').update(
            channel_id=channel,accounted_at=now,event_at=now)


async def connection(lock):
    from autobahn.asyncio.websocket import WebSocketClientFactory,WebSocketClientProtocol
    cfg=await asyncio.to_thread(db_call,enabled_config)
    if not cfg:return
    runtime=await asyncio.to_thread(db_call,get_kook_config)
    if not runtime.enabled or not runtime.token:raise RuntimeError('KOOK机器人未配置')
    channels=await asyncio.to_thread(voice_channels,runtime.token,cfg.guild_id)
    await asyncio.to_thread(db_call,lambda:KookPointsConfig.objects.filter(pk=1).update(voice_channel_ids=channels,status='正在连接KOOK'))
    gateway=await asyncio.to_thread(read_api,runtime.token,'gateway/index',compress=0)
    address=gateway['url'];parsed=urlsplit(address)
    if parsed.scheme!='wss':raise RuntimeError('KOOK返回非安全网关')
    incoming=asyncio.Queue();loop=asyncio.get_running_loop()
    class Protocol(WebSocketClientProtocol):
        def onMessage(self,payload,isBinary):
            try:incoming.put_nowait(json.loads(payload.decode('utf8')))
            except (ValueError,UnicodeError):incoming.put_nowait(None)
        def onClose(self,wasClean,code,reason):incoming.put_nowait(None)
    factory=WebSocketClientFactory(address);factory.protocol=Protocol
    transport,protocol=await loop.create_connection(factory,parsed.hostname,parsed.port or 443,
        ssl=ssl.create_default_context(),server_hostname=parsed.hostname)
    try:
        hello=await asyncio.wait_for(incoming.get(),6)
        if not hello or hello.get('s')!=1 or hello.get('d',{}).get('code')!=0:raise RuntimeError('KOOK握手未通过')
        # Events arriving during the HTTP snapshot remain queued. After the
        # fresh baseline, older join/leave events cannot move time backwards.
        presence,unreadable=await asyncio.to_thread(seed_presence,runtime.token,channels)
        await asyncio.to_thread(db_call,reset_presence,presence)
        sn=0;pending={};next_ping=0;pong_due=None;next_channels=time.monotonic()+600
        while True:
            now=time.monotonic()
            if pong_due and now>pong_due:raise RuntimeError('KOOK心跳超时')
            if now>=next_ping:
                protocol.sendMessage(json.dumps({'s':2,'sn':sn}).encode())
                pong_due=now+6;next_ping=now+30+random.uniform(-5,5)
                await asyncio.to_thread(lock.extend,90,replace_ttl=True)
                await asyncio.to_thread(db_call,lambda:checkpoint(status=connection_status(channels,unreadable)))
                await asyncio.to_thread(db_call,attach_new_presence,presence)
            if now>=next_channels:
                latest=await asyncio.to_thread(db_call,enabled_config)
                if not latest or latest.guild_id!=cfg.guild_id or latest.checkin_channel_id!=cfg.checkin_channel_id:return
                cfg=latest
                previous_channels=set(channels)
                channels=await asyncio.to_thread(voice_channels,runtime.token,cfg.guild_id)
                # Newly accessible channels start counting from verified current
                # presence. Never invent time spent behind missing permissions.
                retry=[c for c in channels if c in unreadable or c not in previous_channels]
                if retry:
                    verified,still_unreadable=await asyncio.to_thread(seed_presence,runtime.token,retry)
                    presence.update(verified)
                    unreadable=still_unreadable
                    await asyncio.to_thread(db_call,attach_verified_presence,verified)
                else:unreadable=[]
                await asyncio.to_thread(db_call,lambda:KookPointsConfig.objects.filter(pk=1).update(voice_channel_ids=channels))
                next_channels=now+600
            try:packet=await asyncio.wait_for(incoming.get(),timeout=.5)
            except asyncio.TimeoutError:continue
            if packet is None:raise RuntimeError('KOOK连接中断')
            signal=packet.get('s')
            if signal==3:pong_due=None;continue
            if signal==5:raise RuntimeError('KOOK要求重连')
            if signal!=0:continue
            seq=packet.get('sn')
            if not isinstance(seq,int) or seq<=sn:continue
            pending[seq]=packet
            if len(pending)>1000:raise RuntimeError('KOOK事件序列缺失')
            while sn+1 in pending:
                event=pending.pop(sn+1)['d']
                await asyncio.to_thread(db_call,process_event,event)
                extra=event.get('extra') or {};body=extra.get('body') or {}
                if event.get('type')==255 and str(event.get('target_id'))==cfg.guild_id:
                    user=str(body.get('user_id',''));channel=str(body.get('channel_id',''))
                    if extra.get('type')=='joined_channel' and channel in channels:presence[user]=channel
                    elif extra.get('type')=='exited_channel' and presence.get(user)==channel:presence.pop(user,None)
                await asyncio.to_thread(db_call,attach_new_presence,presence)
                sn+=1
    finally:
        transport.close()
        await asyncio.to_thread(db_call,reset_presence,{})


class Command(BaseCommand):
    help='自动累计兴安电竞KOOK语音时长与签到积分；不向频道发送消息'
    def handle(self,*args,**options):
        import redis
        client=redis.Redis.from_url(settings.CELERY_BROKER_URL)
        lock=client.lock('xa:kook-points-listener',timeout=90,blocking_timeout=0,thread_local=False)
        async def run():
            backoff=2
            while True:
                if not await asyncio.to_thread(lock.acquire,blocking=False):
                    await asyncio.sleep(30);continue
                try:
                    await connection(lock);backoff=2
                except Exception as exc:
                    # Never log gateway URLs, bot tokens or channel content.
                    reason=str(exc)[:70] if isinstance(exc,RuntimeError) else type(exc).__name__
                    logger.warning('KOOK积分连接重试：%s',reason)
                    await asyncio.to_thread(db_call,lambda:KookPointsConfig.objects.filter(pk=1).update(status='连接重试：'+reason))
                finally:
                    try:await asyncio.to_thread(lock.release)
                    except redis.exceptions.LockError:pass
                await asyncio.sleep(backoff);backoff=min(backoff*2,60)
        asyncio.run(run())
