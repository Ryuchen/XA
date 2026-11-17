# XA 项目文档中心

版本：2026-11-17；状态：**文档体系基线**。本目录是产品、研发、架构、运维与设计资料的权威事实源；Repo Wiki 是由代码生成的知识视图，两者冲突时以本目录较新文档与用户最新决定为准。

企业级 SaaS 化规划入口：[企业级总体规划](development/26-企业级总体规划.md) · [SaaS 平台产品设计](prd/35-企业级SaaS平台产品设计.md) · [目标架构与重构设计](architecture/SAAS_TARGET_ARCHITECTURE.md) · [AI 驱动 SDLC 开发流程](development/27-AI驱动SDLC开发流程.md)。

## 1. 目录体系

| 目录 | 定位 | 内容 | 维护规则 |
| --- | --- | --- | --- |
| [prd/](prd/README.md) | 需求基线 | 01–36 编号 PRD：业务规则、产品决策、差距与验收、源码复核 | 业务规则以用户最新决定为准；决策编号 D 不复用 |
| [development/](development/README.md) | 研发方案与进度 | 01–27 编号主文档＋[topics/](development/topics/README.md) 领域专题＋追踪生成脚本 | 进度单一口径：16/18/19；专题按领域归档 |
| [architecture/](architecture/README.md) | 架构设计 | 当前设计基线（SaaS 目标架构、重构计划）＋历史参考（早期架构/ER/时序） | 历史资料不代表已实现；新设计入当前基线区 |
| [ops/](ops/README.md) | 运维与执行记录 | deployment/ 部署方案、integrations/ 外部接入、records/  dated 执行记录、场景夹具目录 | 记录命名带日期；可执行配置在仓库 `deploy/`，本目录只放文档 |
| [design/](design/README.md) | 设计资料 | 设计参考库、风格系统、各轮原型与视觉资料 | 外部参考须登记来源与采用点（REFERENCE-LIBRARY） |
| [history/](history/README.md) | 历史归档 | 被取代的旧 PRD、旧审计、旧 UI 评审；只读追溯 | 只归档不删除；不得作为当前实现或验收依据 |
| [licenses/](licenses/) | 第三方许可 | 组件许可证文本 | 随依赖变更同步 THIRD_PARTY_NOTICES |
| [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | 设计系统标准 | 十二项 SaaS 交互原则与共享 token（AGENTS.md 引用，路径固定） | 修订须同步 AGENTS.md 约定 |

## 2. 与 Repo Wiki 的对应关系

| Wiki 章节 | 对应文档 |
| --- | --- |
| 项目概述 / 快速开始 | 根 README、[deploy/README](../deploy/README.md)、本文件 |
| 前端架构 | [design/](design/README.md)、`fronted/` 各 README 与设计文档 |
| 后端架构 / 数据库设计 | [architecture/](architecture/README.md)、[development/01](development/01-架构权限与多租户.md)、[02](development/02-数据模型与迁移.md)、[14](development/14-关键数据与事务方案.md) |
| 业务模块 | [prd/](prd/README.md) 01–32、[development/topics/](development/topics/README.md) |
| API 接口文档 | [development/09](development/09-接口契约与工作包.md)、[prd/08](prd/08-页面接口追踪.md)、[prd/09](prd/09-后端接口清单.md) |
| 部署运维 | [ops/deployment/](ops/README.md)、[ops/integrations/](ops/README.md)、`deploy/` |
| 测试策略 / 开发指南 | [development/10](development/10-测试发布与回滚.md)、[27](development/27-AI驱动SDLC开发流程.md)、[CONTRIBUTING](../CONTRIBUTING.md) |

Wiki 由代码与文档生成；目录结构调整后建议在 Qoder Knowledge Center 触发 Wiki 更新，使引用路径与本目录一致。

## 3. 按角色的阅读路径

- **产品/决策**：prd/README → prd/07 差距与验收 → prd/20 决策复核 → prd/33 License → prd/35 SaaS 产品设计。
- **后端研发**：AGENTS.md → development/README → 01 架构多租户 → 09 契约工作包 → 16 进度 → 18 剩余任务 → 对应 topics 领域专题。
- **前端开发**：fronted/README 与 DESIGN.md → prd/34 模块闭环 → prd/08 页面接口追踪 → development/13 事件契约。
- **测试/验收**：development/10 测试发布回滚 → prd/07 T 场景 → development/11 追踪 → fronted/verification-matrix。
- **运维/发布**：deploy/README → ops/deployment/RELEASE_READINESS → ops/deployment/TENCENT_DEPLOYMENT_PLAN → development/26 M8 门禁。
- **新成员**：根 README → 本文件 → development/26 总体规划 → development/27 SDLC 流程 → CONTRIBUTING。

## 4. 文档格式标准（企业级）

### 4.1 命名与编号

- **编号主文档**：`NN-中文标题.md`（prd 01–36、development 01–27）。编号只增不改不复用；缺号（development 20/21）保留空缺，不重排既有编号。prd 原 33 号重复（SaaS License 与历史切换方案），历史切换方案已于 2026-11-17 改号 36，SaaS License 保留 33（AGENTS.md 引用路径不变）。
- **领域专题**：`development/topics/<领域>/<场景名>.md`，场景名直述业务行为（如"双陪预约换人确认当前席位"）；领域划分见 topics/README。
- **执行记录**：`<主题>-YYYYMMDD.md`，归 ops/records/；方案类不带日期，归 deployment/、integrations/ 或对应主目录。
- **历史归档**：被取代资料移入 history/ 对应子类，保留原文件名。

### 4.2 文档头部要素

新建或修订的方案/基线类文档首部应包含：

```
版本：YYYY-MM-DD；状态：<状态词>。
<一句话范围声明：本文是什么、不是什么>
```

状态词表：**规划基线**（未实施）／**需求基线**（待复核验收）／**实施记录**（随开发更新）／**验收证据**（附测试与对账）／**历史参考**（不代表当前实现）。记录类文档至少注明日期与结论边界。

### 4.3 链接与引用

- 一律使用相对路径链接；登记前校验目标可达（整理工具见 ops/records/docs-restructure-20261117.md）。
- 引用业务规则注明 PRD 条目或决策编号；引用实现注明文件路径；引用测试注明命令与隔离环境。
- AGENTS.md、生成脚本（generate_trace.py 等）引用的路径为**固定锚点**，移动前必须同步引用方；本次整理锚点清单见审计记录。

### 4.4 证据与口径

- 构建成功≠业务验收；隔离替身≠真实渠道；待核验≠未实现。每轮只声称有证据的范围。
- 进度与完成度只登记在 development/16、18、19 与 T/G/D 追踪（development/11），其他文档不得另立进度口径。
- 金额、统计未知时显式标注缺失，不写 0 或成功。

### 4.5 归档与清理

- 只归档、不删除历史资料；清理须先核对实际调用与引用（CONTRIBUTING）。
- 被取代文档在头部或索引中标注"历史参考"，并指向取代它的新文档。

## 5. 治理

- 项目级变更日志见根目录 [CHANGELOG.md](../CHANGELOG.md)：由各变更来源（dev/16 进度日志、PRD 变更记录、ops/records 执行记录、领域专题、git 历史）归纳合并，日期倒序、摘要＋权威链接；变更落地当日追加条目，不替代验收证据。
- 文档变更随 [AI 驱动 SDLC 流程](development/27-AI驱动SDLC开发流程.md) 的 S1/S2/S5 产物登记；重大口径变更经用户确认。
- 目录级整理须产出审计记录（迁移映射、链接重写统计、回归校验），存 ops/records/。本次整理：[docs-restructure-20261117](ops/records/docs-restructure-20261117.md)。
- 已知遗留：ops 场景夹具中 138 处历史失效链接（整理前已存在，多指向已退役旧端资料），登记于审计记录，随遗留脚本清偿（26 规划 A3）一并处理。

## 许可与版权

本目录中由青岛声浪传媒有限公司依法享有权利的原创代码、文档与设计属于闭源专有内容，适用仓库根目录的 [LICENSE](../LICENSE)。第三方组件、字体和图标仍适用各自原有许可证和版权声明，参见[第三方说明](../THIRD_PARTY_NOTICES.md)。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.