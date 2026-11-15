# 陪玩端原型功能接入待办

更新：2026-11-05。未完成的原型界面保留，明确显示“待开放/待公布”。这不是演示业务：没有假活动、假日期、假金额、假报名成功，没有调用不存在的接口。可用的真实功能继续沿用；原型Scenario/示例账号/工具壳不当作待开发业务。

## 待接入项目

| ID / 状态 | 原型文件 / 控件 | 当前缺口证据 | 现有UI位置 | 后续业务接入事项 | 验收标准 |
| --- | --- | --- | --- | --- | --- |
| P-01 待开放 | `ProviderProfilePages.tsx::ProviderAuditionsPage` 公开试音活动、游戏服务、名额/截止、填写报名入口 | `services/backend/audition/urls.py` 只有enter/info/exchange/signup/my-signups；`AuditionPublicView` 要有效邀请token；无公开招募列表读取。`AuditionPublicSerializer` 仅id/title/remark/expire_at/is_active，不能组成原型公开活动目录 | `/auditions` → `Auditions.tsx::AuditionsPage`；Mine原型Mic2菜单进入。保留招募卡、游戏/服务徽标、名额/截止说明、disabled公开报名、客服入口；值均待公布 | 确认可见性与资格（“公开”不自动等于匿名）；增加当前门店可见招募目录/详情，配置服务、名额、截止及开放/结束状态；保留现有专属邀请流程 | 读取当前门店真实活动；无活动/失败/加载可区分；不显示别店招募或伪造活动；专属token仍按现有鉴权；名额、截止和资格由服务端决定 |
| P-02 待开放 | 同上原型DetailGrid的段位要求/服务时段/试音时长/审核周期；报名框的服务介绍、音频文件 | `AuditionSignupCreateSerializer.Meta.fields=['contact','game','remark']`；无音频样本字段/上传；公开serializer也没有这四个结构化要求。个人资料的voice_card是真实语音卡，不能冒充试音报名附件 | `/auditions` 招募卡 `mt-5` 两列DetailGrid四字段“待公布”；“查看报名表”打开原型布局的disabled服务介绍/Textarea、音频UploadField及disabled提交按钮；可关闭或联系真实客服 | 定义真实活动要求字段；为报名增加独立音频资料及权限/存储/关联；核定格式、大小、时长、可替换/可审核规则，不直接复用个人语音卡 | 开放前不产生POST/上传或成功通知；开放后实际文件与报名关联、当前门店/本人访问边界正确，损坏/超限上传有真实错误；审核状态与音频读取来自后台；现有contact/game/remark邀请报名不回归 |
| P-03 待开放（同功能不同档期模型） | `ProviderProfilePage` 常规服务日、开始时间、结束时间 | `EscortScheduleView.VALID_SEGMENTS` 只允许00–06/06–12/12–18/18–24四段；PUT slots按weekday+分钟边界覆盖。任意18:00–23:30会被拒绝，不能四舍五入或静默丢掉分钟 | `ProfilePage` “本周接单”卡的自由服务日/起止输入保留但disabled、无示例默认值；可点击PendingFeature说明。下方真实7×4矩阵与保存继续可用 | 确认自由时段是否替代或补充固定档期、北京时间/跨日/交叉时段和预约冲突规则；升级读写模型及旧数据迁移；现有矩阵保留兼容 | 任意有效分钟精度可真实保存并回读；旧四段数据不丢失；没有业务确认的跨日/覆盖不自动转换；预约匹配使用相同真实区间；未完成前这组控件无写请求 |
| P-04 待开放 | `ProviderProfilePage` 基本资料DetailGrid“常用位置”“声音标签” | `EscortMeView._serialize` 及PATCH editable字段未提供结构化常用位置/声音标签；`display_tags` 是后台审核权益TAG，不是按游戏位置或声音分类，不能随意代用 | `ProfilePage` 仍保留原型四格信息区；这两格用可点击/聚焦的“待开放”说明；擅长游戏链接实际技能，个人介绍和语音卡上传仍真实可用 | 确认分类字典、按游戏的位置、本人编辑与后台审核权限、标签展示规则；增加明确数据字段和读写链路 | 回读真实位置/声音标签；类型与权限明确，不能把权益TAG自动认作声音标签；老板侧若展示，数据与陪玩资料一致；不写示例“打野/射手/清澈/沉稳” |
| P-05 待接入展示（后端已有受保护模型） | `ProviderProfilePage` “服务等级”只读格 | `EscortProfile.level` 模型已存在且由后台维护，但 `EscortMeView._serialize` 没有level/level_name；rank_tier是游戏段位，qualifications是接单资格，均不能冒充个人服务等级 | `ProfilePage` “服务等级”格保留，可点击“待开放”说明；真实认证资格仍在同卡显示 | 在权限正确的本人资料DTO补受保护等级展示字段，复用已有等级模型，不新增一套等级，不开放未经授权的自改 | 当前账号/门店的真实等级可只读展示；无等级明确未设置；自助PATCH不能修改后台等级；不能用固定Lv.4、rank_tier或资格名称替代 |
| P-07 口径细化，当前实际指标保留 | `ProviderWorkbenchPage` / `ProviderWalletPage` 的“本月净收益” | 现有ProviderStatsView.total_income与钱包累计流水汇总不是按当前月过滤的完整月净额口径；不能把当前累计值改标签为“本月” | 现有第四项指标/钱包卡继续明确显示实际服务及推荐净收益，不插入假月金额，也不取消真实流水/退款信息 | 若按原型增加月度展示，先确认服务/推荐/退款追回/补贴及月份边界，再增加明确区间汇总字段 | 月度字段可与当前门店实际流水对账，跨月/退款口径一致；不改变浪花/人民币单位；现有累计指标与钱包行为继续正确 |

## 已实现并复用，不列为待开放

- **接单开关已接真实现有状态**：`AvailabilityControl`，Workbench读取 `GET /users/provider-stats/` 的 `escort_status`，Profile读取 `GET /users/escorts/me/` 的 `status`；复用 `updateProviderStatus` → `POST /users/escorts/status/`。`AVAILABLE` 开启；`OFFLINE` 关闭；`BUSY` 显示“忙碌”、开关不选中但可由本人明确打开。渲染BUSY不会自动写OFFLINE。关闭只在用户操作时发OFFLINE；并发订单/资格限制仍由原接口与现有allowed_actions控制。Mine三态Select保留。成功后真实回读，不乐观伪改；响应丢失保留核对，不重复写。原型没有“周末自动重置”的实际后端规则，不另造该行为。
- **“接下来服务”有现成预约API**：Workbench读取本人的pending与confirmed预约，使用已有sort_start=asc/page_size=2，合并按真实时间显示两条，沿原型Schedule时间线与“查看全部档期”；无预约显示真实空态，失败可重试，实时reservation_status_update刷新。不显示原型假预约，也不把该能力记未实现。
- **Mine原型指标有现成统计**：已接provider-stats的今日接单/完成率/评分，原已完成单数/评价数仍在资料脚注保留；真实数据不足显示—和错误提示，不能复制样例数字。
- **历史试音记录已有GET**：新增 `/auditions` 的下半区读取 `/audition/my-signups`，字段按MyAuditionSignupSerializer（link_title/game/status/status_display/audit_remark/created_at）。加载/错误/空/真实记录均使用真实状态。
- **有效邀请报名已有业务**：`/audition?token=...&role=provider` 和兼容 `/pages/audition/index` 的鉴权、专属邀请交换、contact/game/remark提交、既有审核记录不改成公开列表。新增公开页面不是绕过token提交的入口。
- **擅长游戏、技能、真实语音卡/附件、通知、三态状态、7×4档期、报单凭证、钱包提现/试算与账户、通行证、积分/交付、客服会话和评价**：仍使用当前真实接口；不同模型按现有业务扩展原型样式，不一律禁用或记成“未来功能”。

## 界面与范围约束

用户最新视觉override：provider根radius 8px，Button/Input/Select/Tabs 8px、tabs trigger 6px、Badge 4px，卡片8–12px；保留头像/开关的语义圆形。主任务已恢复原型独立Moon/Sun主题和Volume提醒按钮，旧“界面与提醒”Popover移除。PendingFeature仅用于具体待开放功能说明，不是全局自造设置壳。

门店切换只保留顶栏入口；Mine旧StoreChooser/details已移除。以上真实业务接入/未实现功能均保持门店、角色、会话、草稿、幂等和实时边界。原型Scenario选择器、PhoneFrame、说明面板、假账号/假数据和客服自动示例回复是演示工具，不作为待开发功能迁移。

本次完成源码迁移与TypeScript检查。除主任务另行进行的隔离验证外，本文件不声称未来功能已接通，也不把disabled UI、表单预览或读取代码当成真实上传/报名/资金链路成功。

## 本轮已接入的门店目录（原P-06）

`/api/stores/available/` 已提供只读公开目录，仅返回已初始化、启用且数据库已配置的门店和入口/币名，不含业务数据和后台授权。顶部下拉直接显示声浪电竞/浪花与兴安电竞/星辉，实际加载、失败重试、当前项和确认切换均保留。切换整页进入目标门店登录页，令牌、订单、钱包、草稿及实时连接仍按门店隔离。旧手填标识和“目录待开放”不再作为正常切店流程。
