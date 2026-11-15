# 方向选择记录 · 仅提取 UX 动效

## 展示过的方向

- A 声场流光：`dir-1.html`，截图 `dir-1-desktop.png`、`dir-1-full.png`、`dir-1-mobile.png`
- B 开局共振：`dir-2.html`，截图 `dir-2-desktop.png`、`dir-2-full.png`、`dir-2-mobile.png`
- C 轻松组队：`dir-3.html`，截图 `dir-3-desktop.png`、`dir-3-full.png`、`dir-3-mobile.png`
- 六格备选：`thumbs.html`、`thumbs.png`

## 用户选择原话

「不需要这三种布局，只要把这三种页面中的一些UX动效加入到现在的老板页里即可」

## 实施边界

- 不采用 A/B/C 的页面布局，不替换当前视觉世界，不新增页面结构或业务文案。
- 保留老板端当前布局、组件、字段、路由、登录边界与业务逻辑。
- 仅提取：A 的声波反馈、B 的组队/步骤响应、C 的按压回弹与轻盈卡片反馈，以及现有导航的联动。
- 动画不得改变布局尺寸、点击目标、信息层级或数据含义；支持 `prefers-reduced-motion`。
- 当前页面为 Operate 模式：动感服务于选择、状态与操作确认，不做全屏炫技或所有区块统一漂浮。
