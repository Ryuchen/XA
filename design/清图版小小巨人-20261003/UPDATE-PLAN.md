# 清图版小小巨人 · 2026-10-03 修订

目标口径：

- 体验 / 基础 / 进阶 / 豪华的撤离失败加血改为 `+1 / +1 / +1 / +2` 滴。
- 六档限时清图扣血在原数值基础上各减 1，改为 `5 / 6 / 7 / 8 / 9 / 11` 滴，对应 25 / 21 / 17 / 13 / 9 / 5 分钟。
- 同步更新价目页、双平台广告、源数据、项目状态、视觉复检和 Obsidian 玩法卡/变更日志。

原项目路径：`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\清图版小小巨人-四版价目广告封面-20260930`

执行命令：

```powershell
powershell -ExecutionPolicy Bypass -File "G:\Github\XA\design\清图版小小巨人-20261003\apply-update.ps1"
```

由于当前沙箱的自动审批额度已用尽，无法写回 C 盘原项目；原文件未被修改。恢复写权限后，按上述口径修改 `content.json`，运行 `source/workflow.mjs layout`、`render`、`verify-final.mjs`，再更新 `campaign-state.json` 与 `review/visual-qa.md` 并打包新版本。
