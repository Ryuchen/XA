"""Generate a source-linked review aid, not a claim of complete coverage."""
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/ops/mobile-page-prototype-inventory-20261103.md'
LEGACY = {'gameplay':'catalog','category':'catalog','productDetail':'service','serviceCard':'support','orderList':'orders','messageDetail':'message-detail','players':'companions','customer/wallet':'wallet','customer/checkin':'checkin','announcement':'announcements','announcementDetail':'announcement-detail','coupon':'coupons','coupon/mine':'coupons','favorite':'favorites','settings':'profile'}

def unique(items):
    return list(dict.fromkeys(x.strip() for x in items if x and x.strip()))

def clean(value):
    return re.sub(r'\s+', ' ', html.unescape(value)).strip()

def fmt(items):
    values = unique(items)
    return '；'.join('`' + x.replace('`', '′') + '`' for x in values) if values else '无静态提取项，待人工核对'

class Extract(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts, self.attrs, self.events, self.conditions, self.classes, self.components, self.fields = ([] for _ in range(7))

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if '-' in tag or tag in ('dialog', 'dialogtitle', 'dialogcontent', 'alert', 'skeleton', 'card', 'button', 'input', 'textarea', 'select', 'picker', 'image', 'img'):
            self.components.append(tag)
        for key, value in attrs:
            if value is None:
                continue
            value = clean(value)
            if key == 'class':
                self.classes.extend(re.findall(r'[A-Za-z_][\w-]*', re.sub(r'\{\{.*?\}\}', '', value)))
            if key in ('title', 'placeholder', 'aria-label', 'label', 'empty-text', 'error-text') or key.startswith(':placeholder'):
                self.attrs.append(key + '=' + value)
            if key.startswith('v-model') or key in ('value', ':value', 'name', 'data-id', 'data-key', 'data-value'):
                self.fields.append(tag + ' ' + key + '=' + value)
            if key.startswith(('bind', 'catch', '@', 'v-on:')):
                self.events.append(key + '=' + value)
            if key in ('wx:if', 'wx:elif', 'wx:else', 'v-if', 'v-else-if', 'v-else', 'v-show'):
                self.conditions.append(key + '=' + value)

    def handle_data(self, data):
        value = clean(re.sub(r'\{\{.*?\}\}', ' ', data, flags=re.S))
        if value and re.search('[\u4e00-\u9fff]', value):
            self.texts.append(value)

def section(title, link, markup, logic, figma):
    parser = Extract()
    parser.feed(markup)
    expressions = unique(clean(x) for x in re.findall(r'\{\{(.*?)\}\}', markup, re.S))
    endpoints = unique(clean(x) for x in re.findall(r'''(?:\brequest|\bapi(?:<[^>]+>)?|\bfundRequest)\s*\(\s*[`'"]([^`'"]+)''', logic))
    lower = (markup + '\n' + logic).lower()
    markers = [x for x in ('loading','skeleton','state-view','empty','error','retry','pending','conflict','disabled','busy','permission','aria-') if x in lower]
    return '\n'.join([
        f'### {title}', f'- 源码：{link}',
        f'- 可见中文静态文本：{fmt(parser.texts)}',
        f'- 标签、提示及无障碍属性：{fmt(parser.attrs)}',
        f'- 页面动作绑定：{fmt(parser.events)}',
        f'- 模板表单及选择绑定：{fmt(parser.fields)}',
        f'- 页面代码静态接口线索：{fmt(endpoints)}；接口由共享 helper 间接构造时可能漏项，完整路径与权限须逐项核对。',
        f'- 条件分支：{fmt(parser.conditions)}',
        f'- 数据插值表达式：{fmt(expressions)}',
        f'- 组件及现有布局 class：组件 {fmt(parser.components)}；布局 class {fmt(parser.classes)}',
        f'- 状态源码标记：{fmt(markers)}。这只是证据线索，不能据此推断骨架、空态或业务权限已实现。',
        f'- Figma：{figma}；须逐一核对所有列表、Tab、弹窗的有数据、加载骨架、真实空态（适用时）、失败及权限状态。', ''
    ])

def main():
    pages = json.loads((ROOT / 'apps/boss-miniapp/native/app.json').read_text(encoding='utf8'))['pages']
    real, compat = [], []
    for path in pages:
        key = path.removeprefix('pages/').removesuffix('/index')
        (compat if key in LEGACY else real).append((path, key))
    main_ts = (ROOT / 'apps/provider-vue/src/main.ts').read_text(encoding='utf8')
    imports = dict(re.findall(r"import\s+(\w+)\s+from\s+'(\./views/[^']+)'", main_ts))
    routes = re.findall(r"\{\s*path:\s*'([^']+)'[^\n]*?component:\s*(\w+)[^\n]*\}", main_ts)
    lines = [
        '# 老板小程序与打手端：原型逐页源码清单（工作底稿）', '',
        '目标：忠实迁移现有视觉与真实业务，不从零重设计。用户要求每个实际页面分别设计有数据、加载骨架和适用时的真实空态；登录、表单、详情等不机械捏造列表空态。页面内 Tab、弹窗、输入、动作、错误与权限分支须逐项设计。', '',
        '**状态：机器辅助静态提取，尚未逐项人工确认，不是完整功能规格、Figma 完成或测试通过证明。** 模板中的变量和分支须结合 JS / Vue、共享组件与运行时数据核实。提取器不能充分覆盖 JS 动态 toast / modal、共享组件内部、运行时接口字段、表达式中的特殊标签与渲染条件；必须逐页人工检查补充。', '',
        f'老板 `app.json` 注册 {len(pages)} 条：静态归并 {len(real)} 个业务页、{len(compat)} 个兼容入口；打手路由含 {len(routes)} 个组件路由，别名和重定向另列。此计数不是 Figma 覆盖率。', '',
        '现有页面布局、组件及业务信息为设计基线；本文件记录模板组件和布局 class 用于定位，不代表视觉依赖已完成核验。用户历史聊天/评价原文照常显示，上传凭证保留原件；金额名称改“浪花”，原有换算及余额不变。', '',
        '指定的 [shadcn Figma 套件副本](https://www.figma.com/design/XfTilzt45l5zVHapLMSEIc)；已有[三端代表画板](https://www.figma.com/design/XfTilzt45l5zVHapLMSEIc?node-id=4005-2)。老板订单和打手钱包已有代表性三态，不代表所有 Tab/弹窗均已设计。订单及资金页的人工细化见[订单状态](../../design-demos/shenglang-rebrand-review-20261103/order-state-detail.md)和[资金状态](../../design-demos/shenglang-rebrand-review-20261103/money-state-detail.md)。', '',
        '## 一、老板微信小程序', ''
    ]
    for path, key in real:
        base = ROOT / 'apps/boss-miniapp/native' / path
        wxml, js = Path(str(base) + '.wxml'), Path(str(base) + '.js')
        markup = wxml.read_text(encoding='utf8') if wxml.exists() else ''
        logic = js.read_text(encoding='utf8') if js.exists() else ''
        link = f'[{path}.wxml](../../apps/boss-miniapp/native/{path}.wxml) · [页面逻辑](../../apps/boss-miniapp/native/{path}.js)'
        status = '订单列表已有代表性三态；节点逐态映射及完整状态待核' if key == 'orders' else '待建 / 待映射'
        lines.append(section(path, link, markup, logic, status))
    lines += ['## 二、老板端兼容入口', '', '| 入口 | 默认目标 | 上下文 |', '| --- | --- | --- |']
    for path, key in compat:
        note = {'orderList':'有订单 ID 时转订单详情','players':'有服务 ID 时转陪玩选择','coupon/mine':'携带 mine 标签','gameplay':'保留玩法 / 平台 / 等级意图','category':'保留玩法 / 平台 / 等级意图'}.get(key, '保留 ID 和适用上下文')
        lines.append(f'| `{path}` | `pages/{LEGACY[key]}/index` | {note} |')
    lines += ['', '依据 [legacy-route.js](../../apps/boss-miniapp/native/core/legacy-route.js)；实际参数、跳转失败及返回上下文仍须核对。', '', '## 三、打手 H5 / Web', '', '根 `/` 重定向到 `/orders`；`/pages/audition/index` 为 `/audition` 别名；动态参数复用对应页面。守卫见 [main.ts](../../apps/provider-vue/src/main.ts)。', '']
    for route, component in routes:
        path = ROOT / 'apps/provider-vue/src' / imports.get(component, '').removeprefix('./')
        if not path.is_file():
            lines.append(f'### `{route}`\n- 无法定位 `{component}`，待核查。\n')
            continue
        source = path.read_text(encoding='utf8')
        markup = re.sub(r'<script\b[^>]*>.*?</script>', '', source, flags=re.S | re.I)
        markup = re.sub(r'<style\b[^>]*>.*?</style>', '', markup, flags=re.S | re.I)
        rel = path.relative_to(ROOT).as_posix()
        status = '钱包已有代表性三态；节点逐态映射及各 Tab / 弹窗待核' if route == '/wallet' else '待建 / 待映射'
        lines.append(section(f'`{route}` · {component}', f'[{component}.vue](../../{rel})', markup, source, status))
    lines += ['## 四、后续核对与评审', '', '- 人工逐页核对提取值、共享组件、动态文案、API 返回字段、权限、全部 Tab 和弹窗；对不适用的骨架/空态写明原因。', '- 以当前页面为基线，逐页映射 Figma 实例、布局和状态；创建及核验前均保留待建。', '- 老板端模拟器验收/截图前最大化微信开发者工具并确认 iPhone 12/13 (Pro)、390 × 844 和完整屏幕；打手 H5 / Web 均须核对。', '- 七角色对同版 PRD 与原型评审；未全部确认不进入本轮开发。业务验证使用隔离 WSL 打包容器、合成数据与真实业务代码。', '']
    OUT.write_text('\n'.join(lines), encoding='utf8')
    print(f'Wrote {OUT}; boss={len(real)}, compatibility={len(compat)}, provider={len(routes)}, bytes={OUT.stat().st_size}')

if __name__ == '__main__':
    main()
