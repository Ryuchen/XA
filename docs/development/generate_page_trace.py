"""Generate page-to-work-package mapping from the PRD's historic page matrix."""
from pathlib import Path
import re
from generate_trace import DOCS, numbers

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'docs/prd/08-页面接口追踪.md'
OUTPUT = Path(__file__).with_name('15-三端页面开发追踪.md')
MAP = {
 'B': {
  'E01': '1-4,10-12,20-22,33-35',
  'E02': '2,5-9,13,19,24,25,28,30-32',
  'E03': '9,13,15,16,18,23,28,30,32',
  'E04': '4,9-15,17-19,27',
  'E05': '14-16,23,31',
  'E06': '2,5-9,16,24-32',
  'E07': '3,15,20,33-35',
  'E08': '9,15',
  'E09': '14,15',
 },
 'P': {
  'E01': '1,2,11-13,16-20',
  'E02': '2,10,12,13,15',
  'E03': '8,10,15',
  'E04': '2-7,10-14,19,20',
  'E05': '2,3,7-11,14',
  'E06': '10,13-15',
  'E07': '16-18',
  'E09': '3,4,5,7,8,10',
 },
 'A': {
  'E01': '1-3,14,16,18,46-48',
  'E02': '3,15,17,19-29,32-38,44,45,49,50',
  'E03': '6,9-12,15,20-24,28,29,33,44,55',
  'E04': '5-8,16-18,20-22,41-43,45',
  'E05': '6-10,12,13,16,18,19,41',
  'E06': '14,15,20-37,41,49',
  'E07': '4-6,9,11-13,30,31,38-40,45-55',
  'E08': '6,21,22',
  'E09': '6-9,12,16,18,20,48',
 },
}


def main():
    pages = {'B': [], 'P': [], 'A': []}
    for line in SOURCE.read_text(encoding='utf-8').splitlines():
        m = re.match(r'\|\s*([BPA]\d+)\s+([^|]+)\|', line)
        if m:
            ident, route = m.groups()
            pages[ident[0]].append((ident, route.strip()))
    expected = {'B': 35, 'P': 20, 'A': 55}
    sections = {'B': '老板端', 'P': '陪玩端', 'A': '管理后台'}
    output = [
        '# 15 三端页面开发追踪', '',
        '本表覆盖原页面矩阵中的老板 35 项、陪玩 20 项、后台 55 项（后台包含同页功能分栏），逐项给出实施工作包。路由和分栏取自 [PRD 页面清单](../prd/08-页面接口追踪.md)，不代表所有页面或按钮已接通。', '',
        '原清单个别动作和示例是历史记录，尤其是老板／陪玩切店、后台代陪玩确认预约、通行证提前续费、旧登录方式和旧售后规则；实施必须按 [最新产品决策](../prd/20-产品决策逐项复核.md)、[三端模块契约](13-三端模块与事件契约.md) 及各工作包覆盖。所有项均须通过 [E10](10-测试发布与回滚.md)；映射不等于完成。', '',
        '本表由 [generate_page_trace.py](generate_page_trace.py) 与 [generate_trace.py](generate_trace.py) 中人工维护的编号归属生成，生成时检查全部编号是否覆盖。', '',
    ]
    for kind in ('B', 'P', 'A'):
        ids = [int(i[1:]) for i, _ in pages[kind]]
        if sorted(ids) != list(range(1, expected[kind] + 1)):
            raise ValueError(f'{kind}: duplicate or missing page IDs: {ids}')
        index = {key: numbers(value) for key, value in MAP[kind].items()}
        for owner, indices in index.items():
            extra = indices - set(ids)
            if extra:
                raise ValueError(f'{kind}/{owner}: unknown IDs {sorted(extra)}')
        output += [f'## {sections[kind]}（{len(ids)} 项）', '', '| 页面 ID | 原有路由／功能分栏 | 开发工作包 |', '| --- | --- | --- |']
        for ident, route in pages[kind]:
            n = int(ident[1:])
            owners = [key for key in sorted(index) if n in index[key]]
            if not owners:
                raise ValueError(f'{ident}: no owner')
            refs = '、'.join(f'[{key}]({DOCS[key]})' for key in owners)
            output.append(f'| [{ident}](../prd/08-页面接口追踪.md) | {route} | {refs} |')
        output.append('')
    OUTPUT.write_text('\n'.join(output), encoding='utf-8')
    print('Generated', OUTPUT, '; page entries:', sum(map(len, pages.values())))


if __name__ == '__main__':
    main()
