import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = await readFile(path.join(root, 'src/components/store/store-page-meta.ts'), 'utf8')

function entries(section) {
  const body = source.split(`const ${section}: Record<string, StorePageMeta> = {`)[1]?.split('\n}')[0]
  if (!body) throw new Error(`Missing section: ${section}`)
  return [...body.matchAll(/^  (?:'([^']+)'|([\w-]+)): \{ scope: '([^']*)', design: '([^']*)', guard: '([^']*)' \},?$/gm)].map(match => ({ key: match[1] || match[2], scope: match[3], design: match[4], guard: match[5] }))
}

const groups = [
  { title: '老板端（42 页）', entries: entries('bossPages'), route: key => `/${key}` },
  { title: '陪玩端（15 页）', entries: entries('providerPages'), route: key => key },
  { title: '管理后台与平台层（34 页）', entries: entries('adminPages'), route: key => `/admin-${key}` },
]
if (groups.some(group => !group.entries.length) || groups.reduce((sum, group) => sum + group.entries.length, 0) !== 91) throw new Error('Expected exactly 91 documented pages')

const intro = `# 三端门店租户概念设计

> 本文是声浪电竞三端高保真原型的设计补充与后续生产联调约束，不代表已经实现真实数据库、鉴权或资金隔离。原型中的门店、账号、订单与金额均为示例数据。

## 1. 租户模型

- **一家门店＝一个独立业务租户。** 每店拥有稳定且唯一的门店标识、独立老板端、独立陪玩端、独立店内管理后台和独立业务数据库。
- 老板、陪玩、订单、预约、履约、会话、钱包、流水、优惠、积分、会员、运营配置、人员薪资、投流及经营核算全部限定在单店，不跨店查询、扣减、转移或合并。
- 同一微信或同一自然人在不同门店可以拥有独立店内账号关系；平台全局身份只作为授权来源，不能把一店资料、资产或权限自动带入另一店。
- 平台门店管理只维护门店元数据、三端入口、业务库初始化与启停状态。平台授权与店内角色分离，平台层不直接展示各店客户、订单、资金或薪资明细。
- 原默认店业务数据保留在默认店。新增门店从空白业务库开始，不复制原店客户、订单、钱包、接收人或经营数据；公共字典仅可显式引用，不共享运行时数据。

## 2. 入口与认证

原型通过 \`?store=<门店标识>\` 表达独立入口。内置可预览门店为 \`shenglang\`、\`xingchao\`、\`wudao\`。

| 端口 | 独立入口 | 认证与账号边界 |
|---|---|---|
| 老板端 | \`?store=xingchao#/login\` | 微信扫码／快捷登录、资料确认、手机号短信验证与首次 OTP 绑定均在当前店上下文完成；门店参数不是授权凭证。 |
| 陪玩端 | \`?store=xingchao#/provider-login\` | 仅使用微信快捷登录／入驻，与老板端认证方式一致；登录绑定微信、完成微信取号与短信验证后进行首次 OTP 绑定。入驻与审核查询只验证微信和手机号，不强制 OTP。绑定、申请与审核记录不跨店复用。 |
| 管理后台 | \`?store=xingchao#/admin-login\` | 进入或切入门店后使用该店授权账号重新登录，再校验店内角色。 |
| 平台门店管理 | \`#/admin-stores\` | 独立的平台级授权，只管理元数据与生命周期，不替代店内登录。 |

无效、未初始化或已停用门店不能借助修改地址参数进入营业页面；真实环境必须由服务端解析可信门店并鉴权，前端参数仅用于入口识别。

## 3. 全链路隔离约束

| 层次 | 设计约束 |
|---|---|
| 数据库 | 每店独立业务库、连接、迁移版本与备份恢复边界；业务查询不接受客户端任意指定跨店连接。 |
| 令牌与权限 | 令牌包含可信门店上下文；平台令牌、店内后台令牌、老板与陪玩会话分别鉴权。切店后旧店会话不能访问新店。 |
| 缓存 | 缓存键包含门店命名空间；切店清理内存中的旧店详情、查询结果和表单状态，禁止以共享键读取他店数据。 |
| 文件 | 凭证、试音、交付资料及导出文件按店分路径和访问签名；文件下载同时校验门店与业务权限。 |
| 实时消息 | 会话、频道、订阅、未读数与推送接收人按店隔离；切店关闭旧店连接，不在新店重放旧店草稿。 |
| 异步任务 | 播报、通知、导出、结算和重试任务保存并校验原门店；失败重试不得改投当前门店。 |
| 资金与核算 | 充值、支付、退款、礼物、提现、调账、薪资、投流及经营 ROI 均限定同店；禁止跨店抵扣、合并支付或调账。 |
| 审计与异常 | 订单号、文件、消息深链与当前店不一致时统一拒绝；不泄露目标是否存在，并记录安全审计。 |

## 4. 切店与页面状态

1. 三端在固定位置提供门店切换，确认框说明目标入口、未提交草稿处理和重新鉴权要求。
2. 老板端切店回目标店首页（独立入口可先到登录页）；陪玩公开登录／注册页切店留在对应账号入口并清空未提交资料，已登录工作台切店回目标店工作台；管理后台回目标店登录页重新鉴权。
3. 切店采用完整页面导航销毁旧店页面状态；不携带旧店选品、预约、确认单、详情、弹窗、草稿、分页、筛选和操作结果。
4. 每页右侧设计面板展示当前门店、数据范围、独立业务库别名、补充设计与异常边界，页面内不重复显示范围说明条。
5. 原型仅演示状态清理和独立示例数据，不执行真实令牌撤销、数据库切换或资金操作。

## 5. 逐页设计矩阵

`

const tables = groups.map(group => `### ${group.title}\n\n| 页面 | 路由 | 本页数据范围 | 概念设计补充 | 隔离与异常反馈 |\n|---|---|---|---|---|\n${group.entries.map(item => `| ${item.key} | \`${group.route(item.key)}\` | ${item.scope} | ${item.design} | ${item.guard} |`).join('\n')}\n`).join('\n')

const outro = `
## 6. 原型覆盖与验证

- 门店示例：声浪电竞·星海店、星潮电竞·湖滨店、雾岛电竞·云城店；三店分别展示独立老板身份、陪玩身份、钱包余额和后台经营快照。
- 平台门店管理：新增空白门店、初始化、复制三端入口、启停确认及平台／店内权限边界。
- 预览组织：三端统一采用外层原型导航、iframe 页面预览和右侧场景／门店设计面板；后台登录与业务页均纳入该结构。
- 响应式：老板端和陪玩端覆盖手机与桌面；管理后台支持 PC 与 1366 × 1024 iPad Pro 横屏切换，保留店内业务侧栏和平板触控空间。
- 自动化验证：\`node scripts/verify-store-prototype.mjs\`，结果和截图位于 \`verification/\`。脚本启动的临时服务会在结束时关闭。

## 7. 生产落地前置条件

门店标识必须由服务端可信解析；每店数据库、连接池、迁移、缓存、文件、消息、任务、支付商户配置与审计需要逐项实现和验证。不得以本原型通过、前端隐藏按钮或地址栏 \`store\` 参数作为生产级数据隔离已完成的证据。
`

await writeFile(path.join(root, 'STORE-DESIGN.md'), intro + tables + outro, 'utf8')
console.log(`Wrote STORE-DESIGN.md with ${groups.reduce((sum, group) => sum + group.entries.length, 0)} page entries`)
