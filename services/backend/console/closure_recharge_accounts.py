"""闭店守卫的后台新增充值/账号入口适配器；不触碰公共资金函数。"""


def _new_activity_closure_refusal():
    from stores.closure import closure_refusal_response
    from stores.context import current_store

    # 租户只由 Host/token 中间件确定，不能从请求参数选择闭店状态。
    return closure_refusal_response(current_store())


def recharge_closure_refusal():
    return _new_activity_closure_refusal()


def account_create_closure_refusal():
    return _new_activity_closure_refusal()
