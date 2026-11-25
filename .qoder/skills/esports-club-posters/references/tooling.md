# 执行与续作

## 依赖

通过 `load_workspace_dependencies` 获得 Node、Node 包目录。优先用该 Node。`render.mjs` 从当前 Node 运行时旁的 `node_modules` 解析 Sharp；也支持 `--modules <绝对包目录>`。不安装额外图像生成 API。

字体转路径使用 Windows PowerShell 的 System.Drawing，不需要安装 Python/fontTools/Cairo。正文 Microsoft YaHei UI，数字 Bahnschrift，艺术字 Noto Sans SC Black。缺少字体应选择已安装替代并重新排版，不能声称字体完全一致。非 Windows 环境需改用当地可用的字体轮廓工具；现成 SVG 可直接复用。

## 命令

`--target` 取 `cover`/`ad`/`price`/`all`，用于 `init` 与 `layout`；`render` 与 `package` 跟随 `layout.json`，无需重复指定。`price` 用 `--platform desktop|mobile` 与 `--mode absolute|secret`（绝密/机密，也可写中文）收窄到单张。

```powershell
& '<Node绝对路径>' '<技能目录>/scripts/workflow.mjs' init --project '<新项目目录>' --target 'price' --platform 'desktop' --mode 'absolute'
& '<Node绝对路径>' '<技能目录>/scripts/workflow.mjs' layout --project '<项目目录>' --target 'price' --platform 'desktop' --mode 'absolute'
& '<Node绝对路径>' '<技能目录>/scripts/workflow.mjs' render --project '<项目目录>' --modules '<Node包目录>'
& '<Node绝对路径>' '<技能目录>/scripts/workflow.mjs' package --project '<项目目录>' --revision 'r2'
```

`layout` 只构建目标页与其字串，`init` 只复制该目标底图；`render` 与 `package` 跟随 `layout.json`，不会顺带生成其他页。`--only` 用于在多页布局内再收窄。

## 尺寸与格式

`render`/`package` 接受 `--size web|hd|print`（输出宽度 1080/1600/3200，默认 print）、`--width N`、`--height N`、`--scale 0.5`、`--format webp|png`（默认 webp）、`--quality N`（默认 88）。高度按底图原生比例计算；同时给定宽高且比例不符时拒绝导出，提示先换该比例的底图。导出宽超过底图原生宽时告警，应重新生成而非放大。

尺寸、格式与质量都进入页面指纹：改动后自动重导出，并删除同一页的旧格式文件。`--force` 会清空字形缓存并重新转曲，仅在本机字体变化时使用。

`--force` 仅用于确实需要重导出，例如本机字体发生变化；普通改价无需强制。新增字串只转曲一次。输出缓存指纹覆盖版面、底图和渲染器版本；已写入的文字边界用于检查长数字/单位是否挤占图标。

## 非 Windows 环境

转曲（`outline-text.ps1`）与打包（`pack.ps1`）依赖 Windows 的 System.Drawing 与 System.IO.Compression。macOS/Linux 下：

```bash
node <技能目录>/scripts/workflow.mjs init --project <新项目目录> --target cover
node <技能目录>/scripts/workflow.mjs layout --project <项目目录> --target cover
# render 需要 .cache/font-outlines.json 已有缓存，或先替换为本机字体轮廓工具
node <技能目录>/scripts/workflow.mjs render --project <项目目录> --modules <Node包目录>
# package 会在写完查看页后提示改用本机 ZIP；可直接打包项目目录
cd <项目目录> && zip -r campaign-r2.zip content.json campaign-state.json layout.json layout-adapter.mjs assets source output review
```

字体缺失时选择本机已安装的替代字体并重新排版，不得声称与原字体完全一致。已有转曲 SVG 可直接复用，不必重跑轮廓工具。

`package` 会按指纹补齐遗漏的改变页面，未变页面直接复用，以免只改完整版却漏掉广告。若本机需要 `--modules`，打包命令也传入该参数。

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
output/raster/            位图成品（默认 WebP，可 --format png）
output/svg-outlined/      标题/正文/数字转为矢量路径
output/svg-editable/      正文/数字为文字对象，标题为轮廓
output/manifest.json      页面索引、格式与真实原生/输出尺寸
review/                   页面预览（WebP）和文字边界
review/index.html         本地查看与下载页
campaign-<revision>.zip   成品、源数据与必要源文件
```

程序不发布到外部平台。打包只包含当前海报需要的输入、成品和查看页，不递归打包整个工作区。旧版本压缩包保留；不要把大尺寸 PNG 复制进技能作为每次必读资料。

## 最少操作的常见续作

- 只做封面：`--target cover`，不录入也不校验价目数据，只复制 `cover.png`。
- 改某档价格：改 `content.json` 对应值 → `layout --target price --platform … --mode …` → render → 查看改动行 → package。广告页引用同一档数据时，另跑 `--target ad`，不要为改一档重出全套。
- 只改机密两张：`--target price --mode secret`，得到端游与手游各一张。
- 所有 r/w 放大：改项目适配器的单位字号及固定列位置 → 按目标分别 layout/render → 检查最长数字行；广告四卡只在 `--target ad` 时核对。
- 新玩法共用旧画风：先提取内容并确认信息块数量；复用适合的角色，必要时只生成新情景或新底图；保存用户本轮确认后进入文字阶段。

`layout-material.mjs` 是物资参考适配器，不是业务数据库。其补偿、优惠和标题应全部从内容文件读取；新活动无需在脚本中搜改历史价格。
