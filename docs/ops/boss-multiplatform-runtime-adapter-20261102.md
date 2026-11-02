# 多端环境与登录代码适配（2026-11-02）

当前范围已由用户明确调整：多端工程仅交付微信小程序，iOS/Android App 尚在筹备。下文App前置条件只作后续开发记录，不阻塞当前小程序交付；不再尝试访问已被内置浏览器策略阻止的官方控制台。

当前已完成代码适配并同步；实际App SDK授权、移动应用关联、授权页发版和安装包验证尚未完成。配置升级、模拟器字段、代码契约与真实后端HTTP是不同层次的证据，不能合并为App登录已通过。

## 精确文件范围

- 新增 `apps/boss-miniapp/native/core/runtime.js`：统一识别官方SAAASDK宿主及App账号ID，读取App资源环境；未知环境阻断，避免把App无效的miniProgram.release当成生产环境。
- `core/config.js`：按已确认环境选择已有配置，环境缺失或该环境未配置时不回退到release；体验/正式HTTPS要求保留，未改任何地址。
- `core/ui.js`、`pages/support/index.js`：诊断信息使用相同环境判断；正式环境不显示SDK详情。
- `core/brand-assets.js`：环境错误时返回空插画地址，页面仍可显示服务错误与重试，不在Page初始化时崩溃。
- `core/auth.js`、`pages/login/index.{js,wxml}`、`pages/security/index.{js,wxml}`：微信保留wx.login；App按下面顺序获取兼容凭证。取消/失败不改会话，不发后端请求。App没有getPhoneNumber能力，隐藏该授权入口并说明应在微信小程序管理手机号，控制器也阻断不支持的手机授权事件；保留现有checkOnly、明确新建账号确认及后端身份校验。
- 新增 `scripts/test-native-multiplatform.cjs`：36个环境与平台回调契约测试；不替代SDK真机授权。

同步采用逐文件哈希检查：先确认目标仍与适配前基线一致，再精确合入9个已有文件及新增runtime模块。没有整目录覆盖，没有回写order-detail、API/fund业务逻辑或其他agent页面。同步前文件在 `before-runtime-original-sync/`，已同步差异 `runtime-original-synced.diff`；登录5文件独立审阅差异 `auth-login-security.diff`。原生AppID、桌面名称、业务品牌、现有用户字段与接口未改变。

## 官方登录依据和凭证类型

官方[自定义登录页方案](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/new-capability/auth/loginCompatible.html)的顺序是先唤起小程序授权，再取得兼容小程序凭证。当前实现：

1. App用户在现有登录页明确操作后调用 `wx.weixinMiniProgramLogin`。
2. 成功后调用 `wx.getMiniProgramCode`。
3. 仅将后者返回的小程序code交给已有 `/users/wechat-login/` 或 `/users/wechat-bind/`；既有服务端code2session、checkOnly与身份归属规则继续执行。

[weixinMiniProgramLogin接口](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/api/auth/wx.weixinMiniProgramLogin.html)返回的身份管理凭证用于code2Verifyinfo，要求多端应用AppID/secret；该凭证被本实现明确丢弃，不能送进现有小程序code2session。[getMiniProgramCode接口](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/api/auth/wx.getMiniProgramCode.html)返回兼容小程序code，官方[快速接入指南](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/quickstart/auth.html)说明可沿用现有小程序后台获取openid/unionid；这不授权前端猜测、合并或重建账号。

两个App阶段任一取消/失败/缺API都会停止。迟到的授权成功回调不能在取消后恢复登录，重复成功回调也只采集一次兼容code。微信分支不调用这两个App API。

## 实际模拟器发现与修正

[官方getAppBaseInfo差异文档](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/api/diffapi/getAppBaseInfo.html)给出宿主标记 `host.env=SAAASDK`。[getAccountInfo差异](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/api/diffapi/getAccountInfo.html)说明App的miniProgram.envVersion无效，新SDK另有miniapp.envVersion。

独立副本的实际SDK模拟器探针（没有前台输入、没有调用登录/业务接口）发现：该模拟器返回SAAASDK和device.platform=devtools，但没有App账号ID或miniapp.envVersion，只有miniProgram.envVersion=develop。仅按账号ID识别会误判微信；现已修正为识别SAAASDK。只有“SAAASDK + devtools + 明确develop”这一实际模拟器形状允许使用开发地址；绝不使用模拟器miniProgram的trial/release选择服务。真机缺少资源环境、宿主信息读取失败或未知环境均阻断请求。

第三次实际探针确认 platform=app、envVersion=develop、手机号授权能力false、服务地址为隔离13996。探针结果在runtime-probe-{2,3}-result.json，临时App钩子和probe模块已移除；没有读写其他项目窗口、用户身份或生产地址。这是模拟器环境识别证据，不是安装包能力证据。

## 验证结果与边界

- 独立副本与同步后原项目：166个原生基线+36个新契约，共202项通过。日志original-adapter-combined-tests.log。
- 官方编译：50页、54WXML/57WXSS通过，日志original-adapter-compile.log。
- 微信字段、App账号字段、实际SAAASDK模拟器字段三种形状，各2组真实LiveServer完整业务链路，共6组通过，日志original-adapter-http-{wechat,app,app-simulator}.log。页面控制器/HTTP/数据库执行真实代码，覆盖下单/幂等恢复/扣款/退款/履约/报单审核/待结算/评价/聊天/邀请。平台授权和code回调用明确fixture，外部微信凭证校验返回已知隔离身份；后端身份映射、钱包与订单规则没有mock。测试断言送后端的是兼容code，而不是授权阶段的另一类code。
- 取消/失败、缺SDK、空凭证、未知环境与生产HTTP拒绝由契约测试覆盖；这些是代码契约，尚无实际SDK授权/取消界面通过结论。
- 原项目业务代码中没有13996、example.invalid或临时probe地址；该阶段没有修改API配置、真实账号或资金。

## 尚未完成

当前app.json未配置miniApp.useAuthorizePage，app.miniapp.json尚无identityServiceConfig；官方要求的身份管理服务、移动应用绑定及相应小程序授权页/发版尚待核对准备。实际App SDK成功授权、取消与既有账号归属必须使用安装包验证；官方还指出移动应用助手不能替代独立安装包。手机号/头像、隐私指引、客服、扩展SDK和签名/安装流程也仍待完成。没有运行真实微信账号登录、生产支付或外发客服会话；原生当前充值仍是联系客服，未擅自加入支付SDK。
