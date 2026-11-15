# MiSans

本软件使用 MiSans，版权归 Xiaomi Inc.。

- 来源：小米官方 [MiSans](https://hyperos.mi.com/font/zh/) Webfont 服务。
- 采用官方提供的 400 / 500 / 600 / 700 字重、简体中文与 Latin 的 WOFF2 原文件及 Unicode 范围，未修改字形。
- `manifest.json` 记录每个原始文件的 URL、大小和 SHA-256。
- `MiSans-LICENSE.pdf` 保留小米官方许可协议，各端的 `public/licenses/` 同步提供许可与使用声明。
- `shared/src/misans.css` 使用本地资源，运行时无需访问第三方字体 CDN；按 Unicode 范围按需加载。

字体层级：页面标题通常 26px/600，区块标题 16px/600，正文 14px/400，辅助信息 12px/400；重要金额按原型使用 24–32px/600，操作文字按主次使用 500/600。区域对齐与颜色仍按各页面的业务语义决定。
