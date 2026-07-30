# XA 三端样式系统

## 单一事实来源

三端品牌色、语义色、暗色模式和动效时长统一维护在
`packages/design-system/src/_theme.scss`。小程序的间距、字号、圆角、阴影和 Sass
mixins 维护在 `_miniapp.scss`。

业务代码只允许使用语义名称，例如：

- `--xa-color-primary`
- `--xa-color-surface-card`
- `--xa-color-text-secondary`
- `$spacing-md`
- `$radius-lg`

禁止在页面中新增品牌色、背景灰阶、通用圆角和通用阴影的硬编码。业务专属可视化颜色
（等级色、图表序列、运营活动色）可以保留，但需要命名为局部业务变量。

## 分层

1. Primitive：品牌与中性色阶，仅设计系统可直接使用。
2. Semantic：页面、文字、边框、状态等语义 Token，三端组件消费这一层。
3. Component：Button、Card、Input、Dialog、Table 等组件规范。
4. Page：只负责布局组合和业务特有表现，不重新定义基础视觉。

## 单位策略

- 老板端、服务者端：`rpx`，共享同一套小程序 Sass Token。
- 管理后台：`px`，但颜色、字体和动效使用相同的 `--xa-*` CSS 变量。
- `PX` 仅用于 Taro H5 桌面适配场景，不得用于微信小程序基础布局。

## 迁移兼容

旧的 `--c-*`、`--primary` 等变量暂由适配层映射到 `--xa-*`。新代码不得继续使用旧命名；
迁移完成后可删除适配层而无需修改设计系统。

## 页面编写约束

- 页面必须从各端 `styles/variables.scss` 引入 Sass 能力，禁止复制 Token。
- 可点击控件必须覆盖 normal、pressed、disabled、focus 四种状态。
- 卡片、弹窗、表单、列表空状态优先使用公共组件或公共 mixin。
- 安全区使用 `safe-area-inset-*`；弹窗和固定底栏不得写死底部间距。
- 动效遵循 150/250/350ms 三档，并在 `prefers-reduced-motion` 下关闭非必要动画。
- 文本颜色必须满足 WCAG AA；错误、成功信息不能只依赖颜色表达。
