"""限流策略：只对写操作计数的 ScopedRateThrottle 变体。

为什么不直接用 DRF 自带的 ``ScopedRateThrottle``
------------------------------------------------
自带版本对声明了 ``throttle_scope`` 的视图**不分方法**一律计数。而本项目里
读写常常同挂一个 APIView（如 ``WithdrawView`` 的 GET 列表 + POST 提交），
给 POST 配的「10 次/分钟」会连带把用户刷新列表也掐死。

真正需要限流的是「会改状态、花钱、可被暴力枚举」的请求。因此这里让安全方法
（GET/HEAD/OPTIONS）直接豁免，把配额留给写操作。
"""

from rest_framework.throttling import ScopedRateThrottle

#: 不消耗限流配额的 HTTP 方法（RFC 7231 定义的安全方法）。
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


class WriteScopedRateThrottle(ScopedRateThrottle):
    """按 ``throttle_scope`` 限流，但只统计非安全方法。"""

    def allow_request(self, request, view):
        """安全方法直接放行；其余交给父类按 scope 计数。"""
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)
