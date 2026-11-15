"""Generate an evidence-based first-release decision review index.

Unreviewed is deliberately not a synonym for unimplemented. Add evidence only
after checking both the actual entry point and the scope of its tests.
"""
from collections import Counter
from pathlib import Path

from generate_trace import DOCS, MAP, SUPPLEMENTAL_D, numbers, rows


OUT = Path(__file__).with_name('19-产品决策实现状态.md')
HISTORICAL = {
    'D149', 'D150', 'D151', 'D152', 'D153', 'D154', 'D155', 'D156',
    'D157', 'D158', 'D160', 'D170',
}
PARTIAL = {
    'D93': ('排序已有多项口径；普通店币单陪转单原价及未入队零贡献已在隔离环境部分验证，双陪/退款组合及正式抢单时钟尚未贯通。',
            '../../services/backend/orders/tests/test_grab_unstarted_work_stint.py'),
    'D142': ('异议导致结单条件失效后，新申请核实按当时门店配置开始完整时限；单店真实接口已测，双店和 MySQL 并发尚未验证。',
             '../../services/backend/orders/tests/test_room_completion_review.py'),
    'D141': ('转单时待核实或已核实的旧结单申请失效并保留记录，新陪玩可重新提交、客服重新核实；双店与 MySQL 并发尚未验证。',
             '../../services/backend/orders/tests/test_room_transfer_confirmation.py'),
    'D45': ('默认配置已记录本人履约段，双店单陪/双陪转单、转回与真实全退等场景通过；本人结单 MySQL 专项并发及全部组合仍待核验。',
            '../../services/backend/orders/tests/test_provider_transfer_ranking.py'),
    'D201': ('双陪首次上传结单截图保存本人结束事实，换图不改时点；双店及排行榜回归已测，MySQL 并发和未分段转单仍待核验。',
             '../../services/backend/orders/tests/test_provider_completion_ranking.py'),
    'D13': ('本店预存档位从高到低可重复组合，双店真实充值及审批测试覆盖 7000、9000 和 12000 档；MySQL 并发尚未验证。',
            '../../services/backend/wallet/test_recharge_pricing_multistore.py'),
    'D53': ('客服名片绑定本店员工，普通下单、预约转单及客服代单保存负责人；双店接口已测，咨询和工资完整联动及 MySQL 并发仍待核验。',
            '../../services/backend/support/test_staff_assignment.py'),
    'D18': ('默认服务付款积分现仅按本金及付款时会员/生日倍率计算；赠送来源及全额退款双店场景通过，推荐佣金来源与部分退款等仍未完整。',
            '../../services/backend/engagement/test_principal_points_multistore.py'),
    'D05': ('首版门店不再预置财务角色和岗位，客服具备充值及提现初审权限；双店身份隔离与受控资金开关下的充值至售后和薪资流程已验证，平台提现仍需联合验收。',
            '../../services/backend/stores/test_store_finance_role.py'),
    'D14': ('双店真实充值与审批接口验证本金先到账、同决定幂等、通过或驳回按相应金额发赠送；未验证 MySQL 并发及真实线下收款。',
             '../../services/backend/wallet/test_recharge_pricing_multistore.py'),
    'D200': ('已隔离验证确认后的单陪、双陪预约在产生退款押金缺口后仍可付款转单；MySQL 竞争尚未验证。',
             '../../services/backend/orders/tests/test_reservation_refund_deficit_multistore.py'),
    'D08': ('平台统一微信短信配置及渠道不可用处理有隔离测试；微信和短信真实服务尚未接入验收。',
            '../../services/backend/users/test_sms_transport.py'),
    'D25': ('通行证积分购买已默认启用并经隔离测试；普通服务积分付款及其完整售后组合尚未开放。',
            '../../services/backend/users/test_pass_points_default.py'),
    'D30C': ('已隔离验证七天边界、新业务重算及线下押金缴清后起算；全部资金与并发组合仍需核验。',
             '../../services/backend/wallet/test_deposit_clearance.py'),
    'D34': ('成就与会员标签共享本店非礼物实销口径；公开排行榜授权另有测试，完整榜单口径仍需逐项核验。',
            '../../services/backend/engagement/achievement_metrics.py'),
    'D46': ('共享池接待及咨询已读角色边界已有隔离测试；全部消息通知及异常恢复仍需核验。',
            '../../services/backend/chat/test_account_workflow.py'),
    'D76': ('积分使用联动和通行证积分付款默认启用，商城、抽奖、通行证及双店已有隔离测试；服务订单积分付款仍受开关限制。',
            '../../services/backend/engagement/test_points_usage.py'),
    'D79': ('双陪席位与单陪预约冲突已有隔离测试；转单和取消的 MySQL 并发仍需核验。',
            '../../services/backend/orders/tests/test_reservation_team.py'),
    'D82': ('双陪本人确认与无权限用户拒绝已有隔离测试；全部单陪和后台入口仍需核验。',
            '../../services/backend/orders/tests/test_reservation_team.py'),
    'D90': ('双陪分别确认、拒绝后补位及双方预约资格已有隔离测试；全部超时并发与资金组合仍需核验。',
            '../../services/backend/orders/tests/test_reservation_team.py'),
    'D104': ('通行证即时生效及有效期内禁止购买已有隔离测试，MySQL 实测并发购买仅成功一次。',
             '../../services/backend/users/test_pass_points_mysql.py'),
    'D135': ('通用房间由陪玩申请、客服核实、老板确认或超时确认；默认流程及后续收益已有隔离测试，复杂资金组合仍需核验。',
             '../../services/backend/orders/tests/test_room_release_readiness.py'),
    'D138': ('通用房间已有订单、角色、消息和结单流程；九种具体玩法不在本期，复杂资金衔接仍需核验。',
             '../../services/backend/orders/tests/test_room_release_readiness.py'),
    'D139': ('按本店时限保存确认快照；开单前须配置，缺失和异常不付款，自动确认及双店隔离已有隔离测试。',
             '../../services/backend/orders/tests/test_room_release_readiness.py'),
    'D140': ('异议暂停及恢复已测；独立 MySQL 验证异议与确认／自动确认的行锁竞争，其他退款组合仍需核验。',
             '../../services/backend/orders/tests/test_room_boss_confirmation_mysql.py'),
    'D187': ('补资写入层重复提交及付款组合有隔离测试；完整真实结算及并发尚待验证，相关入口受内部开关限制。',
             '../../services/backend/orders/tests/test_addon_shared_funding.py'),
    'D188': ('共享到账分配及原请求重试有隔离测试；多订单完整真实链路和并发尚待验证。',
             '../../services/backend/orders/tests/test_addon_shared_funding.py'),
    'D189': ('共享到账凭证有隔离测试；真实线下到账与跨库故障恢复尚待验证。',
             '../../services/backend/orders/tests/test_addon_shared_funding.py'),
    'D190': ('共享到账分配有隔离测试；两种用途全部组合及并发尚待验证。',
             '../../services/backend/orders/tests/test_addon_shared_funding.py'),
    'D193': ('主单净实付与加购佣金口径已有局部真实业务测试；复杂退款、转单组合未全部验证。',
             '../../services/backend/orders/tests/test_ordinary_refund_without_addon_funding.py'),
    'D194': ('普通订单售后与补资的部分组合已有隔离接口测试；全退零额仅测试开关内接通，其他组合和正式开放仍待完成。',
             '../../services/backend/orders/tests/test_ordinary_refund_without_addon_funding.py'),
    'D198': ('VIP 停用时的实销成就链路已测；其他授权状态和完整组合尚需核验。',
             '../../services/backend/orders/tests/test_ordinary_refund_without_addon_funding.py'),
    'D199': ('已确认加购净额计成就、主单去重及两店隔离已有局部链路测试；全部退款组合未验证。',
             '../../services/backend/orders/tests/test_ordinary_refund_without_addon_funding.py'),
}


def main():
    decisions = rows('D')
    identifiers = [ident for ident, _ in decisions]
    if len(identifiers) != len(set(identifiers)) or set(PARTIAL) - set(identifiers) or HISTORICAL - set(identifiers):
        raise ValueError('决策编号重复或状态引用了不存在的决策')
    mappings = {owner: numbers(expression) for owner, expression in MAP['D'].items()}
    counts = Counter('首版不适用' if ident in HISTORICAL else '部分验证' if ident in PARTIAL else '待核验'
                     for ident in identifiers)
    lines = [
        '# 19 产品决策后端实现状态', '',
        '> 初次建立：2026-11-12。此表是逐项核对工作底稿，状态只代表已记录的证据范围。', '',
        f"共 {len(decisions)} 条决策：部分验证 {counts['部分验证']} 条、首版不适用 {counts['首版不适用']} 条、待核验 {counts['待核验']} 条。",
        '**待核验不等于未实现**；已写代码、仅内部开关可运行、隔离测试通过和端到端可交付是不同状态。本表不能据此推算完成率或剩余开发量。', '',
        '首版不接管旧订单或迁移旧业务数据；D157 所述旧站九种具体玩法本期不开发，通用房间能力仍属于首版范围。新订单的抽成、时限快照、抢单、资金及门店初始化仍须实现。', '',
        '本表仅覆盖决策 D；License 专项规则、差距 G 与验收场景 T 还须分别核对，不能以决策数量替代完整范围。记录状态时应引用真实入口、配置及隔离业务测试，并注明外部服务、内部测试开关与未验证边界。', '',
        '| 决策 | 需求摘要 | 工作包 | 当前记录状态 | 证据及边界 |',
        '| --- | --- | --- | --- | --- |',
    ]
    for ident, title in decisions:
        owners = (SUPPLEMENTAL_D[ident] if ident in SUPPLEMENTAL_D else
                  [key for key in sorted(mappings) if int(ident[1:]) in mappings[key]])
        refs = '、'.join(f'[{owner}]({DOCS[owner]})' for owner in owners)
        if ident in HISTORICAL:
            status, note = '首版不适用', '旧数据／旧订单切换；新系统同类规则仍须分别核验。'
        elif ident in PARTIAL:
            status = '部分验证'
            explanation, path = PARTIAL[ident]
            note = f'{explanation} [证据]({path})'
        else:
            status, note = '待核验', '尚未在本表登记足够证据；不代表未实现。'
        lines.append(f'| [{ident}](../prd/20-产品决策逐项复核.md) | {title} | {refs} | {status} | {note} |')
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{OUT}: {dict(counts)}')


if __name__ == '__main__':
    main()
