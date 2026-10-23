# 最新版更新与部署 · 2026-10-06

本次按用户要求更新当前工作区的最新版本。网站发布版本为 `2026.10.6.1`，微信官方开发包已上传相同版本。

## 已部署入口

- 管理后台：https://admin.xaesports.club 。最新生产文件已切换，浏览器登录页正常显示。
- 陪玩端：https://provider.xaesports.club 。最新生产文件已切换，浏览器登录页正常显示。
- API：https://api.xaesports.club/api 。三域健康端点均通过。
- 微信小程序：AppID `wxd63488bdf6491c90`，官方 CLI 成功上传 `2026.10.6.1`，包体 396,650 字节。该上传回执仅证明开发包上传成功；本次未确认公众平台已将新版设为体验版，也未提审或正式发布。当前浏览器安全策略禁止打开微信公众平台，需用户在那里选择该版本作为体验版。

## 更新范围与部署依据

管理后台、陪玩 H5 和微信原生小程序采用当前工作区源码。发布前修复陪玩注册与后台注册审核页的四处金额换算契约违规，并将同页人民币显示、输入与审核提交统一接入已有共享金额函数，保持人民币和内部账务单位的口径。

线上后端源码与工作区一致，容器内 478 个 Python 文件逐文件 SHA-256 核验通过，继续使用已验证镜像 `sha256:40372afbc984eacb0916552dea73c32a0306fb194ade40395c13148510641e80`。两个 Web、Worker、Beat 和积分监听服务的镜像保持一致；本次无待执行数据库迁移，无后端代码或数据库配置变更。

新发布目录为 `/opt/xa-update-20261006-1`，`/opt/xa-current` 已指向该目录。发布包 SHA-256 为 `ddbb1e79efea01f0ac3e91183524c4f881ef260d0fbf93a9ed7cb226cef4545a`，清单包含 1,095 个文件，记录了前端源码、静态产物、微信上传工程和复用的后端。管理后台与陪玩端公开 HTML、JavaScript 和 CSS 逐文件 SHA-256 与发布包一致。

切换前数据库、媒体和配置备份保存于 `/var/backups/xa/daily/run-20261006T101504Z-Sva9wA`，备份校验通过。上一发布目录 `/opt/xa-redeploy-20261006` 保留，前端激活脚本包含检查失败时回退旧前端的流程。本次未执行数据清空、账号重置或业务种子导入。

## 验证与证据

- 三端 TypeScript 检查通过；最终管理后台及陪玩 H5 生产构建通过。
- 微信原生官方编译通过：46 个页面、47 个 WXML、48 个 WXSS；包内媒体 28,105 字节。
- 客户端组合回归 166 项全部通过，零失败、零跳过。
- 三端样式、Token 与金额换算契约检查通过。
- 后端系统检查与待执行迁移检查通过；两端浏览器登录页正常显示。
- 两端公网静态文件摘要与发布清单一致，三域依赖健康检查通过。

构建保留已有依赖包体积告警。本轮未登录业务账号或创建交易，不重复执行已验证且源码未变化的后端全量回归；微信真机与体验版本选择尚待用户完成。

本机证据目录 `.tools/tencent-deploy/update-20261006/`：`manifest.json`、`verification.json`、`activation.log`、`backend-runtime-check.log`、类型/构建/客户端/样式检查日志及 `wechat-upload.json`。云端验收记录为 `/opt/xa-current/verification.json`。


## 后台 404 修复 · 同日 18:36

用户访问后台发现 `Request failed with status code 404`。线上日志显示后台实际请求 `/api/auth/profile`，原因是本次生产构建误将 `VITE_API_BASE` 设置为陪玩端使用的 `/api`。后台 Axios 配置需要完整前缀 `/api/admin`，此前只检查页面加载和健康端点未覆盖此构建配置错误。

已以 `VITE_API_BASE=/api/admin` 重新构建并部署后台修复版本 `2026.10.6.2`。发布目录 `/opt/xa-admin-api-fix-20261006`，当前入口 `/opt/xa-current` 已切换；陪玩端与微信上传包仍为 `2026.10.6.1`，后端镜像继续复用。切换前备份位于 `/var/backups/xa/daily/run-20261006T103632Z-cT5Wl2`。

修复验证：52 项配置与登录测试通过，包含实际启动 Vite 构建并确认误用 `/api` 或绝对地址的 `/api` 时构建立即失败；正确配置的类型检查和生产构建通过。最终生产包直接校验 Axios 的 `baseURL` 为 `/api/admin`，124 个公开 HTML、JavaScript 与 CSS 文件摘要一致。公网 `/api/admin/auth/profile` 在匿名状态返回预期 401，健康端点正常，浏览器登录页正常显示。用户已被告知使用 Ctrl+F5 重新载入页面；本轮自动验证未登录用户业务账号。

`apps/admin-web/vite.config.ts` 和 `scripts/check-release-config.mjs` 均新增后台 API 前缀检查，防止相同错误再次发布。证据保存在 `.tools/tencent-deploy/admin-api-fix-20261006/`，含 `tests.log`、`build.log`、`patch-manifest.json`、`activation.log` 和 `verification.json`。


## 资格券修复 · 同日

后台和后端已部署2026.10.6.3，修复不减价资格券核销及取消退款恢复资格的问题。普通券仍按原退款规则返还。完整记录见 `docs/QUALIFICATION_COUPON_FIX_20261006.md`。本轮未录入体验单或优惠券，陪玩H5版本保持2026.10.6.1。
