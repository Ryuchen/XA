# 贡献指南

开始前阅读 [AGENTS.md](AGENTS.md)，核对 `git status` 并保留他人尚未提交的修改。当前只负责三端后端和接口，现有前端由其他开发维护；不开展视觉重设计。

## 源码与工具

- `fronted/boss`、`fronted/provider`、`fronted/admin`：三端 React 网页；`fronted/shared` 为共享模块。微信授权走公众号网页授权，不保留小程序桥接包。
- `services/backend`：Django REST、WebSocket、异步任务、业务测试和数据库结构迁移。
- `packages/money`：共享金额工具。旧 Vue/Sass 包保留供追溯，不纳入当前根 workspace。
- `deploy/dev.ps1`：日常本机开发环境的统一入口。

Node.js 使用 22.12+。根工具使用 `package.json` 指定的 pnpm；三端使用独立 npm workspaces，安装依赖执行 `npm --prefix fronted ci`。不要混用锁文件或为整理目录覆盖前端源码。

## 开发与验证

仓库根目录执行 `./deploy/dev.ps1 Up|Status|Verify`。后端修改后使用 `RestartBackend`，代理修改后使用 `Reload`；不要自行启动临时后端或三套 Windows Vite。资源准备与环境边界见 [deploy/README.md](deploy/README.md)。

前端常规检查入口为 `pnpm run verify:frontend`，包括类型检查、共享模块测试和三端构建。旧 `build:mini`、`style:check`、`release:check`、`build:release` 不再作为当前检查入口。

后端使用隔离测试配置与独立数据库，按受影响业务选择真实接口、资金、权限、重试和并发测试。数据库迁移须保留；首次部署仍执行结构迁移和平台、门店配置初始化。本机开发库不清空或重建，不接触生产资金、真实客户和真实消息接收人。

## 提交

新建分支默认使用 `codex/` 前缀，提交遵循 Conventional Commits。每个 PR 说明行为变化、验证结果和未覆盖边界，不把构建成功写成业务验收通过。不要提交密钥、`.env`、数据库、上传文件、日志、虚拟环境或构建产物。

清理旧代码时先核对实际调用和现有修改；与已删除端相关的脚本可能已改成 React 契约测试，不能仅凭文件名批量删除。历史部署与旧产品资料不能用来覆盖最新产品约定。
