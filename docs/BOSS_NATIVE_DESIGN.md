# 老板端原生小程序 · 柔和社交视觉改版

本次范围为 `apps/boss-miniapp/native` 的整个老板端：24 个业务页、5 个原生 Tab、公共加载/空/错误状态及微信原生控件的强调色。16 个历史路由继续跳转至对应原生页面。

用户确认方向：浅色底、粉色强调、更亲切。使用 design-taste-frontend 的审计与视觉原则，保留原生 WXML、WXSS、JS、微信授权组件及已接通的业务接口。设计参数为视觉变化 6 / 动效 3 / 信息密度 5。

## 预览

- [独立打开的 24 页布局预览](design/boss-native/preview.html)：内嵌全部图片，可离线打开，各画框可单独滚动。
- [五个主入口](design/boss-native/main-tabs.png)
- [全部业务页总览](design/boss-native/all-pages.png)

预览来自实际 WXML/WXSS 的浏览器转译，使用明确的演示数据，不连接业务后台。此次已加入项目对应的微信基础库 3.15.2 默认按钮与输入框样式，核对原生样式优先级。图片为空时展示语义图标；线上仍优先使用后台配置的封面、头像、轮播和二维码。预览不模拟微信授权、支付、音频播放、原生 picker、键盘与安全区。

## 原界面问题与布局调整

原页面混用了绿色、深绿色、灰色和多套覆盖样式；大标题、双列快捷卡片与完整筛选区挤占首屏。卡片标题外边距不一致，部分横向按钮继承 `width: 100%`，长名称和金额缺少明确的收缩规则。

改版统一 32rpx 页面边距、28rpx 卡片内边距及 20rpx 卡片间距；采用 42rpx 页标题、30/31rpx 分区标题、28rpx 正文和较小的辅助信息。大多数主按钮高度至少 88rpx，圆角 22rpx；卡片圆角 28rpx。信息列表以行、分隔线和轻背景区分层次，避免每条流水都叠一张卡片。

| 页面 | 布局重点 |
| --- | --- |
| 首页 | 品牌行、带插画的引导区、四个单行快捷入口、公告条、服务进度、双列玩法、服务列表 |
| 服务 | 搜索前置；玩法横向滚动；游戏/平台/难度折叠至更多筛选；封面与文字固定比例 |
| 订单 | 状态标签横向滚动；编号、服务名称、时间、金额及详情入口分层 |
| 消息 | 两个明确的客服入口；横向通知类型；未读圆点；一致的通知摘要间距 |
| 我的 | 头像与昵称、安全状态、余额、三项订单统计、四项权益、设置列表 |
| 登录 | 轻量插画、账号识别说明、可换行的隐私同意项、绑定与新建账号分支 |
| 服务详情 | 封面与无图状态、价格与标签、说明/规则/流程/评价；收藏固定宽度，两项主操作均分 |
| 确认订单 | 服务摘要、数量和陪玩、游戏信息、报价分组；固定支付区与底部安全间距 |
| 订单详情 | 状态说明、信息键值行、时间轴、评价与赠礼；长游戏昵称/UID 可换行 |
| 挑选陪玩 | 圆形头像、昵称及认证、试听按钮、三列记录；“选 TA”固定宽度 |
| 钱包 | 浅粉余额区；流水采用分隔行；金额和时间对齐 |
| 个人资料 | 头像编辑行、昵称、手机号、常用游戏、游戏字段沿用原顺序 |
| 账号安全 | 微信和手机号两组状态卡；说明、授权和客服操作层级一致 |
| 成就 | 名称与状态、说明、当前/目标进度分层 |
| 榜单 | 三段周期切换；名次、姓名及统计量对齐；长昵称换行 |
| 通知详情 | 标题、日期、保留换行的正文与关联操作 |
| 公告列表 | 日期/置顶状态在前，标题与阅读入口对齐 |
| 公告详情 | 适合阅读的行距、正文内边距及长文本换行 |
| 优惠券 | 分段导航；金额和使用条件分栏；大额券减小字号并扩大金额栏 |
| 收藏 | 图片、名称、价格与取消收藏分层；避免操作挤压名称 |
| 签到 | 三列汇总、真实消费进度、七列日历、横向奖励列表 |
| 试音邀请 | 活动说明、有效期、账号和身份确认操作分层 |
| 客服与帮助 | 客服名片、咨询编号、联系操作与二维码，长微信号可换行 |
| 平台会话 | 消息时间、左右气泡、固定输入区；图片随气泡宽度约束 |

## 色彩与状态

| 用途 | 值 |
| --- | --- |
| 主色 / 操作 | `#b83d68` |
| 浅粉强调背景 | `#fce8ef` |
| 页面背景 | `#faf7f9` |
| 内容背景 | `#ffffff` |
| 主要文字 | `#30262c` |
| 辅助文字 | `#796b74` |
| 边框 | `#efe5eb` |
| 错误 | `#b42335` |

服务进行中保留轻绿色状态提示，并附文字；已完成/取消使用中性色。加载骨架不闪烁，空状态给出说明，错误状态可重试。Tab 图标、checkbox、slider、switch 和 swiper 指示点同步强调色。

## 微信原生样式校准

首轮浏览器预览遗漏了 `style: v2` 的默认控件规则，因此没有暴露原生按钮冲突。已从本机项目对应的官方基础库 3.15.2 中确认并复现：`wx-button:not([size=mini])` 会设置 184px 宽度和自动水平外边距，`wx-button[type=default]` 会设置灰底与绿色文字；其优先级高于单独的 `.button`、`.chip` 等类选择器。

现已为所有页面按钮增加 `xa-button` 样式标识，用明确的组合类选择器处理宽度、颜色、布局、选中及禁用状态。按压使用自定义 `hover-class`，避免再次触发微信默认绿色背景。保留 `style: v2`，没有使用全局 `!important`。错误重试按钮在组件自己的隔离样式中处理。

输入框显式声明高度和继承字体；客服输入区保持自动高度。预览同步展示页面 JSON 中实际配置的导航标题，并校准自动高度输入区。旧的预览截图已替换为加入微信默认规则后核对的截图。

## 功能边界

账号识别、微信绑定、手机号授权、订单报价与支付、钱包资金操作、消息和客服接口沿用原逻辑。新增的页面逻辑仅为服务目录的筛选区展开/收起，已选条件会在按钮上提示。没有修改后端、接口契约、表单字段顺序或隐私授权文案。

## 验证记录 · 2026-09-22

- `node scripts/check-native.mjs --require-compiler`：40 页结构与 JS 检查、41 个 WXML 模板、42 个 WXSS 样式表通过官方编译器检查。
- `node --test scripts/test-native-core.cjs`：18/18 通过，覆盖账号隔离、快捷登录/绑定、头像、支付幂等、报价保护及所有模板事件绑定等现有回归。
- 对 24 页分别使用普通内容和长文本/金额样例，在 320、375、430 CSS px 三种宽度检查，共 144 个页面布局组合；加入官方基础库默认控件规则后，未发现页面横向溢出、微信默认绿色覆盖或关键文字/操作被自身容器截断。
- 人工查看 24 页总览，并查看手机号绑定、展开筛选、未登录、加载、错误和空列表等代表状态。
- 当前微信开发者工具窗口无法激活，服务端口未开放，因此此次没有完成真实模拟器/真机视觉检查。浏览器预览结果不等同于微信运行时验证；原生授权弹窗、键盘与安全区仍需在开发者工具和真机核验。

## 资源来源

图标继续使用 Tabler 官方 MIT 图标，来源固定至 v3.31.0。`scripts/create-native-icons.cjs` 生成 10 张 Tab 图标和 14 张功能图标，许可证在 `native/assets/TABLER-LICENSE.txt`。

首页/登录插画通过内置 imagegen 工具生成，部署文件为 `native/assets/brand/companionship.png`（560 × 560，约 153 KiB）。仅缩放与压缩，未使用第三方人物或虚构服务封面。

生成提示词：

> Use case: stylized-concept. Create a premium, friendly 3D illustration asset for a Chinese game-companion WeChat mini-program homepage. Subject: one sculptural over-ear gaming headset in blush pink and pearl white, with a small glossy rounded pink conversation bubble and a tiny heart-shaped speech accent, carefully composed as one cohesive still life. Soft tactile satin polymer and fine soft fabric cushions, polished professional product-render quality, charming but not childish, no cartoon eyes, no human figures. Light blush-white studio backdrop (#fff3f7), soft diffuse daylight, subtle physically plausible contact shadow, one restrained berry-rose accent (#b83d68). Composition: square image, centered objects occupy 75 percent of frame with generous clean margin; must read beautifully at a small 160px mobile card size. No text, no letters, no logo, no watermark, no UI screenshot, no neon, no purple, no extra devices. Deliver the image asset only.
