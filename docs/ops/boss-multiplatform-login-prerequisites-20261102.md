# App登录前置条件与可审阅配置草案（2026-11-02）

当前范围已由用户明确调整：多端工程仅交付微信小程序，iOS/Android App 尚在筹备。下文App前置条件只作后续开发记录，不阻塞当前小程序交付；不再尝试访问已被内置浏览器策略阻止的官方控制台。

当前状态：多端项目升级与环境/登录代码适配已完成；实际App SDK登录尚未验收。以下草案未应用到原项目，没有开通服务、接受协议、创建/解绑移动应用或触发真实登录。

## 已知信息与尚未证明的状态

| 项目 | 当前证据 | 下一步 |
| --- | --- | --- |
| 小程序AppID | 原项目为wxd63488bdf6491c90，官方升级已接受其绑定关系 | 保持，与后端现行小程序凭证校验配置一致 |
| 小程序原始ID | 工具生成adapteByMiniprogram.userName=gh_5581b521bf23 | 保留官方值，不由展示名反推AppID |
| 多端桌面名称 | 用户确认声浪游玩社，%name%指向i18n/base | 保留；这不是多端应用ID或移动应用AppID |
| 多端应用ID | 当前生成文件/模拟器没有提供可据此确认的ID | 可在已登录官方控制台“应用概览”只读查看，核对所选应用及关联小程序 |
| 移动应用关联 | 未读取、不能由多端升级成功推断 | 控制台“移动应用信息”只读查是否已关联、移动应用AppID和平台字段 |
| 身份管理服务 | 状态未知 | 控制台“身份管理”查是否开通，是否达到“可使用” |
| iOS OpenSDK | 工具生成WeAppOpenFuns=true | 本地配置已选中；不代表移动账号/BundleID或真机SDK已可用 |
| Android OpenSDK | 生成配置没有open=true | 若本轮构建Android登录，需选择该扩展；准备了可选diff，未应用 |
| 小程序授权页配置 | 当前app.json没有miniApp.useAuthorizePage | 优先由官方工具配置，草案可审阅 |
| App身份服务配置 | 当前app.miniapp.json没有identityServiceConfig | 准备指向既有登录页的草案；不把写JSON当作服务已开通 |
| 安装包与设备 | 未连接App真机、未生成并安装本轮验证包 | 有效签名/测试设备和安装包联调仍待准备 |

## 官方流程与动作边界

依据[快速接入小程序登录](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/quickstart/auth.html)、[移动应用绑定](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/handbook/web/bind-openapp.html)及[身份管理控制台](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/handbook/web/auth_mgnt.html)：

1. 在多端应用详情只读核对已有移动应用关联。若已存在，应复用其真实AppID和平台信息；若未存在，需要有权限的用户在官方平台完成注册/审核或绑定。iOS BundleID、Android包名会从移动应用信息同步，不在本地编造。若只有单平台信息，不能宣称另一端已具备能力。
2. 只读核对身份管理状态。未开通时，由用户在对应多端应用的“身份管理”完成服务协议与开通；小程序绑定完成不等于身份管理或移动应用关联完成。已有开放平台账号的认证/应用审核条件按官方页面实际状态执行。
3. 账号前置满足后，在独立副本执行“工具→配置小程序登录服务”。先审阅工具展示的作用及变更，确认将生成本地授权页/身份配置；本地差异与下列草案对照。服务开通、账号关联和协议接受不由本地JSON代替。
4. 配置自定义App登录页为现有 `/pages/login/index`。开发验证草案使用authorizeMiniprogramType=1，仅对应开发版小程序；不将它默认当正式App发布配置。正式/体验目标需单独核对对应小程序版本。
5. 切回小程序模式，重新编译/预览，由具有相应权限的测试微信扫码，使包含官方授权页的新版本可用。仅写配置、仅编译WXML或只升级App资源不能证明授权页已存在于手机实际访问版本。
6. 构建并安装实际App，按“App主动授权→跳微信小程序官方授权页→返回App→getMiniProgramCode→隔离后端”的链路，验证成功、用户取消、SDK失败和账号归属。官方明确移动应用助手不能替代独立安装包验证。

只读核验可查看已有ID、绑定关系、平台字段、服务状态和工具提示；不点击开通/确认协议/创建/更换/解绑、不修改签名设置。用户操作包括平台扫码登录、必要服务条款接受、缺失的移动账号关联及实际测试授权。若已有信息能在控制台读取，就不要求用户重复抄写。

## 已有资料与确需补齐的信息

- 优先读取：多端应用ID、关联移动应用AppID、iOS BundleID及是否已审核/绑定、身份管理状态。若构建Android，再核对包名与开放平台所需的应用签名信息。
- 用户需完成或提供可用本地资料：缺失的平台注册/审核/关联操作；用于构建安装包的已有签名配置及测试设备；具备开发版/体验版访问权限的专用测试微信。证书、密码和私钥不要求贴到聊天或前端配置。
- 当前兼容方案使用getMiniProgramCode并沿用已有小程序后端校验；不需要把多端Secret送到前端，也不改用code2Verifyinfo。Apple登录、短信登录不是本轮桥接方案的必要新增功能，不据此要求新建Apple登录密钥或短信配置。
- 实际SDK登录验证需要明确安排用户测试授权及隔离后端；继续不使用生产钱包/订单、真实客户或外发消息作为验收。

## 配置草案与验证

草案目录 `.tools/wechat-multiplatform-check-20261102/next-login-setup/`：

- `native/app.json`：保留当前业务页面与其他字段，只新增miniApp.useAuthorizePage=true。
- `native/app.miniapp.json`：保留工具关联元数据，新增identityServiceConfig.authorizeMiniprogramType=1与miniprogramLoginPath=/pages/login/index。
- `authorization-page.proposed.diff`：上述两文件精确可审阅差异。
- `optional-android-opensdk.proposed.diff`：仅在需要Android登录时启用open扩展的可选差异。
- `checks.json`：已检查JSON结构、登录路由存在、现有SDK选择。未应用、未开通服务、未证明授权页或安装包可用。

不确定项仍保留为未知，尤其移动应用绑定、身份管理“可使用”、安装包签名及SDK真实回调。下一步应先只读核对官方控制台；发现缺项后再让用户完成具体那一步。

## 官方控制台准确入口与只读核查顺序

官方控制台入口为 https://developers.weixin.qq.com/platform/ ，来源是[控制台操作指引](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/miniapp/handbook/web/login.html)、移动应用绑定和快速登录文档的实际链接；没有猜测账号相关后台深链。

- 若浏览器显示扫码登录，停在该页交由用户完成；工具登录状态不等于内置浏览器登录状态。
- 若进入创建应用页面，不填写或提交。官方说明无可见应用时可能自动进入创建页，但当前AppID已通过工具升级证明存在绑定；应核对登录微信、空间及目标小程序管理员/开发者权限，不能直接推定原绑定丢失。
- 在已有应用选择器确认目标“声浪游玩社”，并核对关联小程序AppID为wxd63488bdf6491c90。展示名不代替ID核对。
- “应用概览”：记录多端应用ID、名称及关联小程序；不展开Secret。
- “移动应用信息”：读取明确绑定状态、移动应用AppID、iOS BundleID及Android包名是否齐全、可见的审核状态。不要点击创建、立即绑定、更换或解绑。
- “身份管理”：读取未开通/已开通但不可用/可使用的明确状态，以及小程序登录是否已配置。不要点击开通、接受协议或保存新配置，也不用展开不在本方案范围的Apple/短信配置。

SDK本地事实独立记录：打包SDK iOS/Android均1.7.0，iOS OpenSDK已选，Android未选；模拟器插件2.27.3不是打包SDK版本。API方法存在或模拟器能显示页面不证明移动应用关联、身份服务或安装包授权成功。

本轮只读工具库存发现微信开发者工具已无运行进程，也无可连接浏览器provider，仅有Chrome模板页面窗口。按主流程要求未重启、未切换用户窗口；控制台真实状态继续保留未知，等待主流程用内置浏览器读取后补齐。
