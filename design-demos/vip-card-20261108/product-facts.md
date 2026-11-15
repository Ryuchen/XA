# VIP 会员卡片事实边界

- 当前业务：声浪电竞老板端会员页，真实组件位于 `fronted/boss/src/business/community.tsx` 的 `MembershipPage`。
- 当前隔离会员数据：`assets/member.json`，当前为 `隔离验收 VIP1`，`level: 1`，折扣 `9.8 折`。
- 用户于 2026-11-08 明确补充：会员一共六个等级。本设计按 `level` 1–6 建立视觉映射。
- 其他等级卡片仅标注“等级样式演示”，不虚构等级名称、折扣、升级门槛或权益。
- 真实字段：当前身份、累计确认实销、待确认实销、当前折扣、下一等级与成长进度、等级权益、展示标签、生日设置。
- 当前页面截图：`assets/member-current-desktop.png`、`assets/member-current-mobile.png`。
- 品牌 Logo：`assets/shenglang-brand.svg`，来源 `fronted/boss/public/brand.svg`。
- 参考 1：Apple Card 官方页面 `https://www.apple.com/apple-card/`，截图 `refs/apple-card.png`。
- 参考 2：American Express Platinum 官方页面 `https://www.americanexpress.com/us/credit-cards/card/platinum/`，截图 `refs/amex-platinum.png`。
- 设计仅使用参考的材质、层级与信息组织，不复制外部品牌 Logo、卡号、人物或权益文案。
