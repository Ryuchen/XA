# 后台旧 UI 库迁移收尾（2026-11-02）

本轮保留工作区全部已有修改，仅修改前端和验收文档。后台 Element Plus 的组件、全局接入、动态图标、旧样式与依赖已解除。业务接口、金额转换和订单状态规则保持现有实现；测试使用独立临时数据库/媒体目录与随机端口，没有触及生产资金、客户或外发消息。

## 主要实施范围

- `apps/admin-web/src/views/kook-dispatch/index.vue`、`engagement/index.vue`：真实 shadcn 表格、开关、标签、提示、分页、日期/数值输入与弹窗；保留版本冲突、草稿、未知结果核对、会话失效和离开保护。
- `apps/admin-web/src/views/escort/index.vue`：实际 Table/Avatar/DropdownMenu/Select/RadioGroup/NumberField 等，保留创建、上传、资质、搜索、认证、奖罚和退押金；为账号/密码/昵称错误预留高度，修复 blur 报错插入时底部提交按钮位移导致点击丢失的问题。
- `apps/admin-web/src/views/order/index.vue`：剩余快捷派单、筛选、远程搜索、报价数值及富确认/原因输入迁移；保留单陪/双陪槽位、重复人员拦截、服务计价、报价序列、提交确认、结果待核对、取消退款等原逻辑。
- `components/CrudTable.vue`：实际调用者只有订单/陪玩，两者均提供 desktop/mobile/toolbar 插槽；删去旧表格实例分支，继续使用现有 selectedRows/查询快照/分页状态与原生横滚。缺少 desktop 插槽时显示明确提示。
- `components/SearchSelect.vue`、`ActionDialog.vue`、`composables/useActionDialog.ts`：以真实 shadcn Select/Input/Dialog/Textarea 组合适配现有搜索和 rich VNode 确认契约。原权限/时序/业务校验不迁移到组件内部。
- `components/SimplePagedList.vue`：按钮显式 xs；共享 Button 的默认变体改 xs，SelectTrigger 默认 sm，Pagination 图标按钮默认 icon-xs。Input/NativeSelect/NumberFieldInput/Avatar 没有原库尺寸枚举，使用其实际单一规格；不添加无效 size 属性。所有本轮 NumberField 显式关闭步进吸附，原 step/精度与真实小数保存行为保留。
- 375px 陪玩编辑长表单补验修复：原24列网格仅把字段设为整行，但23个16px列缝最低占368px，超出303px内容区。窄屏现在切换为单列，并允许表单/分组收缩，保留所有字段；实际弹窗scrollWidth不超过clientWidth。
- `packages/shadcn-vue`：从官方版本 `4d6963aa8a482be7609cb05d3f8b7fe42082b634` 复用 tabs/number-field/pagination/select/avatar/dropdown-menu/radio-group，适配包内 imports 与导出；Lucide 图标有明确静态/动态映射，覆盖所有导航键。
- `main.ts`、`App.vue`、`vite.config.ts`、`tsconfig.json`、`components.d.ts`：去掉旧库安装、ConfigProvider、全局图标/类型及 Element resolver。Overlay 检测继续识别 dialog/alertdialog。
- `styles/{index,controls,modals,ocean}.scss` 与 19 页局部 style：通过 PostCSS 保留有效选择器，将旧变量精确映射现行主题令牌，删除无使用者的旧类。完整文件列表为 `.tools/shadcn-global-migration-20261102/changed-styles.json`；修改前副本在同目录。
- admin `styles/shadcn.css` 启用仅该应用构建的 Tailwind preflight，扫描 admin 与共享组件源码，补齐标准主题 border-color 基础层。provider 构建不引入 admin reset；其 style.css 仅同步 Button 44px 触控下限。
- 粗指针规则仅匹配对应 data-slot 控件；图标按钮宽度仅匹配直接唯一 svg 子节点，未匹配无关嵌套按钮。状态变体的显式边框色继续优先于 base 层。DialogClose 粗指针或720px以下使用44×44点击框，桌面视觉尺寸保留；375px实测点击框在弹窗边缘内，与标题文字不相交，并可tap关闭。

## 依赖与引用清零

`apps/admin-web/package.json` 删除 `element-plus` 与 `@element-plus/icons-vue`。pnpm 9.0.0 仅生成 lockfile，不执行安装脚本/重建 node_modules。与清理前 lockfile 比较：只改 admin importer，删除 11 个旧库专属 package/snapshot，无包新增、无版本升级；另两条 VueUse 3.5.44 快照按剩余使用路径变为 optional，其他项目 importer 与依赖条目均不变。精确 diff：`.tools/shadcn-global-migration-20261102/lockfile-final.diff`。

使用包含 ignored/hidden 的搜索，核对 admin/provider 源码、packages/shadcn-vue、main/App、Vite/tsconfig/package、生成声明、pnpm-lock 中 `element-plus/ElementPlus/ElMessage/<el-*/.el-*/--el-*`：当前 0 项。历史文档、验收备份及本地 node_modules 缓存保留追溯，不参与应用接入；未为清理缓存删除用户依赖目录。

## 验收结果

- admin/provider `vue-tsc --noEmit` 与独立 Vite 生产构建通过。最终产物 `.tools/admin-global-shadcn-final-20261102`、`.tools/provider-global-shadcn-final-20261102`，日志在 `.tools/shadcn-global-migration-20261102/`。
- 后台 65 个现有前端测试全部通过。只更新 UI 服务 mock 导入适配，保留原时序/状态/权限与金额断言；没有 mock 核心后端业务。
- 四组真实 LiveServer 浏览器专项共 48 项通过，pageErrors 均为空。均对最终独立生产产物执行，桌面和 375px；每组 `run.py` 包含数据库回读断言，日志 `final-rerun.log`，截图同目录。路径如下。

| 目录（位于 .tools） | 通过范围 |
| --- | --- |
| shadcn-order-acceptance-20261102 | 12 项：真实已付订单取消/理由退款，老板与陪玩远程搜索、套餐搜索、单陪报价/返回不扣款/确认提交、双陪槽位/重复拦截、固定抽成12.3、375px富确认；数据库最终三订单、两笔成功扣款，固定抽成123内部单位、两名不同陪玩。并覆盖工作台真实 KPI 图标渲染。 |
| shadcn-escort-acceptance-20261102 | 14 项：表格、游戏筛选搜索/清空、关键词清空、空创建三字段验证、密码显示、多选、头像上传、编辑返回、时价12.3/胜率72.35真实保存、认证、奖罚12.3、退押金1.2、375px卡片。触控编辑按钮实测44px。 |
| shadcn-table-acceptance-20261102 | 10 项：全选/单选、复制文本、横滚、分页与pageSize清选择、加载/空态/网络失败重试、375px选择与翻页。仅剪贴板写入使用隔离接收器，不写用户系统剪贴板；失败重试只阻断传输，不替换业务响应。 |
| shadcn-migration-acceptance-20261102 | 12 项：积分礼物12.3保存/数据库123、表格、375px、转盘长弹窗、KOOK草稿返回/预览应用保存、TAG暂停验证保存、取消兑换返回、VIP多服务选择、权益搜索、运营日期保存。 |

全局变更后，视觉 agent 在既有 13022/13024 隔离预览完成七个陪玩页和后台通行证 375/768/1440 共24组核对：无横溢、最小按钮和44px触控通过；关闭图标补齐后另完成两端375/768/1440六组复测，标题无遮挡、44×44且实际关闭成功；另积分键盘标签/弹窗、技能点击/禁用/确认、后台支持窄卡片与桌面表格通过。最终主题边框补齐后，再复核订单表格、工作台卡片、陪玩输入/弹窗及375px截图，原状态色保留。

补充真实后端回归沿用此前结果：积分/KOOK82项、陪玩相关23项、押金30项通过。订单/派单60项中59通过，1个既有身份迁移用例失败：`DispatchRealWorkflowTest.test_evaluation_follows_explicit_accounts_after_identity_migration`（orders/tests/test_dispatch_real_workflow.py:1119，预期provider account5，实际3）。未修改后端规避；该项仍需主业务排查。外部支付、KOOK/企微投递与App真机不在这48项通过范围。

## 微信多端剩余工作

仍为官方能力核对，未声称完成多端迁移。账号是否已绑定多端应用、开发工具实际升级入口与项目生成结果尚待确认；不得仅加配置标记冒充升级。wx.login、手机号/头像开放能力、App支付及客服均需按官方平台差异逐项实施并用实际绑定配置/安装包验证。当前用户唯一小程序目标机型为 iPhone 12 Pro；后台与陪玩H5适配范围不变。详见 `boss-multiplatform-readiness-20261102.md`。
