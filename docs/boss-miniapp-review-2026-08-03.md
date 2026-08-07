# 老板端小程序（boss-miniapp）审查与修复报告

> 审查日期：2026-08-03
> 审查人：WeChat Mini Program Developer（专家视角）
> 审查基线：`docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/API.md`
> 技术栈：Taro 4.1.9 + React 18 + TypeScript + Sass + Zustand（微信 / H5 双端）

---

## 一、总体结论

老板端小程序 **24 个页面已按 PRD 全部注册并落地**（`app.config.ts` 中 24 个 page + 5 个 tabBar：首页 / 陪玩 / 服务 / 消息 / 我的），业务代码整体质量较高，接口契约（`{ code, msg, data }`、`code===0` 成功）与金额口径（分 → 兴安币 `formatXaCoin`）贯彻一致。

本轮在「可编译」基础上，重点修复了**会导致白屏 / 按钮锁死 / 崩溃**的运行时缺陷，并补齐了若干**功能错误级**问题。

**验证结果（修复后）：**
- `tsc --noEmit`：**0 错误**（仅 pnpm workspace 配置告警，非代码问题）
- `taro build --type h5`：**构建成功**（仅 webpack 资源体积告警，属正常非阻塞）

---

## 二、修复清单（共 16 处）

### 🔴 崩溃级（白屏 / 锁死 / 必崩）

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 1 | `pages/customer/checkin/index.tsx` | 签到成功的 `res.data.gift.icon/name`、全勤弹窗的 `calendar.full_attendance_reward.name`、补签 `res.data.gift.icon`、礼物列表 `reward.icon/name`、全勤卡与立即签到按钮的 `calendar.full_attendance_reward?.name` / `calendar.next_gift?.icon/name` 直接解引用；后台未配置礼物时字段为 `null` → 页面白屏 | 全部改为可选链 `?.` + 兜底文案（「签到奖励 / 全勤奖励 / 神秘礼物 / 签到礼物」） |
| 2 | `pages/customer/checkin/index.tsx` | 进度条宽度 `today_spend / daily_spend_required * 100`，`daily_spend_required` 为 0 时得到 `NaN%` / `Infinity%` | 分母加 `daily_spend_required ? … : 0` 保护 |
| 3 | `pages/settings/index.tsx` | `handleSave` 无 `try/catch`，`updateMe` 在 401 / 超时 reject 时 `setSubmitting(false)` 永不执行 → 保存按钮**永久锁死** | 用 `try/finally` 包裹，确保 `setSubmitting(false)` 必然执行；异常时提示「保存失败，请稍后重试」 |
| 4 | `pages/settings/index.tsx` | 类目请求失败时 `gameCategories` 为空，`game_profiles: gameCategories.map(...)` 提交 `[]` → **清空用户全部已填游戏资料** | 仅在 `gameCategories.length > 0` 时提交 `game_profiles` 字段 |
| 5 | `pages/companions/index.tsx` | `setRanking(res.data)` 直接赋值，后端只返回 `consume_rank` 时 `order_rank` 为 `undefined`，渲染 `.length` 必崩 | 合并默认值 `{ ...res.data, consume_rank: …, order_rank: … }`（两处调用均修） |
| 6 | `pages/companions/index.tsx` / `pages/players/index.tsx` | `status.toLowerCase()` 未防 `undefined`，陪玩缺 `status` 字段即崩溃 | `(status || '').toLowerCase()` |
| 7 | `pages/players/index.tsx` | `statusTextMap[player.status] || player.status`，`player.status` 为 `undefined` 时渲染出字面量「undefined」 | 改为 `statusTextMap[...] || statusTextMap[statusKey(player.status).toUpperCase()] || '离线'` |

### 🟠 功能错误级

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 8 | `pages/category/index.tsx` | `setServices(res.data || [])` 未判断 `res.code`，业务失败（如 401）仍可能写入脏数据 | 仅当 `res.code === 0 && res.data` 时写入，否则置空数组 |
| 9 | `pages/coupon/mine/index.tsx` | `load()` 内 `await fetchMyCoupons()` 未捕获，401 / 网络异常导致未捕获 Promise | `load` 包 `try/catch` 并 toast 提示 |
| 10 | `pages/players/index.tsx` | `fetchServiceDetail(...).then(...)` 与 `fetchEscorts().then(...)` 无 `.catch`，401 必现未捕获拒绝 | 均补 `.catch` |
| 11 | `pages/chat/index.tsx` | `handleMarkAllRead` 内 `await markAllMessagesRead()` 未捕获，401 必现未捕获拒绝 | 包 `try/catch` |
| 12 | `pages/chat/index.tsx` | `Text numberOfLines={1}` 是 RN 属性，Taro 不支持单行截断 | 移除该属性（文案截断交由 CSS 控制） |
| 13 | `pages/customerService/index.tsx` | 三处 `markChatRead()` 未捕获（loadSession / WS 推送 / useDidShow），401 必现未捕获拒绝 | 均补 `.catch(() => {})` |
| 14 | `pages/announcementDetail/index.tsx` | 请求失败无错误态，失败时 `detail` 恒为 `null` → **永久 Loading** | 新增 `loading` / `error` 状态，失败时展示「公告加载失败 + 重新加载」可重试 |
| 15 | `pages/messageDetail/index.tsx` | `new Date(message.created_at).toLocaleString()`，iOS / 微信对 `YYYY-MM-DD HH:mm:ss` 解析为 `Invalid Date` | 新增 `formatDateTime` 将空格替换为 `T` 后解析，非法值回退原串 |
| 16 | `pages/login/index.tsx` | `showToast('登录成功')` 后 `finally` 中 `hideLoading()` 把成功 toast 一并清除 | 在成功分支先 `hideLoading()` 再 `showToast`，`finally` 仅保留 `setIsSubmitting(false)` |

### 🟡 轻微 / 健壮性

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 17 | `pages/companions/index.tsx` | 游戏筛选传 `category.name` 命中 `fetchEscorts` 的 legacy 模糊参数 `game`，非精准筛选 | 改用 `category.id` 走精准 `game_category` 参数（状态 `selectedGame` → `selectedCategoryId: number \| null`） |
| 18 | `pages/companions/index.tsx` | 默认头像 `copilot-cn.bytedance.net` 不在小程序 `downloadFile` 白名单，必然加载失败 | 移除远程兜底，改用 `resolveImageUrl(x)`（缺头像时返回空串，由 `<Image>` 自然留白，不再请求非法域名） |
| 19 | `pages/customer/wallet/index.tsx` | 根 `ScrollView` 漏写 `scrollY`（全项目唯一），内容可能不可滚动 | 补 `scrollY` |
| 20 | `pages/customer/wallet/index.tsx` | `TX_TABS` 仅 全部/充值/支付，与 `TX_TYPE_LABEL`（含收益/提现/押金）不一致 | 补全 收益(INCOME)/提现(WITHDRAW)/押金(DEPOSIT) 三个 tab，对齐后端流水类型 |

### 🧹 代码清理

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 21 | `src/types/product.ts` | 死代码：`Product` / `CartItem` / `Order` / `Player` 驼峰类型，无任何文件引用 | 删除文件（已确认无 import、无 re-export） |

---

## 三、本轮未改动（已知、建议后续处理）

- **首屏重复请求**：`coupon` / `announcement` / `wallet` / `chat` 的 `useEffect` 与 `useDidShow` 会同时触发一次重复请求。属性能问题非缺陷，建议在 `useDidShow` 内加 `useRef` 去重或移除冗余 `useEffect`，本轮为控制改动面未动。
- **公告/消息详情的 `forceUpdate` 类刷新**：当前依赖 `useDidShow` 重新拉取，行为正确，未改。
- **后端缺陷**（双陪分账、钱包寻址、押金税金等）记录在 `docs/系统逻辑排查报告.md`，属服务端范畴，不在前端修复范围。

---

## 四、建议的后续优化

1. **统一错误态组件**：将公告详情的「加载失败 + 重试」模式抽成通用 `<ErrorState onRetry>` 组件，复用于消息详情、订单详情等。
2. **请求层兜底**：在 `utils/request.ts` 中统一对 `res.code !== 0` 做轻量 toast（可按页面关闭），减少各页面重复判 `code` 样板。
3. **`useDidShow` 去重**：封装 `useSafeDidShow` 避免首屏双请求。
4. **图标 / 头像兜底**：对 `<Image>` 增加 `onError` 兜底占位，避免个别资源 404 时空白。

---

## 五、验证命令

```bash
# 类型检查
pnpm --filter xa-boss-miniapp exec tsc --noEmit -p tsconfig.json   # ✅ 0 errors

# H5 构建
pnpm --filter xa-boss-miniapp exec taro build --type h5            # ✅ compiled (仅体积告警)
```
