# 兴安电竞老板端

微信小程序默认入口已经切换为 `native/`，使用原生 App / Page / Component、JavaScript、WXML、WXSS。无需 Taro、React 或 npm 构建产物即可导入微信开发者工具。现有 `src/` 保留为历史 Taro/H5 实现。

2026-11-02 技术路线：继续维护微信原生代码，目标是在微信开发者工具中迁移至多端应用模式；不使用 uni-app 或 HBuilderX 构建。已在独立副本通过官方工具升级为多端项目，并同步 `projectArchitecture: multiPlatform`、官方插件映射、`project.miniapp.json`、`native/app.miniapp.json` 和 `i18n/base.json`；`compileType: miniprogram` 与 `native/` 入口按官方生成方式保留。安装后的桌面 App 名称按用户确认沿用“声浪游玩社”，项目业务名称和现有界面仍为兴安电竞。App 登录、手机号/头像、环境选择、客服及实际安装包能力仍需逐项适配和验证，当前原生充值为联系客服，不包含在线支付 SDK；不能以小程序编译或配置升级代替 App 验收。详见 [多端核查记录](../../docs/ops/boss-multiplatform-readiness-20261102.md)。环境识别和登录/安全已有最小代码适配，详见 [环境与登录适配记录](../../docs/ops/boss-multiplatform-runtime-adapter-20261102.md)；当前没有实际 App SDK 授权通过结论。

2026-11-02 当前交付范围：使用多端项目开发，现阶段只打包/交付微信小程序，唯一目标机型为 iPhone 12 Pro。iOS/Android App 仅保留后续开发条件；移动应用关联、身份管理、App SDK 授权及安装包真机验证不作为本轮小程序交付前置，也未记为通过。

## 开发与验证

从微信开发者工具导入本目录 `apps/boss-miniapp`，项目配置指向 `native/`。本地后端默认 `http://127.0.0.1:18080/api`，连接 WSL 容器验证环境；真机使用可访问的 HTTPS 域名。启动方式和实际验收结果见 [本地部署记录](../../docs/LOCAL_DEPLOYMENT_VALIDATION.md)。

在仓库根目录运行：

```bash
pnpm boss:build
# 无需安装工作区依赖的等价检查
node scripts/check-native.mjs --require-compiler
node --test scripts/test-native-core.cjs
```

`boss:build` 检查完整页面四件套、JS 语法、依赖、路由、包大小，并在本机存在官方编译器时编译 WXML/WXSS。CI 可以设置 `WECHAT_COMPILER_DIR`；`--require-compiler` 要求官方编译器存在。代码更改由开发者工具直接重新编译，无额外打包或 npm 构建步骤。

小程序单张图片上限为 200,000 字节。素材先压缩再提供展示地址；商品图、活动图、头像和聊天图片使用不超过该上限的展示版本，原始凭证保留。小程序上传头像或聊天图片前自动压缩并复核大小，超限时不上传。首页品牌图也由构建检查逐张校验。

原生端共 50 个页面（含业务页与历史分享路径兼容入口），导航为首页、服务、订单、消息、我的。新增礼物、积分商城、幸运转盘、会员权益、奖品交付及活动海报入口，见 [会员运营说明](../../docs/CLUB_ENGAGEMENT_IMPLEMENTATION.md)。功能规划、接口和验收范围见 [原生老板端产品与联动说明](../../docs/BOSS_NATIVE_PRODUCT.md)。

## 环境与发布

- 配置 `native/config.js` 的 `develop` / `trial` / `release` 服务地址；当前开发环境连接本机 18080，体验版和正式版连接 `https://api.xaesports.club/api`。
- 小程序 AppID 必须与后端环境变量 `WECHAT_APPID`（Django 设置 `WECHAT_MINIAPP_APPID`）一致。AppSecret 仅存放于后端。
- 正式 AppID 为 `wxd63488bdf6491c90`。本机微信凭证保存在 Git 忽略的 `.env.wechat` 和当前用户加密的 `.tools/wechat.local.env.dpapi`；Compose 可通过 `--env-file .env.wechat` 读取。2026-09-30 已同步腾讯云及 WSL 开发后端，见[正式账号配置步骤](../../docs/WECHAT_FORMAL_SETUP_20260930.md)。
- 本机代码上传配置为 `.tools/tencent-deploy/wechat-ci.json`，使用生成的 `boss-cloud-project`，现已指向新账号的 `private.wxd63488bdf6491c90.key`，该新密钥尚未提供。原账号上传密钥不能用于正式 AppID。上传前运行 `scripts/prepare-cloud-miniapp.py` 刷新原生项目；也可由有权限的开发者在微信开发者工具中手动上传。上传密钥不进入小程序包、Git 或 Docker 构建上下文。
- 微信后台配置 request / socket / uploadFile / downloadFile 合法域名、隐私保护指引与客服能力。
- 后端配置真实微信登录，关闭 `WECHAT_MOCK_LOGIN`，启用生产数据库、Redis 和 Celery 工作进程。
- 上线本轮后端前执行 `python manage.py migrate`，新增 `users.0027_wechatphonelock` 保证手机号并发绑定的归属检查。
- 根目录 `pnpm release:check` 检查原生正式环境地址、项目入口、有效域名校验开关、AppID 和陪玩 H5 API。体验版使用 `node scripts/check-release-config.mjs --target=trial`，不会回退正式地址。
- `project.private.config.json` 会覆盖公共项目配置；本地 HTTP 联调可按需关闭域名校验，上传体验版/正式版前须重新开启。发布检查只检查配置格式，不验证域名可达性、平台权限或真实登录。
- `pnpm build:release` 要求官方 WXML/WXSS 编译器通过；缺少编译器时配置 `WECHAT_COMPILER_DIR`，不能将仅结构检查通过视作正式构建通过。
- `deploy:weapp` 只执行本地校验，不上传、不发布；上传体验版在开发者工具中操作。

## 保留的历史构建

`pnpm boss:build:h5` 仍构建 H5；`pnpm --filter xa-boss-miniapp build:legacy:weapp` 仍可生成历史 Taro 小程序用于对照。原生小程序始终从 `native/` 加载，不依赖这些产物。
