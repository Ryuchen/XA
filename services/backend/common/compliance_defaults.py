"""Immutable text-compliance rules shipped as version 1.

Keep this snapshot stable: receipts and published rule versions refer to its
SHA-256 digest. New policy belongs in a new ComplianceRuleVersion.
"""

SERVICE_TEXT_FIELDS = (
    'SERVICE_RULES',
    'SERVICE_DETAILS',
    'GAMEPLAY_RULES',
    'GAMEPLAY_DETAILS',
)

PROVIDER_NAME_FIELDS = (
    'PROVIDER_DISPLAY_NAME',
    'PROVIDER_REGISTRATION_DISPLAY_NAME',
)

DEFAULT_RULES_V1 = [
    {
        'code': f'INTERPRETATION_{index:03d}',
        'category': 'INTERPRETATION_CLAUSE',
        'applies_to': list(SERVICE_TEXT_FIELDS),
        'terms': list(terms),
        'message': '价目表和服务规则不得保留单方解释权或单方变更权',
        'enabled': True,
    }
    for index, terms in enumerate((
        ('最终解释权归本店', '最终解释权归本俱乐部'),
        ('解释权归本店', '解释权归本俱乐部'),
        ('本公司保留最终解释权',),
        ('本平台保留最终解释权',),
        ('本店拥有最终解释权', '本店保留最终解释权'),
        ('本店保留一切解释权',),
        ('门店享有最终解释权',),
        ('活动最终解释权归本店',),
        ('活动解释权归本店',),
        ('优惠活动最终解释权归本店',),
        ('本店有权随时调整活动规则',),
        ('本店可单方面变更服务内容',),
        ('本店可单方解释本次服务',),
        ('以本店解释为准',),
        ('以本店最终说明为准',),
        ('本店保留修改条款的权利',),
        ('本店可随时更改或终止服务',),
        ('条款最终以本店审核为准',),
        ('本店对本活动拥有最终决定权',),
        ('本店保留一切最终决定权',),
        ('本店可单方面作出最终裁定',),
    ), start=1)
] + [
    {
        'code': 'EXTREME_WORD_COMMON',
        'category': 'EXTREME_WORD',
        'applies_to': [
            'STORE_NAME', 'STORE_INTRODUCTION',
            *SERVICE_TEXT_FIELDS,
            *PROVIDER_NAME_FIELDS,
        ],
        'terms': [
            '第一', '第一名', '最好', '最佳', '最强', '最高', '最低', '顶级', '极致',
            '极品', '绝对', '百分之百', '全网最低', '全国第一', '世界第一',
            '独一无二', '无可比拟', '史无前例', '最后机会', 'no1',
        ],
        'message': '内容不得使用极限词或无法核实的绝对化宣传',
        'enabled': True,
    },
    {
        'code': 'NICKNAME_RESERVED_ROLES',
        'category': 'NICKNAME_RESERVED',
        'applies_to': list(PROVIDER_NAME_FIELDS),
        'terms': [
            '接待', '客服', '售后', '官方', '平台', '店长', '管理员', '工作人员',
            '老板', '运营', '审核员', '仲裁',
        ],
        'message': '接待、客服、售后等职能昵称仅限授权角色使用',
        'enabled': True,
    },
]
