# 超额累计单 · 机密地图修订复检

- 版本：excess-secret-maps-r3-20261009，日期：2026-10-09（Asia/Shanghai）。
- 本轮页面：desktop-secret、mobile-secret，共2张。
- 用户明确回复：“删除核电站及对应房卡说明”。两种平台均移除地图列表中的核电站与“核电站含金红卡”条目，保留航天及巴克什的原房卡内容。
- 来源截图：source-references/desktop-secret-before-maps-r3-20261009.png、source-references/mobile-secret-before-maps-r3-20261009.png。
- 对照修订前结构化内容，确认价目、保底、门槛、补偿、结算及售后字段均沿用已确认数据。手游仍为188/388/588/788r，对应188/488/888/1288w。

## 实际PNG与预览

| 页面 | 实际高清PNG | 手机预览 | 改动局部 | 结论 |
| --- | --- | --- | --- | --- |
| 端游机密 | output/png/desktop-secret.png（3200×7200） | review/phone-390/desktop-secret.png | review/maps-r3-details/desktop-secret-maps.png | 已无核电站；三行正文在原底板内，未碰边框或相邻装备行 |
| 手游机密 | output/png/mobile-secret.png（3200×7200） | review/phone-390/mobile-secret.png | review/maps-r3-details/mobile-secret-maps.png | 同步删除，保留两地图及房卡；价格为上轮改价后的版本 |

已直接查看两张本轮实际输出的高清PNG、两个由实际PNG提取的须知局部，以及390px宽并排手机总览 review/maps-r3-overview.png。改动区没有文字重叠、图文遮挡、贴线或裁切；整体文图关系延续已确认稿。原须知正文与信息密度沿用用户本轮提供的海报，复检结论以本次删除内容和相邻区域为范围。

## 区块边界与居中

原底图实测地图规则行边界为1294..1351（原生坐标），正文区为298..777。保留3行原布局，删除核电站后正文为：

1. 地图：航天 / 巴克什；下单先与客服确认。
2. 中途换图需协商。航天含核心区金红卡；
3. 巴克什含塔内和博物馆金红卡。

两张图正文组可见上边1298.72、下边1346.25，距原行边界分别4.72px和4.75px，整体中心偏差0.02px；没有为检查扩大底板边界。实际局部图复查通过，相邻装备行保有原分隔线和留白。新版可改文字SVG中也不含核电站。

辅助记录：review/maps-r3-audit.json。自动数据与几何核对辅助实际PNG查看，不代替视觉结论。

## 打包

本轮成品清单仅包括两张机密版，各含高清PNG、可改文字SVG、转曲SVG；查看页、对应底图及本轮复检文件同步打包。旧版压缩包保留。打包缓存复用两张已检查PNG，交付前核对包内PNG SHA-256与实际检查文件一致。
