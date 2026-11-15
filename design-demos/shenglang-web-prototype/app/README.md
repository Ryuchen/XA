# 声浪电竞三端门店原型

基于 React、TypeScript、Vite、Tailwind CSS 与 shadcn/ui 的既有三端高保真原型。本次补充“门店＝独立租户”概念：每家门店拥有独立老板端、陪玩端和店内后台，业务数据按店隔离；平台层只管理门店元数据、入口、初始化与启停。

> 本项目是设计原型。门店、账号、订单和金额均为示例数据，不连接真实微信、数据库、支付或客户，不能作为生产级数据隔离已经实现的证明。

## 本地运行

```bash
npm install
npm run dev -- --host 0.0.0.0
```

常用检查：

```bash
npm run build
npm run lint
node scripts/verify-store-prototype.mjs
```

验证脚本会启动临时服务并在结束后关闭；报告和截图输出到 `verification/`。

## 三端独立入口

以本地预览地址为例，将 `http://localhost:7100/` 替换为实际开发地址即可：

| 门店 | 老板端 | 陪玩端 | 店内后台 |
|---|---|---|---|
| 声浪电竞·星海店 | `?store=shenglang#/login` | `?store=shenglang#/provider-login` | `?store=shenglang#/admin-login` |
| 星潮电竞·湖滨店 | `?store=xingchao#/login` | `?store=xingchao#/provider-login` | `?store=xingchao#/admin-login` |
| 雾岛电竞·云城店 | `?store=wudao#/login` | `?store=wudao#/provider-login` | `?store=wudao#/admin-login` |

平台门店管理：`#/admin-stores`。

门店参数只表达原型入口，不是鉴权凭证。三端可切换门店：老板端回目标店首页；陪玩公开登录／注册页留在对应账号入口并清空未提交资料，已登录工作台回目标店工作台；后台回目标店登录页重新鉴权。切店不保留旧店详情、选品、草稿、弹窗或操作状态。

老板端与陪玩端仅使用微信快捷登录，不提供账号密码登录。两端登录统一完成微信身份绑定、微信手机号读取、短信验证和首次 OTP 绑定；老板端在手机号验证前额外确认微信头像与昵称。入驻与审核查询只验证微信和手机号，不强制 OTP。示例短信验证码与 OTP 动态码均为 `123456`。

登录、注册及审核均为本地演示，不唤起真实微信、读取真实手机号、发送短信、连接验证器或上传证件材料；正式环境不得用手填号码冒充微信授权。

## 页面与设计说明

- 老板端 42 页、陪玩端 15 页、管理后台与平台层 34 页（含登录），共 91 页均有门店数据范围和逐页设计说明。
- 三端统一使用外层原型导航、iframe 页面预览和右侧场景面板；管理后台支持 PC 端与 1366 × 1024 iPad Pro 横屏切换。
- 每页右侧设计面板展示当前门店、数据范围、独立业务库别名和隔离异常反馈，页面内不再重复显示范围说明条。
- 陪玩账号入口覆盖 PC 微信扫码、移动端微信快捷登录、手机号授权与短信验证、首次 OTP 绑定，以及入驻资料、押金方式、材料校验、微信审核查询与退回补充。
- 平台门店管理演示空白门店创建、初始化、三端入口复制与启停，不复制默认店客户、订单或资金。
- 完整租户模型与逐页矩阵见 `STORE-DESIGN.md`。
- 既有视觉方向沿用记录见 `direction-approved.md`，品牌规范见 `brand-spec.md`。

## 主要源码位置

- `src/components/store/`：门店数据、上下文、切换器和逐页设计说明。
- `src/components/admin/AdminDesignShell.tsx`：管理后台统一原型导航、场景与门店设计面板。
- `src/components/prototype/ViewportPreview.tsx`：三端 iframe 与设备视口切换。
- `src/pages/admin/AdminStoresPage.tsx`：平台门店管理。
- `src/pages/provider/ProviderAuthPages.tsx`：陪玩微信登录、入驻申请及审核进度。
- `src/components/auth/OtpBinding.tsx`：老板端与陪玩端共用的首次 OTP 绑定演示。
- `src/components/provider/ProviderWeChatBinding.tsx`：微信快捷登录／入驻、手机号授权、短信验证与登录 OTP 衔接。
- `scripts/verify-store-prototype.mjs`：91 页路由、切店、独立示例数据和响应式验证。
- `scripts/generate-store-design.mjs`：从逐页元数据生成完整设计矩阵。

## 许可与版权

本目录中由青岛声浪传媒有限公司依法享有权利的原创代码、文档与设计属于闭源专有内容，适用仓库根目录的 [LICENSE](../../../LICENSE)。除适用法律或另行书面授权允许外，不授予使用、复制、修改、部署或分发许可。

第三方组件、字体、图标及其他素材仍适用各自原有许可证和版权声明，参见[第三方说明](../../../THIRD_PARTY_NOTICES.md)。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.
