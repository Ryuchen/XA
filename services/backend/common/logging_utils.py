"""结构化日志基础设施：JSON 行日志 + 资金事件专用记录器。

为什么不引入 ``python-json-logger`` 之类的第三方库
------------------------------------------------
资金链路的日志是审计证据，依赖越少越好；标准库 ``logging`` 已经足够，
一个 40 行的 Formatter 就能输出稳定的 JSON 行，避免为了格式化再压一个依赖。

字段约定（与运维侧采集规则对齐，勿随意增删既有键）
------------------------------------------------
``ts`` / ``level`` / ``logger`` / ``module`` / ``message`` 为通用字段；
``account_id`` / ``order_no`` / ``tx_type`` / ``amount`` /
``balance_before`` / ``balance_after`` / ``trace_id`` 为资金事件字段，
仅在调用方通过 ``extra`` 传入时才出现，不会污染普通日志。
"""

import json
import logging
import uuid
from typing import Any, Dict, Optional

# 资金事件专用 logger 名称：运维侧按此名单独采集与告警。
MONEY_LOGGER_NAME = 'xa.money'

# 业务字段白名单：只有这些键会被 Formatter 从 record 上提取到 JSON 顶层，
# 避免 LogRecord 的内部属性（args/exc_info/...）被误当成业务字段输出。
BUSINESS_FIELDS = (
    'event',
    'account_id',
    'user_id',
    'order_no',
    'order_id',
    'request_id',
    'tx_type',
    'amount',
    'balance_before',
    'balance_after',
    'trace_id',
    'idempotent_hit',
    'operator_id',
    'reason',
)


class JsonLogFormatter(logging.Formatter):
    """把 ``LogRecord`` 序列化成单行 JSON，便于 ELK / Loki 直接结构化检索。"""

    #: ISO8601（本地时区）时间戳格式。
    default_time_format = '%Y-%m-%dT%H:%M:%S'
    default_msec_format = '%s.%03d'

    def format(self, record: logging.LogRecord) -> str:
        """渲染一条日志为 JSON 字符串。"""
        payload: Dict[str, Any] = {
            'ts': self.formatTime(record),
            'level': record.levelname,
            'logger': record.name,
            'module': f'{record.module}.{record.funcName}:{record.lineno}',
            'message': record.getMessage(),
        }
        for field in BUSINESS_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload['exc_info'] = self.formatException(record.exc_info)
        if record.stack_info:
            payload['stack_info'] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def new_trace_id() -> str:
    """生成一次资金操作的追踪 ID（同一笔业务的多条流水共享）。"""
    return uuid.uuid4().hex


def get_money_logger() -> logging.Logger:
    """返回资金事件专用 logger。"""
    return logging.getLogger(MONEY_LOGGER_NAME)


def log_money_event(
    event: str,
    *,
    account_id: Optional[int] = None,
    user_id: Optional[int] = None,
    order_no: str = '',
    order_id: Optional[int] = None,
    request_id: str = '',
    tx_type: str = '',
    amount: Optional[int] = None,
    balance_before: Optional[int] = None,
    balance_after: Optional[int] = None,
    trace_id: str = '',
    idempotent_hit: Optional[bool] = None,
    operator_id: Optional[int] = None,
    reason: str = '',
    level: int = logging.INFO,
) -> None:
    """记录一条结构化资金事件。

    所有金额参数一律为**内部账务单位整数**（10 = 1 兴安币），与后端全链路口径一致；
    这里绝不做 ÷10 换算，避免日志与流水对不上账。

    Args:
        event: 事件名，如 ``withdraw.submit`` / ``order.settle``。
        account_id: 业务账户 ID（ClubAccount）。
        user_id: 兼容期的 legacy 用户 ID。
        order_no: 订单号（有则必填，便于按单串起全链路）。
        order_id: 订单主键。
        request_id: 客户端幂等键。
        tx_type: 流水类型，取值同 ``wallet.Transaction.TxType``。
        amount: 本次变动金额（正数入账 / 负数出账）。
        balance_before: 变动前余额。
        balance_after: 变动后余额。
        trace_id: 同一笔业务的追踪 ID。
        idempotent_hit: 是否命中幂等（True 表示重复请求被拦下）。
        operator_id: 人工操作人（后台调账等）。
        reason: 备注 / 原因。
        level: 日志级别，默认 ``logging.INFO``。
    """
    extra: Dict[str, Any] = {'event': event}
    optional = {
        'account_id': account_id,
        'user_id': user_id,
        'order_no': order_no or None,
        'order_id': order_id,
        'request_id': request_id or None,
        'tx_type': tx_type or None,
        'amount': amount,
        'balance_before': balance_before,
        'balance_after': balance_after,
        'trace_id': trace_id or None,
        'idempotent_hit': idempotent_hit,
        'operator_id': operator_id,
        'reason': reason or None,
    }
    extra.update({key: value for key, value in optional.items() if value is not None})
    get_money_logger().log(level, event, extra=extra)
