# 运维与执行记录

版本：2026-11-17；状态：**索引**。本目录是部署方案、外部接入配置说明与带日期的执行/联调记录；可执行的编排、脚本与环境配置在仓库 [`deploy/`](../../deploy/README.md) 与 [`scripts/`](../../scripts/README.md)，本目录只放文档。发布门禁与上线顺序见 [26-企业级总体规划](../development/26-企业级总体规划.md) M8。

## 目录结构

| 位置 | 内容 |
| --- | --- |
| [deployment/](deployment/) | 部署与发布方案：[RELEASE_READINESS](deployment/RELEASE_READINESS.md)（上线清单）、[TENCENT_DEPLOYMENT_PLAN](deployment/TENCENT_DEPLOYMENT_PLAN.md)、[MANAGED_MYSQL_DEPLOYMENT](deployment/MANAGED_MYSQL_DEPLOYMENT.md)、腾讯云历史实机记录、更新/重置部署与本机容量/部署验证 |
| [integrations/](integrations/) | 外部接入：WECHAT_FORMAL_SETUP_20260930（微信正式配置）、KOOK/企微客服配置、[INTEGRATION_CREDENTIALS](integrations/INTEGRATION_CREDENTIALS.md)、后端集成记录 |
| [records/](records/) | 带日期的执行、联调、审计与整理记录（55 份）；命名 `<主题>-YYYYMMDD.md`；本次目录整理审计见 [docs-restructure-20261117](records/docs-restructure-20261117.md) |
| 场景夹具目录（13 个） | 2026-10 资金/玩法场景联调夹具与记录成对保存：checkout-selection、partial-refund、settled-refund、transferred-team-refund、multi-transferred-team-refund、fixed-multi-transferred-team-refund、team-partial-refund、team-order-e2e、multi-companion-selection、order-commission-mode、lottery-full-flow、lottery-gacha、lottery-ten-guarantee（均带 -20261020/-20261021 日期） |
| [local-wsl-fixed-endpoints.md](local-wsl-fixed-endpoints.md) | 本机 WSL 固定端点登记（活跃环境参考） |
| mobile-inventory-generator.py | 移动端页面清单生成脚本（历史工具，配合 records/ 清单记录使用） |

## 使用规则

1. **历史实机记录不代表当前部署**：TENCENT_DEPLOYMENT_20260928 等早期记录已标注历史；首版未部署，当前以 deployment/ 方案与 26 规划 M8 门禁为准。
2. 场景夹具目录内脚本与记录成对维护，不单独移动；其中指向已退役 `apps/` 旧端的 138 处失效链接为整理前遗留，登记于[整理审计](records/docs-restructure-20261117.md)，随遗留脚本清偿处理。
3. 新执行记录归 records/ 并带日期；方案类文档归 deployment/ 或 integrations/，不带日期。
4. 真实渠道凭据不入库；接入说明只描述配置位置与核验步骤（AGENTS.md 隔离约定）。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.