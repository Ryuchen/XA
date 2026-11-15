# 第三方许可与版权说明

XA 项目中由**青岛声浪传媒有限公司**依法享有权利的原创代码、文档和设计，适用根目录 [LICENSE](LICENSE) 的闭源专有软件声明。第三方代码、组件、字体、图标和素材仍适用各自许可证及版权声明。

闭源声明和公司署名不替换原作者署名、不缩减上游授予的权利，也不免除第三方许可要求。这里列出仓库已保留的许可文件位置；完整授权范围以各材料随附的原始文本为准。

## 已保留的第三方声明

| 材料 | 原版权署名 | 许可及来源说明 |
| --- | --- | --- |
| shadcn-vue 上游组件 | Copyright (c) 2023 unovue | [上游 MIT 原文](packages/shadcn-vue/UPSTREAM-LICENSE)、[适配包说明](packages/shadcn-vue/README.md) |
| Tabler Icons SVG | Copyright (c) 2020–2025 Paweł Kuna | [MIT 原文](scripts/icon-sources/tabler/LICENSE.txt)、[来源说明](scripts/icon-sources/tabler/README.md) |
| MiSans 字体 | MiSans © Xiaomi Inc. | [官方许可 PDF](fronted/shared/assets/fonts/misans/MiSans-LICENSE.pdf)、[字体来源与使用说明](fronted/shared/assets/fonts/misans/README.md) |

三端交付目录中随附的 MiSans 声明与许可也保持原样：

- 老板端：[使用声明](fronted/boss/public/licenses/MiSans-NOTICE.txt)、[许可 PDF](fronted/boss/public/licenses/MiSans-LICENSE.pdf)。
- 陪玩端：[使用声明](fronted/provider/public/licenses/MiSans-NOTICE.txt)、[许可 PDF](fronted/provider/public/licenses/MiSans-LICENSE.pdf)。
- 管理后台：[使用声明](fronted/admin/public/licenses/MiSans-NOTICE.txt)、[许可 PDF](fronted/admin/public/licenses/MiSans-LICENSE.pdf)。

React、Django、Vue 及其他通过包管理器取得的依赖，以其发行包中的许可证、NOTICE 和版权声明为准。本索引不替代依赖自身的许可文件，也不表示已列出所有直接或传递依赖。

## 项目包元数据

项目自有私有包统一使用 `private: true`、`license: "UNLICENSED"` 和公司署名。`author`、`copyright` 字段仅用于项目自有内容的署名，不转移其他权利人的权利。

`packages/shadcn-vue` 为包含上游代码的适配包，其 `license` 使用 `SEE LICENSE IN LICENSE`，指向[包内范围说明](packages/shadcn-vue/LICENSE)。公司版权字段明确限于项目特有原创改动，上游 MIT 原文继续保留。字段格式参考 [npm 的 license 元数据说明](https://docs.npmjs.com/cli/configuring-npm/package-json/#license)。

`UNLICENSED` 不表示采用 The Unlicense，也不表示进入公有领域。JavaScript 锁文件只同步本地项目包的许可字段，第三方依赖的许可证、版本、来源和完整性记录保持原样。

## 文档与历史材料

项目自有 README 的许可附注链接到根闭源声明；历史 README 快照只追加当前许可附注，历史正文保留原样。MiSans 和 Tabler 的第三方来源 README 及其原署名保持原样。

本说明文档的原创内容：Copyright (c) 2026 青岛声浪传媒有限公司。All rights reserved.
