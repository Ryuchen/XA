# 电竞运营管理平台 · Brand Spec

> 采集日期：2026-11-06  
> 来源：本项目真实品牌文件、界面截图及 CSS 变量  
> 完整度：完整（本轮所需的 Logo、界面与色值均已取得）

## 核心资产

### Logo
- 平台：`fronted/admin/public/platform.svg`，六边形电竞标记，透明背景，原始描边 `#2563eb`。
- 声浪：`fronted/admin/public/brand.svg`，声波标记，透明背景，原始描边 `#2563eb`。
- 兴安：`fronted/admin/public/xinan.svg`，爱心山抽象标记，透明背景，原始描边 `#2563eb`。
- 对应透明 PNG：`fronted/admin/public/platform.png`、`fronted/admin/public/xinan.png`。
- 原型将源 SVG 以 data URI 内嵌，保持比例，不重画、不拉伸、不添加白底。
- 原始蓝色透明底版本可用于深浅界面；不凭想象添加反白 Logo。

### UI 截图
- 当前后台：`assets/current-admin-sidebar.png`，用于比较 256px 侧栏、门店切换、分组层级和深色主题。
- 参考界面：`refs/shadcn-sidebar.png`、`refs/polaris-navigation.png`，仅用于分析导航结构，不复制其品牌。

## 色彩
- Blue Planet：`#020617`、`#0F172A`、`#1E3A8A`、`#2563EB`、`#38BDF8`、`#7DD3FC`。
- 奶白与浅灰表面：`#FFFFFF`、`#F8FAFC`；中性文字与边线从项目 shadcn oklch token 继承。
- 深色侧栏现有值：`oklch(0.21 0.006 285.885)`；深色主背景：`oklch(0.141 0.005 285.823)`。
- 浅色主色：`oklch(0.5 0.134 242.749)`；深色主色：`oklch(0.443 0.11 240.79)`。
- 蓝色来自真实 Logo 与既有 Blue Planet 规范，不引入无关彩色渐变。

## 字体与组件
- 字体：`MiSans, -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif`。
- 间距：4、8、12、16、20、24、32、40、44、64、72、80、256。
- 组件圆角以 8px 为主，特殊浮层最多 12px；边框 1px；避免无业务含义的大面积阴影。
- 菜单行高 40–44px，图标 16px，主要菜单文字 14px。

## 禁区
- 不用游戏角色或无关照片填补侧栏。
- 不修改 Logo 形状或加白底，不用 emoji 替代业务图标。
- 不将门店切换伪装成界面内租户覆盖，不把演示门店操作写入真实接口。
- 不用大标题、营销文案或夸张动效抢占操作者的注意力。
