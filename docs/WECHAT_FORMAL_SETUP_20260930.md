# 正式微信小程序配置与验收

更新日期：2026-09-30。正式 AppID：`wxd63488bdf6491c90`。

## 已完成

- 老板端 `apps/boss-miniapp/project.config.json`、根环境模板和腾讯云初始化脚本已切换正式 AppID。
- 本机 `.env.wechat` 已更新，文件限当前用户访问；`.tools/wechat.local.env.dpapi` 加密副本已同步并验证一致，旧加密配置保留备份。
- 腾讯云 `/etc/xa/release.env` 已更新 AppID、AppSecret 并关闭模拟登录。服务器直接调用微信 `stable_token` 验证成功；两个 Web、Worker 和 Beat 共四个容器已核对凭证一致。
- 腾讯云旧环境备份：`/etc/xa/release-before-wechat-20260930T084228Z.env`，权限 600。线上 `/api/health/` 返回 `ok`，两个 Web 均健康。
- 本机 WSL `/etc/xa-local-validation/wechat.env` 及四个应用容器同步更新，旧配置保存在同目录 `wechat-before-formal-20260930T084503Z.env`。
- 正式环境配置检查通过；47 个页面结构、48 个 WXML、49 个 WXSS 通过官方编译器检查。
- 连接生产 HTTPS 的上传工程 `.tools/tencent-deploy/boss-cloud-project` 已重新生成，代码上传配置已改为正式 AppID。

AppSecret 未写入前端、文档或 Git。凭证验证成功仅证明服务器能调用微信接口，不等于新账号已完成域名、隐私、手机号权限或真机登录验收。本次未上传新开发版本、提审或发布。

## 1. 服务器域名与 IP 白名单

登录 [微信公众平台](https://mp.weixin.qq.com/)，确认当前账号的 AppID，再进入「开发管理 → 开发设置 → 服务器域名」。菜单名称以实际后台为准。

| 配置项 | 填写值 |
| --- | --- |
| request 合法域名 | `https://api.xaesports.club` |
| uploadFile 合法域名 | `https://api.xaesports.club` |
| downloadFile 合法域名 | `https://api.xaesports.club` |
| socket 合法域名 | `wss://api.xaesports.club` |
| 服务端接口 IP 白名单 | `43.167.213.74` |

域名项不加 `/api`、`/media`、`/ws/orders/` 或其他业务路径。客户端代码里的 API 地址仍为 `https://api.xaesports.club/api`。检查的是新账号的白名单；本轮生产凭证验证已成功。

## 2. 开发者、手机号与隐私

1. 在「成员管理」添加实际用于开发者工具登录的微信为开发者，并添加手机验收人员为体验成员。
2. 在新账号的能力管理中核对手机号快速验证能力、账号认证要求和可用额度；按后台提示开通。项目通过 `getPhoneNumber` 获取授权 code，并由服务器换取手机号。新账号权限不能沿用旧测试号的验收结论。
3. 完成「用户隐私保护指引」配置，按真实用途声明手机号、头像昵称，以及实际启用的上传图片、咨询、订单和奖品交付信息，填写运营方联系信息。项目登录页直接调用 `wx.openPrivacyContract` 展示平台指引。
4. 正式提审前完成后台要求的基础资料、类目、认证及小程序备案等项目。所需材料以该账号后台提示为准；本次未核验这些状态。

## 3. 企业微信客服绑定

2026-10-05 验收更新：已核验线上客服配置及正式 AppID 一致；用户确认测试版已绑定企业并能打开客服会话，企业微信接待人员收到咨询并回复，小程序用户可看到回复。双向接待通过，详见[接入与验收记录](WECOM_CUSTOMER_SERVICE_SETUP.md)。

在正式小程序后台「客服 → 微信客服」相关入口绑定企业 ID：

```text
ww9c709c084040a1af
```

完成平台要求的确认流程，核对显示“已绑定”。旧测试号曾返回 `openCustomerServiceChat:fail not bind`，更换 AppID 后仍需完成这一绑定。

线上默认客服名片已核对为「兴安电竞客服」，企业 ID 与上述一致，接入链接为：

```text
https://work.weixin.qq.com/kfid/kfc6445fdf249e1cefd
```

[运营后台客服名片](https://admin.xaesports.club/support-cards) 已配置，无需重复创建或填写企业 Secret。接待人员在企业微信「工作台 → 微信客服」确认分配、在线状态和接待权限。详见[客服接入说明](WECOM_CUSTOMER_SERVICE_SETUP.md)。

## 4. 上传体验版与手机验收

用于连接生产服务的开发者工具导入目录：

```text
G:\Github\XA\.tools\tencent-deploy\boss-cloud-project
```

该目录由 `scripts/prepare-cloud-miniapp.py` 生成，开发、体验、正式环境均使用生产 HTTPS；开发者工具中核对 AppID 为 `wxd63488bdf6491c90`，保持合法域名检查开启。源工程 `apps/boss-miniapp` 的 `develop` 仍用于本机 18080 调试，手机连接生产时应导入上述生成目录。

有开发权限的微信用户可在开发者工具中手动「上传」，随后在公众平台「版本管理」设为体验版并添加体验成员。需要自动上传时，在正式账号「开发设置 → 小程序代码上传」生成新上传密钥，保存到以下已被 Git 忽略的位置，并按平台要求配置上传机器的出口 IP：

```text
G:\Github\XA\.tools\tencent-deploy\private.wxd63488bdf6491c90.key
```

截至本次配置，该文件尚不存在，自动上传仍待提供新密钥。AppSecret 与代码上传私钥是两种不同凭证；旧账号上传私钥不能用于新 AppID。

手机依次验证：首页与图片加载、微信登录、手机号授权、头像上传、消息连接、打开企业微信客服及接待回复。体验验收通过后，再由运营方完成提审和审核通过后的发布。

## 旧测试账号

新 AppID 下的 OpenID 与旧账号不同。当前后端对“同手机号但已绑定其他 OpenID”的情况会拒绝自动改绑并提示联系客服。已有余额或订单的账号需核对归属后单独处理；本次没有自动清除绑定、合并账号、创建业务订单或修改资金数据。

## 官方入口

- [服务器域名说明](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html)
- [手机号能力说明](https://developers.weixin.qq.com/miniprogram/dev/framework/open-ability/getPhoneNumber.html)
- [微信客服小程序接入](https://developer.work.weixin.qq.com/document/path/94739)

本次文档检索工具未能读取上述官方页面正文。配置值以本项目源码和实际部署核验为依据；账号权限、后台菜单和审核要求以正式账号当前控制台提示为准。
