"""Generate PRD-to-engineering handoff index; mappings are curated, not test results."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name('11-需求与验收追踪.md')
SOURCES = {
    'D': ROOT / 'docs/prd/20-产品决策逐项复核.md',
    'G': ROOT / 'docs/prd/07-差距待决与验收.md',
    'T': ROOT / 'docs/prd/07-差距待决与验收.md',
}
DOCS = {
    'E01': '01-架构权限与多租户.md',
    'E02': '06-配置会员活动与运营.md',
    'E03': '03-定价钱包与积分.md',
    'E04': '04-抢单预约与履约.md',
    'E05': '05-售后结算押金与提现.md',
    'E06': '06-配置会员活动与运营.md',
    'E07': '07-薪资客服与通知.md',
    'E08': '08-房间加购与历史切换.md',
    'E09': '08-房间加购与历史切换.md',
    'E10': '10-测试发布与回滚.md',
}


def numbers(expression):
    result = set()
    for part in expression.split(','):
        part = part.strip()
        if '-' in part:
            first, last = map(int, part.split('-', 1))
            result.update(range(first, last + 1))
        elif part:
            result.add(int(part))
    return result


# Curated primary and cross-domain owners. Each item must have at least one
# owner; coverage is checked below and never equated with implementation.
MAP = {
 'D': {
  'E01': '1-11,35,48,49,52,53,185,186,188-190',
  'E02': '6,7,13,14,19,22,24,30,38,39,62,64-66,81,89,91,104,113,128,129,139,145,146,166-169,202',
  'E03': '12-18,24-28,39,41-43,51-55,60,61,95,96,99-101,105,111-117,119,127,130,132,133,137,143,147,148,155,163-165,171,172,174-180,185-197,200',
  'E04': '10,11,23,36-40,44,45,51,62,64,66,79-82,84-86,89-94,97,98,102-104,110,118,120-125,128,129,137,144,152,156,159-162,170-184,200,201,202',
  'E05': '19-23,40-43,55-57,61,77,78,83,87,88,92,97,98,101,105-112,114,126,132,149-155,163,164,174-181,185-197,200',
  'E06': '23-35,61,63,65,70-76,95,99,104,113,115-117,119,124,125,127-129,165-169,198,199,201,202',
  'E07': '14,21,35,46-50,53,54,58-60,67-69,83,95,96,100,121-123,126,133,137,185-190',
  'E08': '128-148,157,158,202',
  'E09': '92,149-158,160,169,170',
 },
 'G': {
  'E01': '1-3,7,8,13,16-18,20-22,29,32,33,51,54,65',
  'E02': '3-5,10,14,18,23-29,38-40,47,48,55-57,59,62,63,65-71,73,75,84,99-102,107,110-113',
  'E03': '4-6,11,19,23,24,26,30,37,39-42,44,46,49,50,53,54,58,61,69,75,86,87,90,97,99-101,109,110,113,115,118-120',
  'E04': '7-11,15,23,28,36,44,46,47,55,57-59,65,69,71,75,76,78,81,84,85,88,89,96,102-105,107,108,111,115,117,120',
  'E05': '6,10-13,15,21-23,40-45,48,49,52,53,60,64,68,71-74,76,77,81-83,87-94,95,97,98,106,109,116,118-120',
  'E06': '4,5,19,23-26,29,33-35,37,38,47,53,56,58,62-64,66-70,87,99-101,105,107,110,112-114',
  'E07': '12-14,16,21,27,31,32,38,46,50-52,54,60,61,64,77,79,80,82-83,86,104,106',
  'E08': '107-115',
  'E09': '1,8,10,16,18,21-23,28,40,43,45,58,65,81,95,116',
 },
 'T': {
  'E01': '1-4,10,23-25,28,31,32,38,48,56,65,70,77,80,91,98,117,118,128,178-180,184-186,194,210,215',
  'E02': '4,6,10,14,21,22,28,31,32,38,39,41-44,55,57,63,65,68,70-72,78,83,84,88,89,91-93,101-104,106,108,110,112,114,119-121,123-126,130-134,140,142,151,152,156,162,163,168,170-172,175,176,187-189,191,193,199,201,203-207,211,212',
  'E03': '5-9,14,16-18,26,29,30,33-40,43,44,55,56,58-62,64,67,69,73-76,80,82,97,104,112,115,123,135,138,139,144-149,153-155,159,166-172,174,176,183,190-193,197,201,203-207,208,213,220-228',
  'E04': '10-13,15,17,18,33,34,44,52,65,70,74,81,83,84,87,88,90,91,104,109,110,112,113,116,119-127,130-134,136,137,140-143,147,150,151,158,161,163-165,173,175,177-182,185-189,194,197,202,210,214,216-219,222-228',
  'E05': '8,13-20,26,28,29,33-37,54,58-60,64-69,71,72,75,78,81,82,85-87,104,107,108,111,114-116,122,127-129,142-145,148,149,152-167,169,174,181,184,190,192,197,202,208,209,211-213,215,220-228',
  'E06': '5-7,11,15,23,27,30,32,36,38-43,49-51,53,57,61,63,64,69,70,74,76,88-94,101-106,135,144-146,168,170-172,174,176,181-183,187,188,190,191,193,199,201,203-207',
  'E07': '2,3,14,19-23,28,31,32,38,45-48,54,55,64,67,69,72,73,76,77,79,85,86,90,94-100,111,117,118,128,129,139,144,145,178-180,184,185,192,193,195,196,200,213,215',
  'E08': '190-207,215',
  'E09': '10,14,25,28,31-34,52,55-60,63-69,81,83,127,150,161,186,202,208-215,217',
 },
}

# Suffix decisions are independent requirements, not aliases of D30.
SUPPLEMENTAL_D = {
    'D30A': ('E03', 'E05', 'E06'),
    'D30B': ('E03', 'E04', 'E05'),
    'D30C': ('E02', 'E05'),
}


def rows(kind):
    found = []
    for line in SOURCES[kind].read_text(encoding='utf-8').splitlines():
        suffix = r'[A-Z]?' if kind == 'D' else ''
        match = re.match(r'\|\s*(' + kind + r'\d+' + suffix + r')\s*\|\s*([^|]+)', line)
        if not match:
            continue
        ident, title = match.groups()
        title = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', title).strip()
        found.append((ident, title))
    return found


def main():
    parts = [
        '# 11 需求与验收追踪',
        '',
        '> **2026-11-11 范围变更：** 用户确认线上旧数据全部清除、系统全新部署；历史业务数据迁移／旧订单接管条目仅供追溯，不再作为本次交付验收。E09 改为空库部署、两店及平台初始化验收；新系统产生的订单、资金、权限和审计仍需完整验证。',
        '',
        '本表逐项摘录 PRD 的决策 D、差距 G 和验收 T，标出对应研发工作包及设计文档。一个事项可跨多个工作包；表内映射是设计追踪，不代表编码、测试或上线已完成。需求正文与预期仍以 PRD 为准。',
        '',
        '工作包定义见 [09](09-接口契约与工作包.md#4-工作包与依赖)，模型与迁移见 [02](02-数据模型与迁移.md)；所有 T 均还需经过 [E10 测试与发布](10-测试发布与回滚.md)。本表由 [generate_trace.py](generate_trace.py) 使用人工维护的编号映射生成，并检查缺号、重复号及未指派工作包。',
        '',
    ]
    expected = {'D': 202, 'G': 120, 'T': 228}
    labels = {'D': '产品决策', 'G': '现状差距', 'T': '验收场景'}
    for kind in ('D', 'G', 'T'):
        entries = rows(kind)
        identifiers = [i for i, _ in entries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(f'{kind}: duplicate PRD IDs')
        ids = [int(i[1:]) for i in identifiers if i[1:].isdigit()]
        if sorted(ids) != list(range(1, expected[kind] + 1)):
            raise ValueError(f'{kind}: missing, duplicate, or new PRD IDs; update mapping')
        if kind == 'D' and {i for i in identifiers if not i[1:].isdigit()} != set(SUPPLEMENTAL_D):
            raise ValueError('D: missing or unassigned suffix decisions')
        mapped = {key: numbers(expr) for key, expr in MAP[kind].items()}
        for owner, indices in mapped.items():
            unexpected = indices - set(ids)
            if unexpected:
                raise ValueError(f'{kind}/{owner}: unknown PRD IDs {sorted(unexpected)}')
        parts.extend([f'## {labels[kind]}（{len(entries)} 项）', '', '| 编号 | 需求或场景 | 研发工作包与方案 |', '| --- | --- | --- |'])
        for ident, title in entries:
            owners = (list(SUPPLEMENTAL_D[ident]) if ident in SUPPLEMENTAL_D else
                      [key for key in sorted(mapped) if int(ident[1:]) in mapped[key]])
            if not owners:
                raise ValueError(f'{ident} has no curated engineering owner')
            refs = '、'.join(f'[{owner}]({DOCS[owner]})' for owner in owners)
            source = '../prd/' + SOURCES[kind].name
            parts.append(f'| [{ident}]({source}) | {title} | {refs} |')
        parts.append('')
    OUT.write_text('\n'.join(parts), encoding='utf-8')
    print('Generated:', OUT)
    print('Mapped numeric ID coverage:', ', '.join(f'{kind}={expected[kind]}' for kind in expected),
          f'; D suffix={len(SUPPLEMENTAL_D)}')


if __name__ == '__main__':
    main()
