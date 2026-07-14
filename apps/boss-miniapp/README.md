# 兴安电竞游戏陪玩小程序

Taro + React + TypeScript 微信小程序项目，当前已整理为常见游戏陪玩商城结构：首页推荐、分类、消息、我的、商品详情和联系客服下单页。

## 本地运行

```bash
nvm use
npm install
npm run build:weapp
```

微信开发者工具直接打开本目录即可，项目配置已指向 `dist/` 作为小程序根目录。

## 开发模式

```bash
npm run dev:weapp
```

首次打开微信开发者工具前，确保 `dist/` 已经存在；如果没有，先执行一次 `npm run build:weapp`。
