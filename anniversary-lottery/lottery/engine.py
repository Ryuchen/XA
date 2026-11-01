"""Server-side draws and audited entry grants for the independent lottery app."""
import hashlib
import json
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .catalog import DENOMINATOR, ENDS_AT, NAMES, PITY_AMOUNTS, STARTS_AT, catalog_cells
from .dice import DENOMINATOR as DICE_DENOMINATOR, roll_dice
from .models import Award, BonusAward, Cell, Draw, DrawBatch, Entry, EntryGrant, Participant, Pool, Room


BATCH_SIZE = 10
ROLLS_PER_MINUTE = 60
ROLLS_PER_DAY = 1_000


RULES = [
    '活动时间以当前房间公示的北京时间为准；结束时间不含该时刻。',
    '结果由服务器按当前奖池规则随机生成；骰子动画展示最终行、列点数，并与海报奖格及抽奖记录对应。',
    '管理员为老板发放指定奖池的次数；每次消耗1次，再来一次返还本次次数。活动结束后未使用次数无法抽奖。',
    '十连抽需要本奖池至少10次可用机会；十次结果一起确认，再来一次仍返还对应机会，每次均计入累计抽数和保底。',
    '此独立活动不收取余额、积分或任何费用。抽中积分、店铺余额、冠名、TAG和优惠券后生成待发放记录，由管理员核对交付。',
    '每位老板每个奖池分别累计：每完成30次及50次抽奖，分别额外获得该奖池标注的两档店铺余额；普通中奖不重置累计，可重复获得，第150次同时获得两份保底。',
    '小红奖池30次/50次保底分别为5元/10元，大红为10元/20元，大大红为20元/40元店铺余额。',
    '再来一次也计入累计抽数。保底为骰子结果以外的额外奖励，不替换海报奖格；所有余额奖励均由管理员核对发放。',
]


def remaining_only():
    return settings.LOTTERY_REMAINING_ONLY


def require_admissions_open():
    if remaining_only():
        raise ValidationError('当前仅开放历史剩余次数补抽，已停止新建房间、添加老板和发放次数。')


def activity_rules():
    rules = list(RULES)
    if remaining_only():
        rules[0] = '当前仅开放历史剩余次数补抽，不受原活动结束时间限制；房间须已开始且未暂停，账号须处于启用状态。'
        rules[2] = '仅可使用此前已发放的本奖池剩余次数，不再添加参与资格或次数；每次消耗1次，再来一次返还本次次数。'
    return rules


def request_key(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 64 or not all(c.isascii() and (c.isalnum() or c in '-_') for c in value):
        raise ValidationError('请提供8–64位字母、数字、横线或下划线的操作凭证')
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def percent(probability, denominator=DENOMINATOR):
    return format(Decimal(probability) * 100 / Decimal(denominator), '.10f')


def room_status(room, now=None):
    now = now or timezone.now()
    if not room.enabled:
        return 'disabled'
    if now < room.starts_at:
        return 'upcoming'
    if now >= room.ends_at and not remaining_only():
        return 'ended'
    return 'active'


def room_data(room):
    return {'id': str(room.pk), 'code': room.code, 'name': room.name,
            'enabled': room.enabled, 'starts_at': room.starts_at,
            'ends_at': room.ends_at, 'status': room_status(room),
            'remaining_only': remaining_only()}


def cell_data(cell, weights=None):
    probability = cell.probability
    denominator = DENOMINATOR
    if weights and 'cell_weights' in weights:
        probability = weights['cell_weights'][cell.row - 1][cell.column - 1]
        denominator = weights['joint_denominator']
    elif weights:
        probability = weights['row_weights'][cell.row - 1] * weights['column_weights'][cell.column - 1]
        denominator = weights['denominator'] ** 2
    return {'id': cell.pk, 'row': cell.row, 'column': cell.column, 'label': cell.label,
            'kind': cell.kind, 'probability': probability,
            'probability_percent': percent(probability, denominator),
            'reward_kind': cell.reward_data.get('kind'),
            'quantity': cell.reward_data.get('quantity', 1)}


def catalog_data(room=None):
    campaigns = []
    for pool in Pool.objects.prefetch_related('cells').order_by('id'):
        campaigns.append({'id': pool.pk, 'slug': pool.slug, 'name': pool.name,
                          'version': pool.version, 'enabled': pool.enabled,
                          'pity_rewards': pity_rewards(pool.slug),
                          'status': room_status(room) if pool.enabled and room else 'active' if pool.enabled else 'disabled',
                          'cells': [{key: value for key, value in cell_data(cell).items()
                                     if key not in ('probability', 'probability_percent')}
                                    for cell in pool.cells.all()]})
    return {'title': '兴安电竞 · 半周年店庆',
            'starts_at': room.starts_at if room else STARTS_AT,
            'ends_at': room.ends_at if room else ENDS_AT,
            'timezone': 'Asia/Shanghai', 'rules': activity_rules(), 'campaigns': campaigns,
            'remaining_only': remaining_only(), 'entry_grants_enabled': not remaining_only(),
            'room': room_data(room) if room else None}


def award_data(award):
    return {'id': award.pk, 'label': award.label, 'kind': award.kind, 'data': award.data,
            'status': award.status, 'status_label': award.get_status_display(),
            'delivery_note': award.delivery_note, 'fulfilled_at': award.fulfilled_at}


def draw_data(result):
    award = getattr(result, 'award', None)
    return {**result.result_snapshot, 'id': result.pk, 'created_at': result.created_at,
            'participant_id': result.participant_id, 'account_id': result.participant_id,
            'room_code': result.room.code, 'room_id': str(result.room_id),
            'batch_id': result.batch_id, 'batch_index': result.batch_index,
            'entries_remaining': result.entries_remaining,
            'total_rolls': result.roll_number,
            'pity': result.result_snapshot.get('pity') if 'lower_yuan' in result.result_snapshot.get('pity', {})
                    else pity_data(result.roll_number, result.pool.slug),
            'bonuses': [award_data(bonus) for bonus in result.bonuses.all()],
            'award': award_data(award) if award else None,
            'claim': award_data(award) if award else None}


def batch_data(batch):
    results = list(batch.draws.select_related('award', 'room', 'pool').prefetch_related('bonuses').order_by('batch_index'))
    if len(results) != BATCH_SIZE or any(result.participant_id != batch.participant_id
            or result.room_id != batch.room_id or result.pool_id != batch.pool_id for result in results):
        raise ValidationError('十连抽记录不完整，请联系管理员核对；请勿重新抽取')
    draws = [draw_data(result) for result in results]
    bonuses = [{**bonus, 'draw_id': result['id'], 'batch_index': result['batch_index']}
               for result in draws for bonus in result['bonuses']]
    return {'id': batch.pk, 'batch_id': batch.pk,
            'participant_id': batch.participant_id, 'account_id': batch.participant_id,
            'room_code': batch.room.code, 'room_id': str(batch.room_id),
            'campaign': results[-1].result_snapshot['campaign'],
            'campaign_name': results[-1].result_snapshot['campaign_name'],
            'count': batch.count, 'entries_remaining': batch.entries_remaining,
            'entries_consumed': batch.entries_consumed, 'total_rolls': batch.total_rolls,
            'pity': batch.pity_snapshot, 'created_at': batch.created_at,
            'draws': draws, 'bonuses': bonuses}


def history_data(participant, *, page=1, page_size=10, before_id=None):
    for value, minimum, maximum, label in [(page, 1, 2_147_483_647, '页码'), (page_size, 1, 30, '每页数量')]:
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValidationError(f'{label}超出允许范围')
    if before_id is not None and (type(before_id) is not int or not 0 <= before_id <= 9_223_372_036_854_775_807):
        raise ValidationError('历史记录锚点无效')
    owned = Draw.objects.filter(participant_id=participant.pk, room_id=participant.room_id)
    maximum_id = owned.aggregate(value=Max('id'))['value'] or 0
    anchor = maximum_id if before_id is None else min(before_id, maximum_id)
    snapshot = owned.filter(id__lte=anchor)
    total = snapshot.count()
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    results = snapshot.select_related('award', 'room', 'pool').prefetch_related('bonuses').order_by('-id')[start:start + page_size]
    return {'participant_id': participant.pk, 'account_id': participant.pk,
            'room_code': participant.room.code, 'room_id': str(participant.room_id),
            'draws': [draw_data(result) for result in results],
            'pagination': {'page': page, 'page_size': page_size, 'total': total,
                           'total_pages': total_pages, 'has_previous': page > 1,
                           'has_next': page < total_pages, 'before_id': anchor}}


def participant_data(participant):
    entries = dict.fromkeys(NAMES, 0)
    pity = {slug: pity_data(0, slug) for slug in NAMES}
    for entry in participant.entries.select_related('pool'):
        entries[entry.pool.slug] = entry.remaining
        pity[entry.pool.slug] = pity_data(entry.total_rolls, entry.pool.slug)
    results = participant.draws.filter(room_id=participant.room_id).select_related('award', 'room').prefetch_related('bonuses').order_by('-id')[:30]
    return {'participant_id': participant.pk, 'account_id': participant.pk, 'business_cycle': 1,
            'nickname': participant.nickname, 'entries': entries, 'pity': pity,
            'room': room_data(participant.room), 'draws': [draw_data(result) for result in results]}


def require_superuser(operator):
    if operator is None or not operator.is_active or not operator.is_superuser:
        raise PermissionDenied('仅超级管理员可以执行此操作')


def pity_rewards(pool_slug):
    return {'lower_yuan': PITY_AMOUNTS[pool_slug][30] // 100,
            'upper_yuan': PITY_AMOUNTS[pool_slug][50] // 100,
            'lower_interval': 30, 'upper_interval': 50}


def pity_data(total_rolls, pool_slug):
    amounts = PITY_AMOUNTS[pool_slug]
    return {'total_rolls': total_rolls, 'until_lower': 30 - total_rolls % 30,
            'until_upper': 50 - total_rolls % 50,
            'lower_yuan': amounts[30] // 100, 'upper_yuan': amounts[50] // 100}


@transaction.atomic
def grant_entries(participant, pool_slug, count, key, reason, operator):
    require_superuser(operator)
    event_key = f'grant:{request_key(key)}'
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10_000:
        raise ValidationError('发放次数必须为1–10000的整数')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 255:
        raise ValidationError('请填写1–255字的发放原因')
    reason = reason.strip()
    participant = Participant.objects.select_for_update().get(pk=participant.pk)
    pool = Pool.objects.select_for_update().get(slug=pool_slug)
    existing = EntryGrant.objects.select_related('entry').filter(event_key=event_key).first()
    if existing:
        if (existing.entry.participant_id, existing.entry.pool_id, existing.count, existing.reason, existing.operator_id) != (
                participant.pk, pool.pk, count, reason, operator.pk):
            raise ValidationError('同一发放凭证不能用于不同内容')
        return existing
    require_admissions_open()
    entry, _ = Entry.objects.get_or_create(participant=participant, pool=pool)
    if entry.remaining + count > 100_000 or entry.granted + count > 2_147_483_647:
        raise ValidationError('次数超过允许范围')
    entry.remaining += count
    entry.granted += count
    entry.save(update_fields=['remaining', 'granted', 'updated_at'])
    return EntryGrant.objects.create(entry=entry, event_key=event_key, count=count, reason=reason, operator=operator)


def validate_configuration(pool, cells):
    if pool.slug not in NAMES or len(cells) != 36 or {(c.row, c.column) for c in cells} != {(r, c) for r in range(1, 7) for c in range(1, 7)}:
        raise ValidationError('奖池配置不完整，抽奖暂时关闭')
    expected = {(item['row'], item['column']): item for item in catalog_cells(pool.slug)}
    for cell in cells:
        original = expected[(cell.row, cell.column)]
        if cell.kind not in ('REWARD', 'THANKS', 'RETRY'):
            raise ValidationError('奖格类型有误')
        if (cell.label, cell.kind, cell.reward_data) != (original['label'], original['kind'], original['reward_data']):
            raise ValidationError('奖池与活动海报不一致，抽奖暂时关闭')


def _validate_version(expected_version):
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
        raise ValidationError('请提供有效的奖池版本')


def _locked_participant(participant):
    authenticated_digest = participant.code_digest
    authenticated_room_id = participant.room_id
    participant = Participant.objects.select_for_update().get(pk=participant.pk)
    if participant.code_digest != authenticated_digest or participant.room_id != authenticated_room_id:
        raise ValidationError('参与码已经重置，请重新进入房间')
    return participant


def _prepare_rolls(participant, pool_slug, expected_version, count):
    room = Room.objects.select_for_update().get(pk=participant.room_id)
    if not participant.enabled:
        raise ValidationError('此参与资格已停用')
    now = timezone.now()
    if room_status(room, now) != 'active':
        raise ValidationError('抽奖房间尚未开始、已经结束或暂时关闭')
    pool = Pool.objects.select_for_update().filter(slug=pool_slug).first()
    if pool is None or not pool.enabled:
        raise ValidationError('奖池不存在或已暂停')
    if expected_version != pool.version:
        raise ValidationError('奖池配置已更新，请刷新后再试')
    entry = Entry.objects.select_for_update().filter(participant=participant, pool=pool).first()
    if entry is None or entry.remaining < count:
        if remaining_only():
            raise ValidationError('本奖池剩余次数不足10次，可使用单抽；补抽期间不再添加次数。' if count == BATCH_SIZE
                                  else '本奖池没有剩余次数；补抽期间不再添加次数。')
        raise ValidationError('十连抽至少需要本奖池10次可用机会，请联系管理员添加' if count == BATCH_SIZE
                              else '此奖池暂无参与次数，请联系房间管理员添加')
    if entry.total_rolls + count > 2_147_483_647:
        raise ValidationError('累计抽数达到允许范围，请联系管理员')
    recent = participant.draws
    if recent.filter(created_at__gte=now - timedelta(minutes=1)).count() + count > ROLLS_PER_MINUTE:
        raise ValidationError('操作频繁，请一分钟后再试')
    if recent.filter(created_at__gte=now - timedelta(days=1)).count() + count > ROLLS_PER_DAY:
        raise ValidationError('今日参与次数达到上限，请明天再试')
    cells = list(pool.cells.order_by('row', 'column'))
    validate_configuration(pool, cells)
    return room, pool, entry, cells


def _record_roll(participant, room, pool, entry, cells, *, event_key, fp, batch=None, batch_index=None):
    """Caller owns the participant/room/pool/entry locks and outer transaction."""
    dice = roll_dice(pool.slug)
    selected = next(cell for cell in cells if (cell.row, cell.column) == (dice['row'], dice['column']))
    entry.remaining -= 1
    if selected.kind == 'RETRY':
        entry.remaining += 1
    else:
        entry.consumed += 1
    entry.total_rolls += 1
    entry.save(update_fields=['remaining', 'consumed', 'total_rolls', 'updated_at'])
    snapshot = {**cell_data(selected, dice), 'campaign': pool.slug, 'campaign_name': pool.name,
                'pool': pool.slug, 'version': pool.version, 'reward_data': selected.reward_data,
                'room_id': str(room.pk), 'room_name': room.name,
                'total_rolls': entry.total_rolls, 'pity': pity_data(entry.total_rolls, pool.slug)}
    snapshot.pop('id')
    result = Draw.objects.create(participant=participant, room=room, pool=pool, cell=selected,
        batch=batch, batch_index=batch_index,
        event_key=event_key, request_fingerprint=fp, campaign_version=pool.version,
        random_ticket=dice['row_ticket'], second_random_ticket=dice['column_ticket'],
        roll_number=entry.total_rolls, result_snapshot=snapshot,
        probability_snapshot={**dice, 'joint_denominator': dice.get('joint_denominator', DICE_DENOMINATOR ** 2),
                              'cells': [cell_data(cell, dice) for cell in cells]},
        entries_remaining=entry.remaining)
    if selected.kind == 'REWARD':
        Award.objects.create(draw=result, label=selected.label, kind=selected.reward_data['kind'],
                             data=selected.reward_data)
    for threshold, amount in PITY_AMOUNTS[pool.slug].items():
        if entry.total_rolls % threshold == 0:
            BonusAward.objects.create(draw=result, threshold=threshold,
                label=f'{threshold}次保底 · {amount // 100}元店铺余额',
                data={'balance_amount': amount, 'threshold': threshold, 'roll_number': entry.total_rolls})
    return result


@transaction.atomic
def draw(participant, pool_slug, key, expected_version):
    """Replay before mutable checks; serialize all operations on one participant."""
    event_key = f'draw:{participant.pk}:{request_key(key)}'
    _validate_version(expected_version)
    fp = fingerprint({'pool': pool_slug, 'version': expected_version})
    participant = _locked_participant(participant)
    existing = Draw.objects.select_related('award', 'room', 'pool').prefetch_related('bonuses').filter(event_key=event_key).first()
    if existing:
        if existing.room_id != participant.room_id:
            raise ValidationError('此抽奖记录不属于当前房间')
        if existing.request_fingerprint != fp:
            raise ValidationError('同一操作凭证不能用于不同奖池或版本')
        return existing
    if DrawBatch.objects.filter(event_key=f'batch:{participant.pk}:{key}').exists():
        raise ValidationError('同一操作凭证不能从十连抽改为单抽')
    room, pool, entry, cells = _prepare_rolls(participant, pool_slug, expected_version, 1)
    return _record_roll(participant, room, pool, entry, cells, event_key=event_key, fp=fp)


@transaction.atomic
def draw_batch(participant, pool_slug, key, expected_version):
    event_key = f'batch:{participant.pk}:{request_key(key)}'
    _validate_version(expected_version)
    fp = fingerprint({'pool': pool_slug, 'version': expected_version, 'count': BATCH_SIZE})
    participant = _locked_participant(participant)
    existing = DrawBatch.objects.select_related('room', 'pool').filter(event_key=event_key).first()
    if existing:
        if existing.room_id != participant.room_id:
            raise ValidationError('此十连抽记录不属于当前房间')
        if existing.request_fingerprint != fp:
            raise ValidationError('同一十连抽凭证不能用于不同奖池或版本')
        return existing
    if Draw.objects.filter(event_key=f'draw:{participant.pk}:{key}').exists():
        raise ValidationError('同一操作凭证不能从单抽改为十连抽')
    room, pool, entry, cells = _prepare_rolls(participant, pool_slug, expected_version, BATCH_SIZE)
    batch = DrawBatch.objects.create(participant=participant, room=room, pool=pool,
        event_key=event_key, request_fingerprint=fp, campaign_version=pool.version,
        initial_entries=entry.remaining, entries_remaining=entry.remaining)
    initial_consumed = entry.consumed
    for index in range(1, BATCH_SIZE + 1):
        _record_roll(participant, room, pool, entry, cells, event_key=f'batch-draw:{batch.pk}:{index}',
                     fp=fingerprint({'batch': batch.pk, 'index': index}), batch=batch, batch_index=index)
    batch.entries_remaining = entry.remaining
    batch.entries_consumed = entry.consumed - initial_consumed
    batch.total_rolls = entry.total_rolls
    batch.pity_snapshot = pity_data(entry.total_rolls, pool.slug)
    batch.save(update_fields=['entries_remaining', 'entries_consumed', 'total_rolls', 'pity_snapshot'])
    return batch


@transaction.atomic
def fulfill_award(award_id, operator, note):
    require_superuser(operator)
    if not isinstance(note, str) or not note.strip() or len(note) > 500:
        raise ValidationError('请填写1–500字的实际发放说明')
    award = Award.objects.select_for_update().get(pk=award_id)
    if award.status == Award.Status.FULFILLED:
        return award
    award.status = Award.Status.FULFILLED
    award.delivery_note = note.strip()
    award.fulfilled_by = operator
    award.fulfilled_at = timezone.now()
    award.save(update_fields=['status', 'delivery_note', 'fulfilled_by', 'fulfilled_at'])
    return award


@transaction.atomic
def seed_catalog():
    """Import 108 poster cells without resetting entries, draws or activation."""
    for slug, name in NAMES.items():
        pool, created = Pool.objects.get_or_create(slug=slug, defaults={'name': name})
        pool = Pool.objects.select_for_update().get(pk=pool.pk)
        frozen = pool.draws.exists()
        changed = False
        if pool.name != name:
            if frozen:
                raise ValidationError('已有抽奖记录，不能覆盖奖池名称；请创建新版本活动')
            pool.name = name
            changed = True
        for data in catalog_cells(slug):
            data = dict(data)
            row, column = data.pop('row'), data.pop('column')
            cell, cell_created = Cell.objects.get_or_create(pool=pool, row=row, column=column, defaults=data)
            differences = {field: value for field, value in data.items() if getattr(cell, field) != value}
            if differences:
                if frozen:
                    raise ValidationError('已有抽奖记录，不能覆盖奖项或概率；请创建新版本活动')
                for field, value in differences.items():
                    setattr(cell, field, value)
                cell.save(update_fields=list(differences))
                changed = True
            changed = changed or (cell_created and not created)
        if pool.cells.count() != 36:
            raise ValidationError('奖池存在海报以外的奖格，导入已撤销')
        if changed:
            pool.version += 1
            pool.save(update_fields=['name', 'version'])
    return Pool.objects.count()
