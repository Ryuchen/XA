# 项目目录清理（2026-11-11）

> 后续更新：2026-11-15 已删除本记录中保留的旧 Vue/Sass 包；上游许可留存位置见 [第三方许可说明](../../../THIRD_PARTY_NOTICES.md)。

后续后端无引用代码、接口及兼容分支检查见[后端清理记录](backend-unused-cleanup-20261111.md)。

本轮只整理根工具与文档，保留前端、后端业务、数据库迁移及其他人的未提交工作。开始时 `apps/` 与部分历史目录已有删除记录，这些不是本轮删除的成果。

## 已整理

- 移除指向旧原生业务端的 `build:mini`，以及旧 `admin:dev`、`style:check`、`release:check`、`build:release` 根命令。开发统一使用 `deploy/dev.ps1`；当前检查不承诺已具备生产发布条件。
- 根 pnpm workspace 仅保留 `packages/money`；锁文件清除已不存在的 `apps/*` 和退役 Vue/Sass 包的依赖图。`fronted/package-lock.json` 保持原样。
- CI 前端任务安装并检查当前 React workspace，不再遍历已删除的旧端测试目录；后端任务保持原样。
- 重写主 README 与贡献指南，去掉旧端口、Taro/Vue 启动和旧发布步骤。清理前 README 的完整文本保存在[历史快照](../../history/README-before-cleanup-20261111.md)，相对链接按新位置调整。
- 删除 `scripts/preview-ocean-ui.mjs`：该脚本只为已删除的 Vue 后台提供合成数据预览，仓库内未发现调用者。
- 删除根目录三份 2026-09-09 日志：`preview-migrate.log`、`provider-build.log`、`provider-typecheck.log`，合计 48,189 字节。

## 保留与边界

- `packages/shadcn-vue` 含既有未提交改动，与 `packages/design-system` 一并保留原位置，不纳入当前根 workspace。
- 不按 `native`、`vue`、`legacy` 文件名批量删测试；部分历史名称已接入 React 生产控制器和真实 HTTP 契约。
- 保留 `.tools`、`.venv`、依赖目录、上传文件、私有 runtime、数据库及 Redis。`.tools/fronted-acceptance` 仍被标准开发部署使用。
- 保留旧产品与部署记录；历史 Compose 和 Docker 发布工作流仍可能引用 `apps/`，需在单独发布任务中按新交付方案核对。本轮不将它们视为已适配的生产发布链路，也不执行线上部署或数据清理。
- 保留 `AGENTS.md` 与三端源码、后端业务及迁移文件，不修改产品认证、门店、资金和权限逻辑。

## 本轮验证

- 使用项目指定的 pnpm 9.0.0 离线生成根锁文件，并通过 `--frozen-lockfile --lockfile-only --offline --ignore-scripts` 核对；没有重新安装或清除现有依赖目录。
- 三端 TypeScript 检查和生产构建通过。后台、老板端构建仍有较大 chunk 提示，本轮未调整前端拆包。
- WSL `XA-Ubuntu-24.04` 中执行 `scripts/test-managed-compose.py`，4 项只读 Compose 配置测试通过。Windows 终端没有 Docker 命令，最初在 Windows 的执行未能启动该检查，随后在既有 WSL 环境完成；未启动或停止服务。
- 现有前端共享测试 100 项中通过 98 项，2 项失败：`fronted/shared/tests/business.test.ts:159`、`:514` 期望“浪花”，当前返回“店币”。本轮未改该测试及其业务实现，失败留待前端负责者核对；`verify:frontend` 因此仍会报错，不能标记为全绿。
- 根命令目标、当前文档本地链接、锁文件旧依赖清除与 `git diff --check` 核对通过。

上述检查不能替代真实数据库业务流程及微信、短信、支付等外部渠道验收。

（微信小程序相关内容已按用户 2026-11-17 决定删除，git 历史可追溯。）
