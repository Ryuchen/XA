# Skills 用法手册

本目录存放项目级技能，当前只有 `esports-club-posters`（电竞俱乐部海报制作）。

本文面向**使用者**，给可直接复制的命令与自然语言指令示例。技能自身的 agent 指令在 [esports-club-posters/SKILL.md](esports-club-posters/SKILL.md)，本文件不被技能加载，也不参与出图流程。

---

## 0. 环境准备

### Node 运行时

shell 里可能没有 `node`。用 Qoder 内置运行时：

```bash
# macOS / Linux
export ELECTRON_RUN_AS_NODE=1
NODE="/Applications/Qoder CN.app/Contents/MacOS/Qoder CN"
SKILL=".qoder/skills/esports-club-posters"
```

```powershell
# Windows（登记的 WSL/本机环境）
$NODE  = "<workspace dependency loader 给出的 Node 绝对路径>"
$SKILL = ".qoder/skills/esports-club-posters"
```

下文统一用 `"$NODE" "$SKILL/scripts/workflow.mjs"`。Windows 下写成 `& $NODE "$SKILL/scripts/workflow.mjs"`。

### 依赖与平台限制

| 能力 | 依赖 | 非 Windows |
| --- | --- | --- |
| `init` / `layout` | 仅 Node | 可用 |
| `render` | Sharp + 字体轮廓 | Sharp 可用；转曲需已有 `.cache/font-outlines.json` 或替换本机轮廓工具 |
| `package` | PowerShell 打包 | 写完查看页后提示改用本机 `zip` |

Sharp 找不到时加 `--modules <含 sharp 的 node_modules 目录>`。

---

## 1. 三十秒上手：只做一张封面

```bash
"$NODE" "$SKILL/scripts/workflow.mjs" init   --project /tmp/poster-cover --target cover
# 编辑 /tmp/poster-cover/content.json：brand、title、offer、coverVersions
"$NODE" "$SKILL/scripts/workflow.mjs" layout --project /tmp/poster-cover --target cover
# 核对数据后，把 campaign-state.json 的 contentVerified 设为 true 并写明依据
"$NODE" "$SKILL/scripts/workflow.mjs" render  --project /tmp/poster-cover --modules <node_modules>
"$NODE" "$SKILL/scripts/workflow.mjs" package --project /tmp/poster-cover --revision r1
```

实际输出：

```text
Prepared 1 pages (cover); 11 distinct strings.
Exported cover 1600×900 webp        # 约 145 KB
```

`--target` 只用于 `init` 与 `layout`；`render` 和 `package` 跟随 `layout.json`，不必重复指定。

---

## 2. 自然语言 → 命令对照

| 你说 | 参数 |
| --- | --- |
| “做一张封面” | `--target cover` |
| “做广告页 / 朋友圈四卡” | `--target ad` |
| “只要端游绝密的价目表” | `--target price --platform desktop --mode absolute` |
| “手游机密那张” | `--target price --platform mobile --mode secret` |
| “四张价目都要” | `--target price` |
| “整套都要”（明确要套图时才用） | `--target all` |

平台与模式也接受中文：`--platform 端游 --mode 绝密`。

对应关系：`desktop`/`pc`/`端游` → 端游；`mobile`/`手游` → 手游；`absolute`/`绝密` → 绝密；`secret`/`confidential`/`机密` → 机密。

---

## 3. 完整示例集

### 3.1 单张价目海报：端游 · 绝密

```bash
P=/tmp/poster-price
"$NODE" "$SKILL/scripts/workflow.mjs" init   --project $P --target price --platform desktop --mode absolute
"$NODE" "$SKILL/scripts/workflow.mjs" layout --project $P --target price --platform desktop --mode absolute
"$NODE" "$SKILL/scripts/workflow.mjs" render --project $P --modules <node_modules>
```

```text
底图: poster-gold.png
Prepared 1 pages (price); 83 distinct strings.
Exported desktop-absolute 3200×7682 webp      # 约 648 KB
```

绝密用 gold 底图，机密用 teal 底图，`init` 只复制需要的那张。

### 3.2 广告页（四卡 2×2）

```bash
"$NODE" "$SKILL/scripts/workflow.mjs" init   --project /tmp/poster-ad --target ad
"$NODE" "$SKILL/scripts/workflow.mjs" layout --project /tmp/poster-ad --target ad
```

```text
底图: social.png
Prepared 1 pages (ad); 56 distinct strings.
```

广告页展示四类价目，因此 `content.tiers` 必须齐全（端游/手游 × 绝密/机密）。

### 3.3 四张价目 / 全套

```bash
# 四张价目
"$NODE" "$SKILL/scripts/workflow.mjs" layout --project $P --target price
# → Prepared 4 pages (price); 115 distinct strings.  底图 gold + teal

# 全套（封面 + 广告 + 四张价目）
"$NODE" "$SKILL/scripts/workflow.mjs" layout --project $P --target all
# → Prepared 6 pages (all); 121 distinct strings.  底图 4 张
```

### 3.4 改一档价格，只重出受影响页

```bash
# 1. 改 content.json 里对应档位的价格
# 2. 只重排该页
"$NODE" "$SKILL/scripts/workflow.mjs" layout --project $P --target price --platform mobile --mode secret
"$NODE" "$SKILL/scripts/workflow.mjs" render --project $P --modules <node_modules>
"$NODE" "$SKILL/scripts/workflow.mjs" package --project $P --revision r2
```

广告页引用同一档数据时，另跑一次 `--target ad`。**不要为改一档重出全套。**

### 3.5 换俱乐部品牌

只改 `content.json` 的 `brand`，海报品牌行、查看页标题、交付说明自动跟随，无需改脚本：

```bash
"$NODE" -e "
const fs=require('fs');const f='$P/content.json';
const d=JSON.parse(fs.readFileSync(f,'utf8'));
d.brand='星河电竞俱乐部';
fs.writeFileSync(f,JSON.stringify(d,null,2));"
"$NODE" "$SKILL/scripts/workflow.mjs" layout --project $P --target price --platform desktop --mode absolute
```

实测 `layout.json` 中出现 3 处新品牌名（页脚、封面品牌行、广告品牌行）。

### 3.6 换单位符号与配色

改**项目副本** `layout-adapter.mjs`，不要改技能内的示例适配器：

```js
// 单位列：示例为 'r' / 'w'
number(p, t.prices[i], 'r', 260, cy, 44, ink, 42, 'price');
number(p, t.loot[i],   'w', 604, cy, 50, color, 48, 'loot');

// 配色常量
const ink='#213F53', secondary='#567486', gold='#A96620', teal='#187C78', white='#FFFDF7';
```

改完重新 `layout` → `render`。长期保留的改动同步回项目适配器，避免下次生成回退。

### 3.7 换比例（1:1 朋友圈 / 9:16 故事）

比例由底图决定，**不能拉伸**。正确顺序：

1. 按目标比例生成新的无字底图（见 `references/art-direction.md` 的尺寸映射）。
2. 放入项目 `assets/backgrounds/`。
3. 改项目适配器的 `page(id, label, bg, w, h, ew, eh)` 宽高。
4. `layout` → `render`。

直接指定不符比例的宽高会被拒绝：

```bash
"$NODE" "$SKILL/scripts/workflow.mjs" render --project $P --width 1000 --height 1000
# 错误：目标 1000×1000 与底图比例 809×1942 不一致；换比例需要先生成该比例的无字底图，不能拉伸现有底图。
```

### 3.8 尺寸与格式

```bash
R() { "$NODE" "$SKILL/scripts/workflow.mjs" render --project $P --modules <node_modules> "$@"; }

R --size web          # 1080 宽 → 1080×2593
R --size hd           # 1600 宽 → 1600×3841
R --size print        # 3200 宽 → 3200×7682（默认）
R --width 800         # 800×1920，高度按比例算
R --height 2000       # 833×2000
R --scale 0.5         # 相对适配器导出尺寸减半
R --format png        # 印刷/无损时才用
R --quality 80        # WebP 质量，默认 88
```

实测单页体积：

| 规格 | PNG | WebP |
| --- | --- | --- |
| 价目 3200×7682 | 13.66 MB | 0.63 MB |
| 价目 1600×3841 | — | 0.28 MB |
| 价目 1080×2593 | — | 0.18 MB |
| 封面 1600×900 | — | 0.14 MB |

**省体积靠格式，不靠降分辨率。** 1080 宽长图在手机上约缩到 390pt，画布正文需 ≥36px 才有 13–14pt；示例价目正文 13.4px / 809 宽画布，反推最小导出宽约 2200px。所以密集页默认 `print`，`web`/`hd` 只用于封面、广告等大字少内容页。

尺寸、格式、质量都进入页面指纹：改动后自动重导出，并删除同页旧格式文件。

### 3.9 用风格库选画风

封面/广告页取社媒卡 `SC-*`（低/中密度），价目海报取信息图 `IG-*`（高密度）：

| 目标 | 版式族 | 密度 | 举例 |
| --- | --- | --- | --- |
| 封面 | `SC-*` | 极低/低 | SC-002 文案主导、SC-016 极简大字小图、SC-023 知识封面卡 |
| 广告页 | `SC-*` | 中 | SC-010 双格标题列表、SC-014 留白驱动主视觉、SC-020 一大一组非对称 |
| 价目海报 | `IG-*` | 高 | IG-003 多行左文右图、IG-004 双列多行对比、IG-033 讲义式高密分析 |

风格库来自 [yang0/handraw-style](https://github.com/yang0/handraw-style)，本机安装根 `~/.qoder-cn/skills/handdraw-style-prompter/`。未安装时用技能自带示例画风，不编造编号。

**提示词正文严禁出现编号**（`#018`、`C-01`、`SC-016`），一律用自然语言名称，避免模型把编号当文字画进画面。

### 3.10 文字校验（含模型绘制文字时必做）

```text
1. Read 选定图片
2. 与原始文案逐字比对标题与副标题
3. 全对 → 交付，textVerification.status = pass
   有错字 → 用相同 prompt 重试，最多 2 次
   仍错 → 报告具体哪个字，给三选项：接受瑕疵 / 换风格重试 / 缩短标题重试
          → textVerification.status = accepted-with-flaw，写明依据
```

字数上限：主标题 ≤8 字（>10 字出错率显著上升），副标题 ≤12 字。含数字的文案（“199元”“5折”）出错率高，须先告知用户，建议纯中文交模型、数字走矢量层。

### 3.11 断点续作

先读项目 `campaign-state.json`：

| 字段 | 用途 |
| --- | --- |
| `targets` | 上轮目标与平台/模式 |
| `contentVerified` / `contentVerificationBasis` | 数据是否已核对及依据 |
| `visualStyle` | 画风、色彩、版式族、密度、激活方式 |
| `textVerification` | 文字校验结果 |
| `approvals` | 插画与底图的确认状态 |

手动改过 `layout.json` 就**不要再跑 `layout`**（会覆盖）；直接 `render`。需要长期保留的布局改动同步到项目适配器。

---

## 4. 给 agent 的结构化指令模板

```text
目标：price
平台：端游
模式：绝密
俱乐部：星河电竞俱乐部
玩法/标题：常规物资单
业务场景：俱乐部价目海报
受众：门店老客户与社媒读者
内容密度：高
情感基调：清晰、可信、克制
主题色：主色 普鲁士蓝 + 点缀色 象牙白
画风：示例手绘画风（或风格库风格名 + 参考作者）
数据来源：用户 2026-11-25 提供的四张价目原图，已高清核对
```

字段只写必要信息，物体细节与机位光斑交给图像模型。标题卖点可风格化，价格/物资/门槛/补偿/规则正文一律矢量渲染。

---

## 5. 项目产物

```text
<项目目录>/
├── content.json              唯一业务数据源
├── campaign-state.json       目标、确认、画风、文字校验、版本
├── layout-adapter.mjs        可改的项目适配器
├── layout.json               渲染计划（只含目标页）
├── assets/backgrounds/       本项目底图
├── source/                   脚本副本，留作重建依据
├── .cache/                   字形轮廓与页面指纹（不打包）
├── output/
│   ├── raster/               位图成品（默认 WebP）
│   ├── svg-outlined/         转曲 SVG，不依赖字体
│   ├── svg-editable/         可改文字 SVG
│   ├── manifest.json         页面索引、格式、原生与输出尺寸
│   └── DELIVERY.md           交付说明
├── review/                   预览 WebP、文字边界、index.html
└── campaign-<revision>.zip   成品包（Windows 打包或本机 zip）
```

macOS/Linux 手动打包：

```bash
cd <项目目录> && zip -r campaign-r2.zip \
  content.json campaign-state.json layout.json layout-adapter.mjs \
  assets source output review
```

---

## 6. 常见错误对照

| 错误输出 | 原因 | 处理 |
| --- | --- | --- |
| `未知目标 xxx；可选 cover/ad/price/all` | 目标拼错 | 用四个合法值之一 |
| `未知平台 xxx` / `未知模式 xxx` | 参数拼错 | 用 `desktop/mobile`、`absolute/secret` 或中文 |
| `没有匹配的价目页` | 平台/模式与 `content.tiers` 不符 | 核对 tiers 的 platform/mode 取值 |
| `目标 W×H 与底图比例不一致` | 想靠拉伸换比例 | 先生成该比例底图，见 3.7 |
| `先依据本轮资料核对 content.json…` | `contentVerified` 未置 true | 核对数据后写入 true 与依据 |
| `找不到 Sharp` | 运行时无 sharp | 加 `--modules <node_modules>` |
| `当前转曲器使用 Windows System.Drawing` | 非 Windows 且无字形缓存 | 复用已有转曲 SVG 或替换本机轮廓工具 |
| `当前打包器使用 Windows PowerShell` | 非 Windows | 查看页已生成，用本机 zip |
| `警告：导出宽 … 超过底图原生 …` | 底图偏小被放大 | 在生成阶段要求足够尺寸，别靠放大 |
| `项目已存在` | `init` 不覆盖旧目录 | 续用原项目，或换新目录 |
| `未知尺寸 xxx` / `未知格式 xxx` / `--quality 需要 1–100` | 参数非法 | 用 `web/hd/print`、`webp/png`、合法质量值 |

---

## 7. 文档索引

| 文件 | 用途 |
| --- | --- |
| [SKILL.md](esports-club-posters/SKILL.md) | agent 入口：目标路由、输入字段、文字分工、交付门禁、架构与资源 |
| [references/tooling.md](esports-club-posters/references/tooling.md) | 命令、项目结构、尺寸与格式参数 |
| [references/layout-and-data.md](esports-club-posters/references/layout-and-data.md) | 数据契约、示例坐标、`layout.json` 模型 |
| [references/art-direction.md](esports-club-posters/references/art-direction.md) | 生成流程、生成时尺寸锁定、三类提示词骨架 |
| [references/handdraw-style-library.md](esports-club-posters/references/handdraw-style-library.md) | 风格/色彩/版式族集成与激活决策 |
| [references/text-accuracy.md](esports-club-posters/references/text-accuracy.md) | 字数上限、数字风险、强制二次校验 |
| [references/sample-visuals.md](esports-club-posters/references/sample-visuals.md) | 示例角色与封面构图规范 |

示例素材位于 `esports-club-posters/assets/sample-club/`，可整套替换为俱乐部自有已确认稿。