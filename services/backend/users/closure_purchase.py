"""闭店守卫的通行证购买入口适配器；不处理报价、退款或读取。"""


def provider_pass_closure_refusal():
    from stores.closure import closure_refusal_response
    from stores.context import current_store

    # 租户只由 Host/token 中间件确定，不能从请求参数选择闭店状态。
    return closure_refusal_response(current_store())
