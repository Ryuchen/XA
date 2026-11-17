# 仓库遗留资产清理（.tools 与退役 Vue 包，2026-11-17）

日期：2026-11-17；状态：**执行记录（已完成，隔离区待用户处置）**。

## 1. 背景

用户目标"继续清除整个项目中旧有和无关代码"。上轮小程序切除与文档整合已固化为提交 `3dcb1a47`（787 文件）。本轮清理 git 忽略的本地遗留资产：`.tools/` 死快照与退役 Vue 包目录残留；不改动任何 git 跟踪的源码。

## 2. 保留规则（先核对引用，再判定死亡）

1. **活引用保留**：联合扫描 `deploy/`、`scripts/`、`services/backend/config/`、`.github/`、`design-demos/`、`docs/development/`、AGENTS.md 与 fronted 各 package.json 中对 `.tools/<名>` 的引用（含 Python 路径拼接写法），命中即保留。例：`fronted-acceptance`（验收凭据与 serve.py）、`fronted-scaffold`（三端 package.json source 元数据）、`web-design-review-20261108`（fronted `test:ui`）、`local-validation.env`、`mysql-8.0.46-winx64`（隔离 MySQL 发行版）、`tencent-deploy`、`visual-preview`、`visual-update`、`capacity` 及 51 个遗留脚本引用的快照目录。
2. **隔离数据库红线保留**：目录含 MySQL datadir 标志（`auto.cnf`、`ibdata1`、`#innodb_redo` 等，含 `data/` 子目录）自动保留：`mysql-test-data`、`aftersale-race-*`、`team-report-mysql-*`、`fulfillment-receipt-race-*`、`grab-receipt-race-*`、`validation-upgrade-*` 等；`validation-backups/`（xa-verify SQL 备份）豁免保留。不清空、不重建任何本机隔离库。
3. **近期工作保留**：mtime ≥ 2026-11-08 的条目保留（含前端团队 11-11 的 `saas-frontend-20261111`、`saas-preview-review-20261111`、近期 WIP 日志、`backend-unused-cleanup-20261111` 清理前备份、`license-consistency-20261111`、`plugin-builder`）。
4. **基础设施脚本保留**：根目录散落脚本按名称匹配基础设施模式（mysql/backup/compose/install/download/wsl/docker/bootstrap/settings/fixture/seed/timezone/celerybeat）保留 96 个；其余 210 个一次性脚本经全活引用扫描 0 命中后列入清除。
5. **fronted/.tools 不动**：属前端开发维护的工作区（11-11 设计草稿），不在本轮范围。

## 3. 执行动作

- 1924 个条目（死快照目录、旧日志 1355、HBuilderX 与 MySQL 下载中间包、210 个无引用一次性脚本中的过期部分、`packages/shadcn-vue`、`packages/design-system`）以同盘改名移入 **`G:\XA-cleanup-quarantine-20261117\`**，保留相对 `G:\Github\XA` 的目录结构，0 失败；可随时还原。选择隔离区而非回收站 API，避免无头会话错误弹窗阻塞。
- `.tools` 由 22G 降至 15G；隔离区 7.5G。`packages/` 仅余 `money`（pnpm workspace 与锁文件本就只含 money，无需改锁文件）。
- 删除空目录 `scripts/history/`（无内容、无跟踪文件）。
- 退役依据：pnpm-workspace.yaml 注明"旧 Vue/Sass 包已退役"；锁文件 importers 仅根与 packages/money；fronted/scripts/design-demos 对 shadcn-vue/design-system 零引用；provider-vue 应用源码已不在 git 跟踪中。

## 4. 验证

- `manage.py check --settings=config.test_settings` 复跑 0 issues；`mysqld.exe`、`fronted-acceptance/`、`local-validation.env` 等活基础设施路径核验存在。
- 本轮零跟踪源码改动，无需前端复验；本会话环境无 node/pnpm（Windows/WSL/PowerShell profile 均缺失），前端验证沿用上轮同日审计证据（见 `docs-consolidation-miniprogram-removal-20261117.md` 与提交 `3dcb1a47` 说明）。
- 移动清单与分类脚本输出留存于会话临时目录（tools-final-recycle.txt 等），隔离区本身即完整可恢复副本。

## 5. 遗留与后续

1. 隔离区 7.5G 的永久删除须用户确认后执行。
2. 51 个顶层遗留脚本轨道（仍被后端测试调用、含有效断言）处理口径待用户决定：外科式移除旧端步骤保留后端断言 / 按 26 规划 A3 先补 React 等价覆盖 / 暂保留。
3. `.tools` 保留的 15G 中，51 脚本引用的快照目录随该轨道决议一并清偿；隔离 MySQL 发行版与 datadir 属测试矩阵基础设施，长期保留。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.