# 小程序包内图片与音频预算修复

2026-09-28，开发版本 `2026.9.28.1` 已通过微信官方 CLI 上传至小程序 `wx495c9fb56eb3ad24`。

## 检查与改动

原生小程序 `apps/boss-miniapp/native` 原有 28 张图片，共 295,283 字节（288.4 KiB），没有包内音频。单张图片均小于 200 KB，但合计超过用户反馈的 200 KB 检查门槛。

两张展示插画原样移到后端应用的静态资源目录 `services/backend/engagement/static/miniapp/brand/`，通过 `https://api.xaesports.club/static/miniapp/brand/` 加载。首页、登录、我的和礼物页共用 `core/brand-assets.js`，随环境配置选择资源地址。图片没有重新编码或降低画质；文件名带内容摘要，方便缓存与后续更新。

| 图片 | 字节数 | SHA-256 |
| --- | ---: | --- |
| companionship.ef8c5df7748b.png | 156,952 | ef8c5df7748be2f3dfdce3f06aa87c8dda36d54a6694723c6d91d0d3467c404f |
| gift-moment.621950ab1523.png | 108,724 | 621950ab1523f6372cf4cea28b2574814d40568ee362616c866a20f9be377d83 |

包内保留 26 张小图标，共 **29,607 字节（28.9 KiB）**，最大单张 2,004 字节。检查使用更保守的 **200,000 字节合计预算**。微信官方上传、预览记录的整个代码包为 **387,796 字节（378.7 KB）**，原版本为 653,057 字节；代码包总量与媒体资源预算是两个指标。

`scripts/check-native.mjs` 在编译前检查包内所有图片和音频的合计大小；超过预算即失败。CI 中的回归覆盖“每个资源均小于门槛，但图片与音频合计超限”的情况。生成上传项目和旧 IP 联调项目时会移除源树已不存在的文件，防止旧插画残留后再次被打包。

## 验证证据

- 原生结构与 JS：47 个页面；官方 WXML 编译 48 个模板、WXSS 编译 49 个样式表均通过。
- 客户端组合回归：136 项通过，零失败。
- 两张公网 HTTPS 图片返回 HTTP 200、image/png，内容和本机原图逐字节一致；微信 `getImageInfo` 解码为 560×560、480×480。
- 开启合法域名校验的微信官方模拟器：真实登录 code 经后端仅检查身份兑换成功；首页、登录、我的、礼物四个页面的资源地址与截图均已核验。
- 实际上传项目 `.tools/tencent-deploy/boss-cloud-project/native` 的资源合计也为 29,607 字节。
- 上传与预览信息：`.tools/tencent-deploy/wechat-upload-2026.9.28.1.json`、`wechat-preview-2026.9.28.1.json`；手机二维码 `wechat-preview-2026.9.28.1.png`。
- 图片检查与页面截图：`.tools/tencent-deploy/native-cloud-report.json`、`native-cloud-*-artwork.png`。

## 服务器资源与后续审核

当前服务器原图保存在 `/opt/xa-current/services/backend/engagement/static/miniapp/brand/`，并已发布到现有持久化静态卷 `xa_backend_static` 的 `miniapp/brand/`。Caddy 通过已有 HTTPS `/static/` 路由读取该卷。本次仅添加静态文件，无后端镜像切换、数据库迁移或资金写入。普通容器重启继续使用该静态卷；以后构建新的后端镜像时，这两张图会随 Django 应用静态资源一并进入镜像并由 collectstatic 收集。

需要从源码重新生成上传项目时运行 `python scripts/prepare-cloud-miniapp.py`，再用微信开发者工具打开生成目录。不要上传仍含旧插画的历史项目或旧版本。选择 `2026.9.28.1` 作为体验版本后可再进入提审；这次只确认资源预算、编译、上传与模拟器显示通过，不代表微信正式审核已经通过。此前真机手机号授权正常的反馈对应旧版本，新版二维码可用于再次检查远程图片加载。
