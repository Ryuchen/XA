# 多端工程下的微信小程序交付核对（2026-11-02）

当前交付仅为微信小程序，项目保留官方多端工程结构，目标 iPhone 12 Pro；iOS/Android 仅筹备。当前源码包是 **20261102-190730**，纳入订单取消前的已核销券不返还提示；本版已完成实际IDE小程序模式普通编译与首页验收，准确范围见下表。没有上传、提审或发布。

## 当前源码包

- 文件：`.tools/wechat-miniprogram-delivery-20261102/兴安电竞-微信小程序-多端工程-20261102-190730-已验证.zip`，324360 bytes。
- SHA256：`3edbf5d7c3087b957102c32e4f8bf3274d54a0e71bff7b29fe8dd97117c0be9a`。
- 来源快照：`.tools/wechat-miniprogram-delivery-20261102/source-20261102-190730`；279来源文件逐项哈希与当前原项目一致，ZIP全部文件回读一致。
- 相对172643已验证版仅变化 `native/pages/order-detail/index.js`；收藏页样式和预约详情已填资料展示均保留。原AppID、API配置、`native/`入口与`compileType:miniprogram`、官方`projectArchitecture:multiPlatform`配置保持。解压后导入顶层工程目录，不直接导入native子目录。
- 不含后端、数据库、node_modules、私有配置或密钥。当前取消提示依赖配套后端的`cancel_coupon_nonrefundable`快照字段；真实HTTP回归已使用相应后端。多端App桌面名沿用户确认保留“声浪游玩社”，小程序业务界面仍为兴安电竞。
- 165123、171128、172643及172643“已验证”历史包全部保留。

## 当前快照验证

| 层次 | 结果 | 范围与限制 |
| --- | --- | --- |
| 结构/JS与资源预算 | 通过 | 50页、687.9KB；内置图片/音频30579bytes |
| 官方独立WXML/WXSS编译器 | 通过 | 54模板、57样式，非IDE整体构建 |
| 环境/登录代码契约 | 36/36通过 | 当前确切快照；SDK回调为明示夹具，不代表真实授权 |
| 原生控制器＋真实隔离HTTP | 2/2通过 | 当前确切快照，真实后端和数据库，覆盖取消退款/接单/开服/完单/评价；微信外部身份换码为夹具 |
| 本次资格券取消提示专项 | 通过 | 业务agent真实HTTP/控制器验收；包含历史老板身份、模板后改、实际退款和事后回执；普通券保持原提示。资格券与双陪券17项回归另由业务agent完成 |
| **当前快照实际IDE小程序构建/渲染** | **通过** | Stable2.02.2608080，小程序模式普通编译，面板显示analyzing codes success；实际首页loading=false/error为空、本地接口4玩法。iPhone12/13(Pro)390×844/DPR3/SDK3.15.2 |
| 微信上传/预览码/提审/发布 | 未执行 | 当前为可导入源码ZIP，不是已上传发布包 |

证据：`.tools/wechat-miniprogram-delivery-20261102/evidence-20261102-190730/`内`snapshot-static-compile.log`、`snapshot-runtime-tests.log`、`snapshot-wechat-http.log`、`qualification-cancel-notice-fixed.log`与`delivery-verification.json`。原项目此前202项源代码测试记录位于`.tools/wechat-multiplatform-check-20261102/original-adapter-combined-tests.log`，不冒充本轮全量重跑。

## 当前190730实际IDE验收

用户再次授权短暂暂停输入后，先复核190730的279来源文件与当前项目一致。复用34693工具服务，仅为190730独立项目启用19525专用自动化端口，无认证/信任阻碍。进入小程序模式，关闭首次下单须知，点击普通编译，构建面板显示`analyzing codes...`、`ignoring files: native/app.miniapp.json`、`analyzing codes success`。首页实际渲染并加载4玩法；系统回读与表格一致。验收后已最小化独立窗口释放前台，不再发前台输入，没有登录、上传或发布。

本版`ide-home.png`、`ide-runtime.json`、`ide-verification.json`位于`evidence-20261102-190730/`。存在工具预加载与弃用API告警，未宣称零警告或全页UI回归。新增取消确认由专项真实HTTP/控制器验证，此次IDE只验编译和首页，不用生产订单操作代替隔离验收。再次核对原项目及快照源码均未被IDE改变。新“已验证”包仅更新README/验收元数据，279来源文件与190730原包完全一致；旧包保留。

## 历史172643实际IDE验收

172643已在官方Stable2.02.2608080的小程序模式点击普通编译，构建面板显示`analyzing codes...`、`ignoring files: native/app.miniapp.json`、`analyzing codes success`。首页实际渲染，本地接口4个玩法，loading=false/error为空；首次下单须知实际点击关闭。系统回读iPhone12/13(Pro)、390×844、DPR3、SDK3.15.2。存在工具资源预加载和废弃API警告，不据此声称零警告。

历史证据保留于`evidence-20261102-172643/ide-verification.json`、`ide-runtime.json`、`ide-home.png`。当时复用34693工具服务，仅为该独立快照建立19524自动化端口，完成后最小化窗口释放前台，没有登录或上传。190730使用本次独立验收记录，不借用该历史结论。

## 后续可重复核验

不抢前台的当前快照静态检查：

```powershell
& 'C:\Users\chenh\AppData\Local\OpenAI\Codex\runtimes\cua_node\5c41f77a7900391c\bin\node.exe' '.tools/wechat-miniprogram-delivery-20261102/check-delivery.mjs' --require-compiler
```

[官方CLI V2](https://developers.weixin.qq.com/miniprogram/dev/devtools/cli.html)说明`open --project`会自动编译并打开模拟器，`auto`同样依赖工具和项目窗口；没有已确认的无IDE、无上传完整小程序编译命令。后续如有源代码变更需要重新实际IDE核验，应先协调前台、核对现有服务端口与独立项目；不自动重启、切账号、接受信任或认证。`build-npm`仅依赖构建，preview/upload不能作为本任务的无上传替代。

后续App移动关联、身份服务、OpenSDK/签名、安装包及真实授权/手机号/头像/客服尚未通过，也不是当前微信小程序交付前置；没有继续访问被策略阻止的官方控制台。
