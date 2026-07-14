# 兴安电竞 - 游戏陪玩小程序

基于 Taro + React + TypeScript 开发的微信小程序，提供游戏陪玩服务展示和预约功能。

## 技术栈

- **框架**: Taro 4.1.9
- **前端**: React 18 + TypeScript
- **样式**: SCSS
- **构建工具**: Webpack 5

## 项目结构

```
├── src/                          # 源代码目录
│   ├── pages/                    # 页面目录
│   │   ├── home/                 # 首页 - 热门推荐、新品上架
│   │   ├── category/             # 分类页 - 游戏分类筛选
│   │   ├── chat/                 # 聊天页 - 消息中心
│   │   ├── mine/                 # 我的页面 - 用户信息、订单
│   │   ├── productDetail/        # 商品详情 - 陪玩服务详情
│   │   └── serviceCard/          # 客服页面 - 联系客服下单
│   ├── data/
│   │   └── mockProducts.ts       # 游戏陪玩商品数据
│   ├── styles/
│   │   ├── variables.scss        # SCSS 变量定义
│   │   ├── theme.scss            # 主题色配置（蓝色系）
│   │   └── compat.scss           # 兼容性样式
│   ├── types/
│   │   └── product.ts            # TypeScript 类型定义
│   ├── utils/
│   │   └── format.ts             # 格式化工具函数
│   ├── app.config.ts             # 全局应用配置
│   ├── app.tsx                   # 应用入口组件
│   └── app.scss                  # 全局样式
├── dist/                         # 编译输出目录（微信小程序）
├── scripts/
│   └── update-theme.js           # 主题色更新脚本
└── package.json                  # 项目依赖配置
```

## 主要功能

### 1. 首页 (Home)
- 搜索栏 - 支持关键词搜索陪玩服务
- Banner 轮播 - 展示活动广告
- 热门推荐 - 展示热门陪玩商品
- 新品上架 - 展示最新上架服务

### 2. 分类页 (Category)
- 侧边栏分类 - 按游戏类型筛选（三角洲、暗区突围、无畏契约等）
- 商品列表 - 双列卡片展示陪玩服务
- 价格、销量展示

### 3. 聊天页 (Chat)
- 快捷入口 - 在线客服、互动消息、系统通知
- 消息列表 - 展示系统通知、客服消息、活动优惠
- 未读消息提醒

### 4. 我的页面 (Mine)
- 用户信息 - 头像、昵称、ID
- 订单统计 - 待支付、待发货、待收货、已完成
- 功能菜单 - 我的订单、我的收藏、收货地址、设置

### 5. 商品详情 (ProductDetail)
- 图片轮播 - 展示陪玩服务图片
- 价格信息 - 现价、原价、折扣
- 规格选择 - 时长、模式等选项
- 商品详情 - 服务描述、详情图片
- 操作按钮 - 加入购物车、立即购买

### 6. 客服页面 (ServiceCard)
- 客服信息 - 头像、名称、微信号
- 二维码展示 - 长按识别添加客服
- 下单提示 - 详细的下单流程说明
- 操作按钮 - 复制微信号、返回首页

## 游戏类型

目前支持的陪玩游戏：
- 三角洲行动
- 暗区突围
- 无畏契约
- CSGO
- 和平精英
- 王者荣耀

## 主题色配置

默认使用蓝色系主题，可通过脚本快速切换：

```bash
# 更新主题色
npm run theme:update #4A9EFF #6BB6FF #3A8EF

# 更新并自动编译
npm run theme:build #4A9EFF #6BB6FF #3A8EF
```

主题色配置文件：`src/styles/theme.scss`

## 开发命令

```bash
# 安装依赖
npm install

# 开发模式（带热更新）
npm run dev:weapp

# 生产构建
npm run build:weapp

# 更新主题色
npm run theme:update <primary> <primary-light> <primary-dark>
```

## TabBar 导航

- **首页** - 浏览推荐陪玩服务
- **分类** - 按游戏类型筛选
- **聊天** - 消息中心、联系客服
- **我的** - 个人中心、订单管理

## 数据说明

商品数据位于 `src/data/mockProducts.ts`，包含：
- 8 个陪玩商品
- 6 种游戏分类
- 完整的商品信息（价格、规格、库存等）

## 注意事项

1. **源码修改**: 所有修改应在 `src/` 目录下进行
2. **编译输出**: `dist/` 目录由编译自动生成，不要手动修改
3. **页面配置**: 新页面需在 `src/app.config.ts` 中注册
4. **样式规范**: 使用 SCSS 变量，保持主题一致性

## 微信小程序配置

- **appid**: wx495c9fb56eb3ad24
- **miniprogramRoot**: dist/
- **基础库版本**: 3.15.2

## 项目特点

- ✅ 蓝色系主题，视觉统一
- ✅ 响应式布局，适配各种屏幕
- ✅ TypeScript 类型安全
- ✅ SCSS 模块化样式
- ✅ 主题色一键切换脚本
- ✅ 符合微信小程序最佳实践
