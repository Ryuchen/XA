# 本地域名部署验收（2026-11-05）

## 部署脚本固化复验（2026-11-06）

后续标准入口为 `deploy/dev.ps1`，详见 [开发环境部署](../README.md)。18106 已交由版本化 `scripts/serve-backend.py` 与 `backend.ps1` 管理，不再依赖交互会话或 `.tools` 中的运行代码。

- 连续两次 `Up`（其中一次从仓库父目录调用）成功，运行中四个容器 ID/启动时间及后端 PID 不变，hosts 修改时间与共享 MySQL/Redis 容器 ID 不变。证据 `.runtime/repeat-up-result.json`。
- `Up -Hmr` 五入口全部收到实际 CSS 更新且内容散列不变；Nginx `Reload` 先校验再重载通过。
- `Down` 只停止本 Compose 项目和受管 18106；原 18105、13101/13102/13103 以及共享 MySQL/Redis 保持运行。随后 `Up` 恢复四个健康容器和受管后端，五入口均返回 200，域名与门店绑定正确。
- 后端生命周期在独立 18107 端口验证重复启动、重启、停止和重复停止；篡改进程创建时间记录后拒绝终止且原进程保留，测试进程已清理。
- PowerShell 脚本语法检查通过。运行状态、凭据、缓存、hosts 备份和日志均保持在忽略提交的位置。

三个前端容器（boss、provider、admin）及 Nginx 网关运行健康。Windows hosts 将 `sl.esports.club`、`xa.esports.club`、`admin.esports.club`、`esports.club` 解析到 `127.0.0.1`。这是本机 HTTP 预览，业务与 HMR 使用 WebSocket；本轮没有部署公网 DNS 或 HTTPS 证书。

## 已执行

- 四个老板/陪玩入口使用真实隔离身份、接口和数据库载入，品牌与门店一致，没有门店切换按钮。篡改 query/header 返回 400，跨店令牌返回 401，跨店 WebSocket 被拒绝；四页均收到实际业务心跳且无页面异常、失败资源。
- 统一后台使用现有测试账号完成真实密码与 TOTP 登录：声浪平台超管、声浪普通后台账号、兴安后台账号及兴安只读账号。连续退出再登录正确识店，页面重新打开后恢复本店；普通账号不能管理门店，平台超管可管理。后台 API 与 WebSocket 均拒绝老板/陪玩令牌。
- 后台两店工作台加载实际数据；登录后新文档清理此前身份的模块状态。资料同步和令牌续期保留当前会话，不触发全页重载。
- 五入口逐一连接实际 Vite HMR，触碰各自 `index.css` 修改时间后均收到更新。源码 SHA256 前后相同，没有覆盖并行工作的源码。前端源码、公共金额模块、代理配置及启动脚本均已通过 Docker inspect 确认外置挂载。
- 三端 TypeScript 检查通过；前端共享测试 93 项通过，最后登录导航/代理修正后 39 项关键回归通过。老板/陪玩路径构建及后台 Linux 容器构建通过。
- 后端最终 130 项回归中 129 项通过。既有 `OtpReplayConcurrencyTest.test_same_dynamic_code_can_only_login_once_across_challenges` 因 SQLite 不支持行锁跳过；新门店识别、OTP、续期、私密图片及实时通道隔离用例均执行通过。

## 数据与运行范围

沿用两套独立测试数据库 `xa_test_fronted_acceptance_20261104` 与 `xa_test_store_preview_b_20261104`，未复制客户或资金。后台数据库仍由统一业务服务按已验证身份选择；域名代理不接受用户提供的数据库名称。

后端运行在本机 `18106`，保留原 `18105` 与原三个前端预览进程。新前端由容器运行。老板端测试只替换微信与短信的外部传输，票据、短信校验和账号鉴权执行真实业务代码；不代表真实微信生产登录已验收。

本轮启动、配置重载与后端恢复命令见 [README](README.md)。内部证据位于 `.runtime/hmr-results.json`、`.runtime/mounts.json`、`.tools/visual-update/store-domain-preview-results.json`、`.tools/visual-update/admin-domain-login-results.json` 和 `.tools/visual-preview/neutral-admin-full-tests.log`。私有凭据不纳入版本控制或用户报告。
