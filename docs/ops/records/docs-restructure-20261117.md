# docs 目录企业级整理审计记录

日期：2026-11-17；状态：**执行记录（已完成，含验证证据）**。范围：`docs/` 全目录结构整理、编号修正、索引与格式标准建立、全仓库引用重写。业务内容未改写；未执行 git 提交（变更保留在工作树，由维护者决定提交时点）。

## 1. 整理前状态

- docs 共 922 个文件：根目录散落 77 个（旧 PRD、审计、部署方案、UI 评审混杂）；development 376 个（编号主文档与约 350 个未编号专题平铺）；ops 316 个（方案与 dated 记录平铺＋13 个场景夹具目录）；design 103、prd 37、architecture 8、history 2、licenses 3。
- prd 存在 33 号重复：`33-SaaS门店授权与License.md` 与 `33-历史未结订单切换方案.md`。
- 全仓库链接基线检查：637 个 md 文件中 138 处失效链接，全部位于 ops 场景夹具/移动评审记录，指向已退役 `apps/` 旧端与 design-demos 历史路径（整理前已存在）。

## 2. 目标分类体系

```
docs/
├── README.md            文档中心：目录体系、Wiki 对应、阅读路径、格式标准（新增）
├── DESIGN_SYSTEM.md     固定锚点（AGENTS.md 引用）
├── prd/                 需求基线 01–36（33 号冲突消除）
├── development/         编号主文档 01–27＋生成脚本
│   └── topics/          18 个领域专题子目录＋索引（新增）
├── architecture/        当前设计基线＋历史参考（README 已分区）
├── ops/                 deployment/ integrations/ records/＋13 个夹具目录＋README（新增）
├── design/              参考库＋风格系统＋轮次原型＋README（新增）
├── history/             audits/ product/ ui-reviews/＋README（新增）
└── licenses/            第三方许可
```

## 3. 迁移统计（479 个文件）

| 去向 | 数量 | 来源 |
| --- | --- | --- |
| development/topics/（18 领域） | 368 | development 未编号专题 346＋根目录业务专题 22（含 WECHAT_DIRECT_* 支付草案 6） |
| ops/records/ | 55 | ops 根目录 dated 执行/联调/审计记录 |
| history/audits/ | 16 | 根目录 2026-08/09 审计＋系统逻辑排查报告等 |
| history/ui-reviews/ | 12 | 根目录旧 UI 评审/视觉记录 |
| history/product/ | 11 | 旧 PRD 变体、原生小程序产品/设计、早期 API 与交付记录 |
| ops/deployment/ | 9 | 部署方案与验证记录（RELEASE_READINESS、TENCENT_*、MANAGED_MYSQL 等） |
| ops/integrations/ | 6 | 微信/KOOK/企微正式配置与凭据说明 |
| design/ | 1 | FRONTEND_STYLE_SYSTEM.md |
| prd/ 改号 | 1 | 33-历史未结订单切换方案 → 36-历史未结订单切换方案 |

领域分类按文件名业务前缀规则＋人工复核（misc 仅 2 份基线类文档）；完整映射见本次会话工具产物，分类结果以 [topics/README](../../development/topics/README.md) 索引为准。另删除可再生缓存 `development/__pycache__`。

## 4. 固定锚点（未移动）

- AGENTS.md 引用：`docs/DESIGN_SYSTEM.md`、`docs/history/design-20261117/REFERENCE-LIBRARY.md`、`docs/prd/02-老板端.md`、`03-陪玩端.md`、`04-后台职能与操作.md`、`33-SaaS门店授权与License.md` —— AGENTS.md 本身零改动。
- 生成脚本依赖：`generate_trace.py`（prd/07、prd/20、dev 编号主文档）、`generate_decision_status.py`（dev/19）、`generate_page_trace.py`（dev/15、prd/08）—— 脚本零改动，依赖路径全部保留。
- 各目录 README 位置不变（内容更新）。

## 5. 引用重写与验证

- 重写范围：全仓库 md（排除 node_modules/.git/.tools/.venv/__pycache__/.qoder），含根 README/DESIGN/CONTRIBUTING 相关引用、fronted/ 各文档、scripts/README、docs 内部互链。
- 三轮处理：①移动目标映射重写；②残留修复（14 文件 22 处）；③已移动文件内指向未移动目标的相对链接重锚定（29 文件 214 处）。
- **最终验证：真实回归 0**（以"迁移前链接有效"为判据逐一重建核对）；**旧路径残留引用 0**；失效链接 138 处与整理前基线完全一致，均为 ops 历史记录指向已退役 `apps/` 旧端，非本次引入。
- `README.md` 与 `scripts/README.md` 对 legacy-script-followup-52 的引用已随迁移同步重写至 ops/records/。
- `.tools/` 历史工具快照（upload-manifest、license-consistency before/ 等）**冻结不改写**：属历史证据，保留旧路径原貌。

## 6. 内容层建设

- 新增 [docs/README.md](../../README.md)：目录体系、Wiki↔docs 对应、角色阅读路径、文档格式标准（命名/编号/头部要素/状态词/链接/证据/归档）、治理规则。
- 新增 README：[history](../../history/README.md)、[ops](../README.md)、[design](../../design/README.md)、[development/topics](../../development/topics/README.md)（368 份专题全量索引）。
- 更新：prd/README 阅读顺序（33–36 重排，36 号标注改号缘由）、development/README（专题目录说明）、根 README（文档中心入口）、architecture/README（当前基线/历史分区，前轮已完成）。
- 编号规则固化：编号只增不改不复用；development 20/21 缺号保留。

## 7. 遗留与后续

1. 138 处历史失效链接（ops 夹具/移动评审 → 已退役 apps/ 与 design-demos 路径）：随 [26 规划 A3](../../development/26-企业级总体规划.md) 遗留脚本清偿一并处理；此前这些记录不作当前验收依据。
2. Repo Wiki 引用路径需在 Qoder Knowledge Center 触发 Wiki 更新后与新目录一致（Wiki 为生成视图，本次未改写）。
3. topics 索引为生成式清单：新增专题文档后须同步更新对应领域清单与计数。
4. 存量文档头部状态要素不全（多数专题已有版本/状态行）：按 docs/README §4.2 标准在后续修订时逐步补齐，不做批量改写以免引入语义风险。
5. 本次全部变更未提交；提交前按 CONTRIBUTING 核对 `git status`，建议按"结构迁移＋引用重写＋索引新增"分组提交以便追溯。

## 8. 验证方式复核

- 链接检查器：对全仓库 md 的 Markdown 链接目标解析（含锚点剥离、URL 解码、跨驱动绝对路径跳过），迁移前基线与迁移后逐条比对。
- 回归判据：对每条失效链接重建其"迁移前位置＋迁移前目标"，仅当迁移前有效而迁移后失效才计为回归；结果 0。
- 固定锚点与生成脚本依赖路径逐一存在性核验通过。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.