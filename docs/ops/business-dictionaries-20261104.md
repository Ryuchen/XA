# 业务字典与配置统一入口

本轮以功能打通为目标，沿用现有后台组件，不进行页面视觉改造。

## 分类与边界

新增通用字典类型为薪资岗位、投流渠道和运营成本类别。字典只保存稳定编码、名称、说明、排序和启停状态；薪资金额、提成、日成本及收入归因由对应业务表保存。薪资岗位不授予后台权限，角色授权继续由已有 `AdminRole` 管理。

已有游戏类目、服务分类、老板分级及陪玩等级放在同一入口的业务字典清单中，跳转原编辑器并使用原接口和权限，不复制原记录，不改变主键、商品关联、分级折扣、等级抽成和历史订单快照。游戏类目及服务分类位于服务商品页面相应页签。

平台抽成、提现、支付费率、押金、通行证、待接单时限、峰谷限流、会员和KOOK派单列为规则配置入口，仍通过原专用端点修改。不会开放任意 `SystemConfig` 键值写入；密钥不出现在字典或配置值清单。订单、支付、提现、售后状态机及账务类型仅说明其业务来源，不开放增删。

## 数据与接口

`console.BusinessDictionaryItem` 的 `type + code` 唯一。类型与编码创建后不可修改，显示名称与说明可改。停用仅使该项退出新增业务选项，既有业务关联仍可读取。薪资计划与成本记录使用受保护外键；存在业务引用时禁止删除。

- `GET /api/admin/dictionaries/` 返回类型、已有字典、规则入口和只读说明。
- `/api/admin/dictionary-items/` 使用标准列表、创建、详情、局部更新与删除，支持类型、名称/编码和启用状态筛选。
- `GET /api/admin/dictionary-options/?type=...` 只返回启用项的选择字段，使用后台标准分页，每页最多100条，消费方必须逐页读取完整选项。
- 管理使用 `dictionary:view` / `dictionary:edit`；薪资的查看或编辑权限可读岗位选项，成本的查看或编辑权限可读渠道及成本类别选项，不因表单读取而授予字典修改能力。

迁移按用户参考图初始化九种岗位：店长、全职客服、兼职客服、打手管理、售后管理、财务、运营、声浪考官和兴安考官。表中三位全职客服合计底薪3600和绩效2400，具体每人1200/800及提成规则在薪资业务中配置，不在字典中保存数额。

投流渠道仅初始化“未知/未归因”和“自有渠道”，成本类别初始化“投流成本”和“其他运营成本”；这些选项不代表实际发生费用，不补造渠道花费或收入归因。

## 开源结构参考与差异

检索并查看了 [RuoYi 的字典类型模型](https://github.com/yangzongzhuan/RuoYi-Cloud/blob/master/ruoyi-api/ruoyi-api-system/src/main/java/com/ruoyi/system/api/domain/SysDictType.java)及[字典项模型](https://github.com/yangzongzhuan/RuoYi-Cloud/blob/master/ruoyi-api/ruoyi-api-system/src/main/java/com/ruoyi/system/api/domain/SysDictData.java)，借用类型、稳定键值、显示名称、排序及启停分离的结构。这里将类型限制为已有实际业务消费的三类，不照搬任意状态枚举编辑、样式配置或缓存管理，也不迁移项目已有带业务字段的专用字典。

同时查看了 [Frappe HRMS 的薪资结构分配](https://github.com/frappe/hrms/blob/develop/hrms/payroll/doctype/salary_structure_assignment/salary_structure_assignment.py)，参考岗位、薪资结构、人员分配及生效时间分离的方式；不引入其税务、公式引擎或完整人事体系，工资与ROI继续按本项目业务和用户参考图实现。

## 验证范围

`console.test_dictionaries` 使用真实模型、权限、业务JWT、首次TOTP绑定与验证、API及数据库，覆盖编码唯一性/稳定性、启停、业务外键删除保护、选择权限隔离、超过100项的分页以及既有配置入口的权限提示。没有替代核心字典或薪资逻辑，没有真实投流或外部发送。

2026-11-04 已在独立 MySQL 测试库 `test_xa_test_fronted_acceptance_20261104` 完整执行14项并全部通过，包含薪资方案、成本类别和渠道的真实外键保护；没有在验收应用库或生产库写入测试数据。执行命令为 `python manage.py test console.test_dictionaries --settings=config.fronted_mysql_test_settings --keepdb --noinput -v 1`，脱敏日志保存在忽略目录 `.tools/fronted-acceptance/dictionary-tests.log`。管理后台类型检查通过，页面浏览器联调由三端功能验收统一执行。
