# KOOK 派单配置与积分运营组件迁移（2026-11-02）

本轮仅修改前端。未修改任何后端接口、模型、数据库迁移或资金/订单业务规则；未向外部 KOOK/企微发送消息。保留工作区已有修改。

## 文件范围

- apps/admin-web/src/views/kook-dispatch/index.vue：按钮、输入、文本域、开关、复选框、状态、提示、下拉、表格、规则弹窗与确认框全部使用现有 shadcn-vue 及应用确认组件；保存冲突、未确认请求、请求 ID 幂等核对、会话修订与离开保护逻辑保留。
- apps/admin-web/src/views/engagement/index.vue：所有原 Element 控件迁移。积分礼物、转盘、奖品交付、TAG 审核/暂停、VIP 权益、套餐绑定、预存档位、播报、活动及运营设置继续使用原接口及字段。
- packages/shadcn-vue/src/components/{tabs,number-field,pagination,select}/：从官方参考源码新增，package.json 仅增加对应 exports。源码版本 4d6963aa8a482be7609cb05d3f8b7fe42082b634（现有 .tools/shadcn-vue-reference）。只适配包内 import、PaginationItem 显式 value 和最小图标按钮默认尺寸。

对应组件文档：[Dialog](https://www.shadcn-vue.com/docs/components/dialog)、[Number Field](https://www.shadcn-vue.com/docs/components/number-field)、[Tabs](https://www.shadcn-vue.com/docs/components/tabs)、[Pagination](https://www.shadcn-vue.com/docs/components/pagination)、[Select](https://www.shadcn-vue.com/docs/components/select)。

两页按钮显式 xs，下拉触发器 sm，新增分页按钮 icon-xs。无尺寸枚举的输入/开关等保持各自组件行为；全局默认尺寸仍需统一盘点。

## 交互适配

- 数字使用 NumberField 的步进、最小/最大值及精度配置；金额换算函数保持原实现。
- 日期使用 shadcn Input 的 date/datetime-local，展示时从 ISO 转为本地时间，编辑后转回 ISO；保留秒级精度及清空值。
- 权益与套餐选择保留搜索、多选和数值 ID，搜索框使用 shadcn Input。
- 标签暂停原因用真实 Dialog + Textarea 收集，保持 2–255 字验证；取消兑换保留明确确认及返回。
- 两页长弹窗采用专属 global 类限制高度，避免 Portal 节点无法命中 scoped 样式而让页脚超出视口。

## 验证

1. admin-web 的 vue-tsc --noEmit 通过；Vite 独立生产构建通过，产物 .tools/admin-engagement-shadcn-migration-20261102/。
2. 原有 console.test_kook_template_warning_browser 在 SQLite LiveServer + 375px Edge 中通过。
3. 真实业务接口回归 82 项通过：engagement.tests、test_coupon_bindings、test_gift_benefits、test_lottery_gacha 及 console.test_kook_config；日志 .tools/shadcn-migration-acceptance-20261102/business-regression.log。
4. 自有隔离 LiveServer 浏览器专项在 .tools/shadcn-migration-acceptance-20261102/{run.py,browser.cjs}：验证新建礼物、12.3 币换算与数据库回读、转盘长弹窗、TAG 暂停输入验证/保存、取消兑换返回、VIP 两个服务多选、权益搜索选择、运营日期保存及 KOOK 草稿放弃返回/预览应用/配置保存。最终结果见 browser-result.log，截图同目录。

以上验证不包含真实外部 KOOK 投递、微信手机号/支付或 App 真机。

## 余项（历史交接快照，已由后续收尾报告替代）

仍需完成 escort/index.vue、order/index.vue 与 CrudTable 的真实组件迁移；后者包含 selection/row-key/表格实例使用，不能先删旧库导致订单功能失效。main.ts 全局 ElementPlus/图标/语言及样式、App.vue 的 ConfigProvider、layout Search 图标、vite.config.ts 的 ElementPlusResolver、package.json 两项依赖、tsconfig global 类型、auto-imports/components 生成声明与旧 el 样式/变量需在使用者迁完后清理。其他业务页虽无旧控件，仍有 --el-* 样式变量引用，须改为当前主题令牌。

共享 Button 默认仍 sm，ConfirmDialog 内按钮未显式最小尺寸，Input/NativeSelect 默认 h-9，SelectTrigger 支持 sm，NumberFieldInput 默认 h-9，Switch 20px。最小规格应通过组件变体/局部配置调整，并在 375px 对弹窗、键盘、触控及多选内容复验，不能仅缩放全局样式。

后续已完成陪玩、订单、CrudTable、全局样式/图标及旧依赖清理。当前验收与剩余范围见 `shadcn-admin-migration-final-20261102.md`，本页余项不再代表当前状态。
