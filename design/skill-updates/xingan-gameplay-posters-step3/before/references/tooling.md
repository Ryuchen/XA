# 执行与续作

## 依赖

通过 `load_workspace_dependencies` 获得 Node、Node 包目录。优先用该 Node。`render.mjs` 从当前 Node 运行时旁的 `node_modules` 解析 Sharp；也支持 `--modules <绝对包目录>`。不安装额外图像生成 API。

字体转路径使用 Windows PowerShell 的 System.Drawing，不需要安装 Python/fontTools/Cairo。正文 Microsoft YaHei UI，数字 Bahnschrift，艺术字 Noto Sans SC Black。缺少字体应选择已安装替代并重新排版，不能声称字体完全一致。非 Windows 环境需改用当地可用的字体轮廓工具；现成 SVG 可直接复用。

## 命令

```powershell
& '<Node绝对路径>' '<技能目录>/scripts/workflow.mjs' init --project '<新项目目录>'
& '<Node绝对路径>' '<技能目录>/scripts/workflow.mjs' layout --project '<项目目录>'
& '<Node绝对路径>' '<技能目录>/scripts/workflow.mjs' render --project '<项目目录>' --only 'desktop-secret,social' --modules '<Node包目录>'
& '<Node绝对路径>' '<技能目录>/scripts/workflow.mjs' package --project '<项目目录>' --revision 'r2'
```

`--force` 仅用于确实需要重导出，例如本机字体发生变化；普通改价无需强制。新增字串只转曲一次。输出缓存指纹覆盖版面、底图和渲染器版本；已写入的普通文字边界可辅助检查长数字/单位。艺术字、描边/阴影及插画主体没有完整边界记录，仍需按 [成图检查流程](final-visual-qa.md) 补测或查看实际输出，不能把边界文件当作视觉验收结果。

`package` 会按指纹补齐遗漏的改变页面，未变页面直接复用，以免只改完整版却漏掉广告。若本机需要 `--modules`，打包命令也传入该参数。打包前完成成图复检；若打包期间新增或重渲染了页面，交付前补查这些实际成图，并确保包内文件与检查版本一致。程序自动写入 `stage=delivered` 不表示视觉检查通过，以项目 `review/visual-qa.md` 的实际检查记录为准。

`layout` 根据项目 `content.json` 调用项目自己的适配器，保存布局；`render` 不隐式覆盖该布局。每次只改文字时先改内容再 layout；手工改布局后直接 render。长期布局改动同时改项目适配器，避免下一次生成回退。

## 项目结构

```text
content.json              唯一业务数据源，附本轮来源与核对记录
campaign-state.json       阶段、确认依据、contentVerified、当前版本
layout-adapter.mjs        可修改的当前玩法适配器
layout.json               渲染计划（文字请求、元素、画布与输出尺寸）
assets/backgrounds/      生成原始底图
source/                   本机转曲脚本，留作重建依据
.cache/                   字体路径和页面指纹；不要作为用户成品重复打包
output/png/               高清最终图
output/svg-outlined/      标题/正文/数字转为矢量路径
output/svg-editable/      正文/数字为文字对象，标题为轮廓
output/manifest.json      页面索引与真实原生/输出尺寸
review/                   页面预览、普通文字边界和成图复检记录
review/visual-qa.md        人工记录检查版本、整图/手机/局部预览及修复复查结论
review/index.html         本地查看与下载页
campaign-<revision>.zip   成品、源数据与必要源文件
```

程序不发布到外部平台。打包只包含当前海报需要的输入、成品和查看页，不递归打包整个工作区。旧版本压缩包保留；不要把大尺寸 PNG 复制进技能作为每次必读资料。

## 最少操作的常见续作

- 改某档价格：改 `content.json` 对应值 → layout → render `--only 对应海报,social` → 查看改动行 → package。
- 所有 r/w 放大：改项目适配器的单位字号及固定列位置 → layout → render → 检查最长数字行与广告四卡 → package。
- 只改机密表头：改表头矢量元素，不调用图像模型；输出两张机密页面，若广告对应结构也变则同步。
- 新玩法共用旧画风：先提取内容并确认信息块数量；复用适合的角色，必要时只生成新情景或新底图；保存用户本轮确认后进入文字阶段。

`layout-material.mjs` 是历史物资参考适配器，不是已满足当前视觉规范的成品。`init` 复制后，需在项目 `layout-adapter.mjs` 中落实封面去除例外、各文字组居中、须知正文加粗放大及空间重排；只改 `layout.json` 时避免再运行 `layout` 覆盖。它也不是业务数据库。其补偿、优惠和标题应全部从内容文件读取；新活动无需在脚本中搜改历史价格。
