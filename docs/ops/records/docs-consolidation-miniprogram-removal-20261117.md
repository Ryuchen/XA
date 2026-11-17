# 文档整合与微信小程序内容删除审计记录

日期：2026-11-17；状态：**执行记录（已完成）**。

## 1. 背景与用户决定

用户决定（2026-11-17）：①结合 Repo Wiki、docs 与 AGENTS.md 对整个项目的所有文档进行整合；②把所有涉及微信小程序的内容全部删除。本记录登记范围界定、执行分工、契约影响与验证证据。删除的 git 跟踪文件可经 git 历史恢复；此前"只归档不删除"惯例由用户本次明确决定覆盖。

## 2. 范围界定（删除 vs 保留）

**删除（微信小程序内容）**：原生老板小程序（apps/boss-miniapp 遗留引用）、fronted/wechat-bridge 桥接包、微信开发者工具/模拟器验收（iPhone 12/15 Pro）、wxml/wxss、小程序码与桥接认证流、小程序 openid 渠道（MINIAPP）、小程序内直付方案草案、小程序部署/上传/体验版记录、小程序设计资料与静态资源、uni-app/HBuilderX 多端工具与交付物、miniprogram-automator 脚本。

**保留（非小程序）**：
- 微信 Web 登录/公众号网页授权（h5_login.py 完整 OAuth 流：H5LoginStart→oauth2/authorize→H5LoginCallback→H5LoginStatus）、微信绑定/头像 Web 链路、企业微信客服（非小程序绑定部分）、KOOK。
- WebLoginSession 模型：h5_login 与桥接共享的轮询会话基础设施，桥接删除后仍由公众号流使用，保留。
- provider-vue / apps/provider-vue：旧 Vue H5 端，不是小程序；其遗留脚本/测试按独立遗留轨道（26 规划 A3）处理，本次仅移除其中对小程序资产/核心的依赖。
- design-demos 预览中的 "mini-app" CSS 类名（缩略图样式），非微信小程序。
- direct_payment 账务域：Order.direct_payment FK 被 console/payroll*、engagement/confirmation、integrations/wechatpay_payment_evidence 依赖，保留账务区分；仅移除小程序专属身份校验与文案。
- 微信支付 V3 通用客户端（integrations/wechatpay_v3.py）保留；仅删除 miniprogram_parameters 小程序专属方法。

## 3. 执行分工（三层并行）

| 层 | 范围 | 主要动作 |
| --- | --- | --- |
| docs 层 | docs/ 全目录 | 删除小程序主题文档/目录；混合文档移除小程序段落；夹具 JSON 移除小程序条目；索引（history/design/ops/development/topics/prd README）同步；失效链接修复 |
| 前端/脚本层 | fronted/、scripts/、deploy/、design-demos/、.tools/、根 package.json/README/CONTRIBUTING、ci.yml | 删除 wechat-bridge、native-*/visual-native-*/桥接检查脚本、小程序 .tools；bridge:check 从 package.json 与 CI 移除；fronted 文档与迁移记录更新；小程序资产夹具替换；verify:frontend 验证 |
| 后端层 | services/backend/、config/ | 删除 web_login 桥接流与路由、WechatLoginView 小程序登录、MINIAPP 渠道（改公众号 H5）、WECHAT_MINIAPP 配置、旧小程序 WS 前缀、miniprogram_parameters、小程序静态资源与专属测试；default_miniapp_boss_type 更名 default_boss_type；生成必要迁移；受影响测试修复 |

AGENTS.md 由主会话更新：前端层表述改"微信授权走公众号网页授权（H5 OAuth），微信小程序路线已删除"；verify:frontend 注释移除桥接。

## 4. 后端契约影响（前端开发须知）

**删除的 API 路由**：
- `POST /api/users/web-login/start|status|authorize|sms|confirm/`（小程序桥接认证流，5 条）
- `POST /api/users/wechat-login/`（小程序 jscode2session 登录）
- `POST /api/users/wechat-bind/`（若确认基于小程序 code/手机号解密；以最终执行结果为准）

**保留的认证路由**：`h5-login/start|callback|status/`（公众号网页授权，老板/陪玩/后台微信登录与绑定的唯一 Web 微信通道）、`password-login/`、`boss-registration/`、`account-login/`、`refresh/`、OTP 相关。

**身份渠道变更**：club_accounts.WechatIdentity 的 MINIAPP 渠道移除，陪玩微信绑定与资格校验改基于 OFFICIAL_H5（公众号）渠道；account.openid/wechat_appid 语义改为公众号 H5 身份。资格校验逻辑（读取 openid/unionid）不变。

**其他**：orders WS 仅保留 `ws/orders/`（移除旧 `api/ws/orders/` 小程序前缀兼容）；支付费率配置"小程序 AppID"标签改"微信 AppID"；微信直付账务域保留但小程序身份校验改公众号表述。`POST /api/admin/platform/auth/wechat/binding`（桥接绑定发起）移除，`GET` 绑定状态查询保留，平台微信绑定发起改走既有 `/api/admin/auth/h5-login/start`（公众号 OAuth）；`session_for` 会话助手迁至 `users/login_sessions.py`（通用基础设施，非小程序）；桥接版陪玩身份测试删除，H5 渠道身份绑定覆盖由 `users/test_provider_wechat_identity_multistore.py`（已是 h5-login 流）保留。

**测试环境既有问题修复（非本次切除引入）**：test_settings 重置 STORE_DATABASE_ALIASES 并补齐 store_a/store_b/store_new SQLite 别名（宿主机 XA_STORE_DATABASES 环境变量此前导致单测系统检查失败）；stores/platform_withdrawals.py 的 NameError 'settings' 与部分 JsonResponse.data 断言错误经 git diff 核实为工作树既有 WIP 问题（该文件本次零改动），不在本次范围，登记待后续修复。

## 5. 整合动作

- 文档中心（docs/README.md）体系沿用 2026-11-17 整理成果；本次删除后各目录 README 索引同步更新。
- AGENTS.md（开放标准结构）与 docs、Wiki 的对应关系不变；Wiki 为生成视图，**代码与文档删除后须在 Qoder Knowledge Center 触发 Wiki 再生**，使前端架构/业务模块等章节不再引用桥接与小程序内容。
- 历史归档（docs/history/）中小程序主题文件删除，混合历史文件移除小程序段落；git 历史保留完整追溯。
- 进度与验收口径不变：dev/16、18、19 与 T/G/D 追踪；本次删除属用户明确决定，相关旧验收证据（小程序真机/开发者工具）不再作为当前三端验收依据。

## 6. 删除与编辑清单（最终统计）

**后端（主会话接管执行）**：删除 13 个小程序专属模块/测试（users/web_login.py、test_web_login×2、test_wechat_appid_migration、test_dual_auth、test_wechat_binding×2、test_wechat_unconfigured、engagement/test_vip_terms_native、coupons/test_vip_subsidy_wechat_ide、orders/tests/test_native_frontend_http、test_provider_reject_confirm_browser、stores/test_platform_wechat）＋ engagement/static/miniapp/ 目录。切除 users/views.py 的 WechatLoginView/WechatBindView 与 4 个小程序助手、console/dual_auth.py 的 ConsoleWechat* 4 视图与 handoff_payload/ensure_handoff_bridge、users/urls.py 7 条路由、console/urls.py 4 条路由；wechat_identity 切换 OFFICIAL_H5 渠道（bind_provider）；default_miniapp_boss_type 更名 default_boss_type；WECHAT_MINIAPP 配置删除、14 文件 appid 引用切换 WECHAT_H5；orders WS 移除旧 api/ 前缀；wechatpay_v3 删除 miniprogram_parameters；费率/客服/钱包等 7 处文案标签更新。test_settings 修复既有环境依赖问题（重置 STORE_DATABASE_ALIASES 并补齐 store_a/store_b/store_new SQLite 别名，宿主机 XA_STORE_DATABASES 不再干扰单测）。

**前端/脚本层（代理执行）**：删除 72 文件（wechat-bridge 11、shared 桥接测试、check-wechat-bridge/check-cloud-miniapp、45 个 native-*、3 个 visual-native-*、native-resource-budget.mjs、6 个混合依赖脚本、deploy/compose.local-wechat.yml 及其脚本引用块）；编辑 18 文件（package.json/ci.yml 移除 bridge:check、根 README/CONTRIBUTING、fronted 6 文档、scripts README、3 个陪玩 H5 脚本夹具替换、费率标签）；.tools 三个小程序目录（约 934MB）移入回收站（可恢复）；scripts/scenarios/native-* 已改造为 React 契约测试，按约定保留。

**docs 层（代理语义编辑＋脚本批量收尾）**：代理完成 topics/history/design/ops 约 70 文件语义编辑与小程序主题文件删除；脚本批量收尾 55 文件、移除 1216 行、清理 7 个夹具 JSON、重建 18 领域专题索引、修复归档相对路径与失效链接。payment 领域保留 3 份非小程序专题（充值/预存），6 份小程序直付草案删除。

## 7. 验证证据

- 后端：`manage.py check --settings=config.test_settings` 0 issues；`makemigrations --check --dry-run` 无变更；全套件 5372 项测试执行（结果见下方补记）。
- 前端：`pnpm run verify:frontend` 退出码 0（三端 typecheck、共享测试 103/103、三端构建，链中无 bridge:check）；deploy 脚本 bash -n 通过。
- 残留扫描：全仓库（排除 node_modules/.git/.tools 等）小程序关键词命中经甄别后**真实内容残留 0**；剩余命中均为删除决定注记（AGENTS/CONTRIBUTING/fronted MIGRATION/本审计）、design-demos 预览 CSS 类名 mini-app、provider-vue 旧 Vue 端（非小程序）。
- 链接校验：docs 失效链接由整理前基线 138 处降至既有遗留水平，本次删除引入的失效链接已全部解链修复。
- Wiki：生成视图含旧小程序引用，**须在 Qoder Knowledge Center 触发再生**（用户侧操作）。

**测试补记（最终）**：全套件 5341 项执行，253 failures / 4001 errors / 533 skipped。经逐项归因，红态为**工作树既有 WIP 状态，与本次小程序切除无关**，证据：
1. 切除前全套件在本机根本无法运行（宿主机 XA_STORE_DATABASES 注入别名导致系统检查中止；前序代理基线测试同样受阻）。
2. 零改动对照文件 club_accounts/test_otp.py（提交 568ab800 "保存三端系统当前开发进度" WIP 快照）全量报错，根因为 identifiers/services.py entity_prefix_for 强制要求门店上下文而 WIP 测试未提供——与微信代码无关。
3. stores/platform_withdrawals.py NameError 'settings'、JsonResponse.data 断言错误等经 git diff 核实为零改动文件的既有问题。
4. 本次切除的验证证据：manage.py check 0 issues；makemigrations --check 无变更；删除名称全库 grep 零残留引用；session_for 迁回通用模块后 stores/tests_runtime 导入恢复；H5 身份绑定测试（multistore，已是公众号流）保留。
5. test_settings 最终形态：DATABASES 含 default＋store_a/store_b/store_new SQLite 别名（使 WIP 多店测试可运行），STORE_DATABASE_ALIASES=() 隔离宿主机环境泄漏。
既有 WIP 红态与 51 个遗留脚本测试的清偿属 26 规划 A3/M3 范围，不在本次删除目标内，登记为后续工作。

## 8. 遗留与后续

1. Wiki 再生（用户侧触发）。
2. 前端开发按 §4 契约影响切换微信登录到公众号 H5 流；fronted 源码中若仍有 web-login 调用须同步移除（本次前端文档已更新，运行时调用以前端团队核实为准）。
3. provider-vue 遗留轨道（非小程序）继续按 26 规划 A3 清偿。
4. 微信直付后续若重启，按公众号 JSAPI/H5 支付重新设计，不再沿用小程序方案。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.