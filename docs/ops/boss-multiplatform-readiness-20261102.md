# 老板小程序多端迁移核查（2026-11-02）

当前范围已由用户明确调整：多端工程仅交付微信小程序，iOS/Android App 尚在筹备。下文App前置条件只作后续开发记录，不阻塞当前小程序交付；不再尝试访问已被内置浏览器策略阻止的官方控制台。

当前继续维护 apps/boss-miniapp/native 的微信原生代码，未引入 uni-app/HBuilderX。用户完成绑定后，已在独立副本通过官方工具升级，并在真实回归后只同步必要多端配置到原项目；项目仍从 native/ 加载，compileType 按官方方式保留 miniprogram，新增 projectArchitecture=multiPlatform。App 平台适配及安装包验收尚未完成。以下早期状态按过程保留，最新结果见文末“配置同步与验收结果”。

## 官方当前文档核对

旧 dev.weixin.qq.com/docs/framework 页面已提示不再维护，现行入口为 [小程序多端框架](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/intro/)。本次使用直接 HTTPS 获取并解析官方正文，日期 2026-11-02。

- [升级多端项目](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/handbook/devtools/miniproject.html)：小程序需先绑定多端应用，使用开发者工具“工具→升级为多端项目”。升级标记是 project.config.json 新增 projectArchitecture: multiPlatform，另生成 project.miniapp.json；不能靠修改 compileType 冒充升级。
- [配置文件](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/pre-read/config.html)：SDK、Open SDK/Network 等扩展能力、图标、启动界面及平台配置各自设置，不能猜测应用 ID、签名或证书。
- [wx.login 适配](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/api/diffapi/wx.login.html)：wx.login 在 App 不适用，应根据登录方案采用官方适配 API 并核验后端身份关联。
- [需适配组件](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/component/adapt_component.html)：button 的 getPhoneNumber/chooseAvatar/contact 等微信开放能力在 App 不直接支持。
- [App 支付](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/api/miniapp/requestPayment.html)：wx.miniapp.requestPayment 依赖 Open SDK、移动应用与多端应用/商户绑定，参数不同于小程序支付；必须安装构建包真机验收。
- [App 微信客服](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/api/miniapp/openCustomerServiceChat.html)：接口为 wx.miniapp.openCustomerServiceChat({corpId,url})，需移动应用认证/关联；与现有小程序调用参数不同。

## 现有代码逐项风险

| 能力 | 现有位置 | 多端验收要求 |
|---|---|---|
| 登录 | native/core/auth.js 的 wx.login；pages/login | 保留小程序登录分支，App 登录及已有账号关联独立核验 |
| 手机号 | pages/login/index.wxml、pages/security/index.wxml 的 getPhoneNumber | App 使用受支持的手机号登录/绑定方案，验证旧账号归属与取消/失败 |
| 头像 | pages/profile/index.wxml 的 chooseAvatar | App 分支选择头像/授权与上传，保留尺寸和隐私规则 |
| 客服 | pages/support/index.js 的 wx.openCustomerServiceChat | 小程序参数不直接复用 App API，真实微信拉起与失败退路需验收 |
| 长连接 | native/core/realtime.js 的 wx.connectSocket | 核对 Network SDK，验收登录/重连、退后台恢复、会话切换 |
| 媒体 | native/core/api.js 的 uploadFile、pages/chat 的 chooseMedia | 核对平台 SDK/权限/隐私声明、取消与超限失败 |
| 付款 | 当前 checkout 余额支付及其他支付入口 | 不因 App 接入改变当前余额/订单链路；App 支付需后端契约、商户权限和真机专项 |

目前缺少已核验的多端应用绑定、生成配置、目标平台 SDK/签名及 App 真机运行证据。本轮没有贸然生成无法验证的配置或改写平台接口；小程序结构/编译检查只证明现有小程序，不代表 App 登录、支付或客服已迁移。后续应在微信开发者工具核对绑定，生成官方配置后保留小程序分支逐项适配并真机验证。

当前验收目标：按用户最新要求，原生微信小程序仅以 iPhone 12 Pro 为基准；后台和陪玩 H5 继续覆盖各自桌面与移动尺寸。

## 实际开发工具核查补充

已实际打开 Stable 2.02.2608080。项目列表有“多端应用”入口；导入原生项目的开发模式下拉只有“小程序/插件”，旁边有多端“去体验”入口。当前使用独立副本 `.tools/wechat-multiplatform-check-20261102`，其全部环境 API 均改指向未运行的127.0.0.1:13996，未使用原项目窗口编译业务。

尝试用原 AppID `wxd63488bdf6491c90`、不使用云服务导入副本时，工具明确显示 **Error: 需要重新登录**。因此当前无法读取该AppID的多端绑定状态；不能将这个错误写成“账号未绑定”，更不能据此声称升级已完成。没有自动化用户登录、改变账号绑定或项目模式。已有“评价验收隔离”和“XA-UI-Native-Support-Isolated”项目保持原状；导入已取消。观测记录 `.tools/wechat-multiplatform-check-20261102/observations.json`，CLI端口失败记录 `open.log`。

可执行前置条件：由用户重新登录开发工具后，复查该AppID的多端应用绑定与管理员/开发者权限；在独立副本使用官方“工具→升级为多端项目”流程，核对生成配置而非手填标记；准备移动应用/OpenSDK/商户及客服的实际关联信息。登录、手机号/头像、支付/退款、客服和网络能力需各自验证；本轮未越过这些外部前置条件，也未以小程序编译代替多端验收。

工具运行补充：尝试独立user-data-dir启动未得到可用项目窗口，已停止本次启动的91980进程，两个既有项目与自动化端口保留；官方CLI自探测记录指向已无服务的34693，显式--port63944只读islogin仍走此过期记录并超时。未编辑共享安全/端口配置，相关日志同目录login-status.log；已有自动化连接仍可用，后续由主流程统一处理工具登录/服务，避免打断并行验收。

## 用户重新登录后的最新状态（优先于上节登录障碍）

用户自行完成登录后，原 AppID 已能成功导入独立副本，窗口标题 `XA-MultiPlatform-Readiness-Isolated`。模式仍为小程序；未执行升级、未生成正式多端项目配置、未改变账号绑定。原生主页可显示不等于多端验证通过。随后按主流程要求暂停窗口自动化，让用户继续使用电脑；绑定状态仍待在明确的窗口时段读取，不把“导入成功”推定为“已绑定”。

现有 API 盘点保存在 `native-api-inventory.json`。高影响改动点进一步核对如下：

| 能力 | 现有实现 | 官方差异与后续验证 |
| --- | --- | --- |
| 现有老板身份 | core/auth.js:56 loginCode、pages/login/index.js:15、pages/security/index.js:9 | `wx.login`只适用于微信小程序。官方[登录兼容方案](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/new-capability/auth/loginCompatible.html)提供`wx.getMiniProgramCode`/`wx.weixinMiniProgramLogin`，有机会保留现有服务端code验证；需先确认绑定、登录Page和授权页配置，并验证现有账号/钱包/订单归属不产生第二个身份。不能只把函数名直接替换后声称通过。 |
| API环境与诊断开关 | core/config.js:4、core/ui.js:34、pages/support/index.js:6 | 官方[getAccountInfoSync差异](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/api/diffapi/getAccountInfo.html)说明App中的miniProgram.envVersion恒为release且无效；新版SDK增加miniapp.envVersion，host/miniapp还提供多端与资源包ID。现有按miniProgram.envVersion选择配置及诊断开关需适配，否则App调试可能选中release服务。独立副本已将所有环境指向本地不可用端口隔离。 |
| 手机号/换绑 | login和security的getPhoneNumber按钮 | App不支持该open-type；官方有本机号码登录等新能力，但不能把新的手机号身份自动映射既有老板账号。需按当前绑定及恢复账号语义验证，保留用户拒绝授权/已有余额提示。 |
| 头像 | profile/index.wxml chooseAvatar 与 profile/index.js:16 | App不支持该open-type。需选择官方支持的账号头像或图片选择上传方案，验证授权拒绝、文件读取/压缩、私有图片上传及会话切换保护。 |
| 钱包充值/支付 | wallet/index.js:46 recharge转到support?scene=recharge；core/api.js fund负责余额业务幂等 | 当前原生代码没有wx.requestPayment或wx.miniapp.requestPayment调用；现行充值是联系客服流程。不能把App支付SDK接入列为已存在逻辑的简单替换，也不应擅自改成在线支付。App支付是未来若明确启用时需要商户/OpenSDK/安装包联调的独立链路。 |
| 微信客服 | support/index.js:36 | 小程序参数使用extInfo.url和消息卡片，App官方API是wx.miniapp.openCustomerServiceChat({corpId,url})，并依赖移动应用与企业绑定。保留现有平台客服、复制咨询编号等备选和失败反馈；不可在未绑定时触发真实客户外发或谎称打开成功。 |
| HTTP、上传和实时 | core/api.js wx.request/uploadFile；core/realtime.js wx.connectSocket | 在多端实际包验证请求/证书、文件路径、过期续签、WS心跳重连及HTTP权威回读；当前会话修订、资金请求幂等和未知结果核对不能因平台分支丢失。 |
| 其他界面能力 | 图片预览、音频、文件系统、剪贴板、canvas、页签角标与导航 | 逐项以实际SDK/安装包验证；图像长按微信菜单在App有差异。现有原生iPhone12Pro交互基线继续保留，不能用后台/H5替代。 |

官方文档已通过直接HTTPS重新取得并提取正文，存于独立副本 `official-docs/`；其中包含升级指南、登录兼容、账号信息、组件适配、客服、支付及配置。web工具抓取官方站点失败后使用此方式核对。仅做源码/文档准备，尚未对上述App链路写入或验收完成结论。

## 最新实际绑定核查结果

用户提供短暂窗口操作时段后，仅在 `XA-MultiPlatform-Readiness-Isolated` 选择“工具→升级为多端项目（新）”。官方弹窗标题为“不满足切换条件”，说明此小程序尚未绑定为多端应用的开发小程序；继续将要求接受Donut开发者服务协议和多端SDK Beta条款，并快速创建多端应用、绑定该小程序。

据此，当前可明确记录**该AppID尚未完成此多端绑定前置条件**。本次没有勾选协议或点击确定，已取消并最小化独立窗口，立即释放前台。独立副本和原项目的project.config仍为miniprogram，未新增projectArchitecture或project.miniapp.json；没有外部账号创建、绑定或项目模式变更。后续需要用户确认并完成协议/多端应用创建关联，或由官方控制台先完成绑定，再回到独立副本重试官方升级。原生App链路适配与真机验收仍按上表执行。

## 配置同步与验收结果（当前）

用户自行完成多端绑定后，在独立副本选择官方“工具→升级为多端项目（新）”，工具进入多端应用模式并生成配置，没有再次要求创建账号或接受绑定协议。本轮未代替用户创建外部账号或修改外部绑定。

桌面名称的来源和作用：官方[配置文档](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/pre-read/config.html)说明，升级初始名称取自多端应用控制台创建时填写的名称，用作安装后手机桌面的App名称，不能等同于本地项目名或直接推定为小程序注册名称。官方[多语言文档](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/handbook/devtools/localization.html)说明，`%name%`按i18n目录解析，base.json是没有语言覆盖时的基准。此次生成值为“声浪游玩社”，用户随后明确确认沿用，故ios/android/common全部保留该值。本地项目名、导航标题和AppID不随桌面名称变更。

同步文件仅如下四项，未覆盖业务JS/WXML/WXSS、原服务地址或用户已有字段：

- `apps/boss-miniapp/project.config.json`：仅新增`projectArchitecture: multiPlatform`与官方生成的模拟器插件映射`wxext14566970e7e9f62: 2.27.3`；原AppID、项目名、miniprogramRoot、compileType、settings和condition均保留。没有同步工具自动调整的condition值。
- `apps/boss-miniapp/project.miniapp.json`：官方生成SDK1.7.0、name占位、资源目录等基础配置，保持原始生成值。默认空图标/启动图和扩展SDK选择仍需安装包阶段逐项配置，尚未声称可发布App。
- `apps/boss-miniapp/native/app.miniapp.json`：保留官方生成的adapteByMiniprogram.userName。该值为工具生成的关联元数据，不是桌面名称，也没有用本地项目名替换；本轮未对其服务端归属另作推断。
- `apps/boss-miniapp/i18n/base.json`：保留用户确认的桌面名称。

同步前副本在`.tools/wechat-multiplatform-check-20261102/before-original-config-sync/`。哈希核对证明同步前已有native文件均未改变，原开发/正式API配置不含隔离13996地址。官方升级自身只修改副本project.config并新增上述三个文件；精确文件变动在official-upgrade-diff.json。

验证分层如下，不把各层合并成App已完成：

1. 官方工具实际进入多端应用模式，模拟器显示原生主页；尚无iPhone App安装包/设备连接证据。
2. 升级副本与同步后的原项目均通过官方编译器：50页、54个WXML、57个WXSS；日志compiler-check.log、original-compiler-check.log。此项仅证明结构与编译。
3. 原生166个现有前端测试通过，日志native-unit-tests.log。
4. 副本与同步后的原项目各执行两组真实LiveServer原生页面控制器HTTP回归，覆盖页面读取、下单扣款、丢响应幂等恢复、接单、开工/完工凭证、报单审核、售后窗口待结算、评价、取消退款、聊天和邀请切换身份，数据库断言通过。日志controller-http-regression.log、original-controller-http-regression.log，驱动同目录。核心业务未mock，wx传输映射真实本地HTTP；外发队列与外部微信登录代码交换隔离，真实微信登录未调用。复用旧驱动时仅在独立副本适配现行评价失败提示文案，状态与数据库断言未删减。这组是页面控制器/HTTP证据，不等同App SDK原生能力或全页面真机交互。

App未完成项：miniProgram.envVersion在App无效，需按实际SDK改环境选择；微信登录/手机号/头像需平台适配及既有身份归属回归；客服需OpenSDK与企业绑定实际联调；上传、音频、WebSocket等扩展SDK及权限须用安装包逐项验证；图标、启动图、签名和安装/发布配置尚未配置验收。当前原生充值是联系客服流程，未擅自引入微信支付SDK。用户唯一原生目标机型继续为iPhone12Pro；没有App真机支付、退款或外发客服通过结论。

## 后续代码适配进展（当前）

环境识别与登录/安全最小代码已在独立副本完成文档核对、实际模拟器环境探针、202项测试及6组真实HTTP契约后精确同步到原项目。实际App SDK授权仍未完成，详见 `boss-multiplatform-runtime-adapter-20261102.md`；前文“环境选择尚未适配”等描述为该阶段之前的状态，不再覆盖本节。
