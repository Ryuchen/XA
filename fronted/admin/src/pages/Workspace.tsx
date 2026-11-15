import { Link } from "react-router"
import {
  ArrowRight,
  ClipboardList,
  Headphones,
  Package,
  ShieldCheck,
  Users,
} from "lucide-react"
import { useCurrentStore } from "@xa/shared"
import { useAdminAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { PageHeader } from "@/components/business"
import "./saas.css"

export default function WorkspacePage() {
  const auth = useAdminAuth(),
    store = useCurrentStore()
  const tasks = [
    {
      to: "/dictionaries",
      name: store.status === "CONFIGURING" ? "开店设置清单" : "快捷配置",
      description: store.status === "CONFIGURING" ? "核对本店开业所需的核心配置" : "按业务查找本店规则与配置入口",
      permission: ["dictionary:view", "dictionary:edit"],
      icon: ShieldCheck,
    },
    {
      to: "/deposit-rules",
      name: "押金与结款规则",
      description: "核对本店陪玩资格和收益规则",
      permission: "deposit_rule:view",
      icon: ShieldCheck,
    },
    {
      to: "/orders?dispatch=quick",
      name: "新建客服代单",
      description: "核对老板、服务、价格与付款确认",
      permission: "order:dispatch",
      icon: Headphones,
    },
    {
      to: "/orders?queue=staff_dispatch",
      name: "查看待派订单",
      description: "查看本店需要客服派单的订单，按每笔订单的状态处理",
      permission: "order:view",
      icon: ClipboardList,
    },
    {
      to: "/reports",
      name: auth.hasPerm("report:audit") ? "审核履约报单" : "查看履约报单",
      description: "核对陪玩提交的履约凭证、分账份额和审核结果",
      permission: "report:view",
      icon: ShieldCheck,
    },
    {
      to: "/users",
      name: "本店老板资料",
      description: "查看本店客户资料和关联业务",
      permission: "user:view",
      icon: Users,
    },
    {
      to: "/modules",
      name: "老板端 H5 与品牌",
      description: "查看购买授权、客户侧定制和需求记录",
      permission: ["module:view", "module:manage"],
      icon: Package,
    },
  ].filter(
    (task) =>
      auth.hasPerm(task.permission) &&
      (task.to !== "/orders?dispatch=quick" || auth.hasPerm("order:view")) &&
      (store.status !== "CONFIGURING" ||
        ["/dictionaries", "/deposit-rules", "/modules"].includes(task.to))
  )
  const firstTask = store.status === "CONFIGURING" ? tasks[0]
    : tasks.find(task => task.to === "/orders?queue=staff_dispatch") ?? tasks.find(task => task.to === "/reports") ?? tasks[0]
  const otherTasks = tasks.filter(task => task !== firstTask)
  return (
    <div className="space-y-6">
      <PageHeader title="我的工作台" tag={store.name}>
        <span className="text-sm text-blue-100">{auth.profile?.nickname || auth.profile?.username}</span>
      </PageHeader>
      <div className="xa-role-scope">
        <strong>
          {auth.profile?.is_superuser
            ? "俱乐部管理员 / 经营者"
            : auth.hasPerm("order:dispatch")
              ? "客服工作区"
              : "本店业务工作区"}
        </strong>
        <span>仅当前俱乐部数据 · 可用功能由账号权限决定</span>
      </div>
      <section className="saas-section">
        <h2>{store.status === "CONFIGURING" ? "开店准备" : "当前可用任务"}</h2>
        {firstTask ? (
          <>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-4 rounded-lg bg-accent px-4 py-3">
            <div className="flex items-start gap-3">
              <firstTask.icon className="mt-1 size-5 text-primary" aria-hidden="true" />
              <div>
                <h3 className="font-semibold">{firstTask.name}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{firstTask.description}</p>
              </div>
            </div>
            <Button asChild><Link to={firstTask.to === "/dictionaries" && store.status === "CONFIGURING" ? "/dictionaries?section=checklist" : firstTask.to}>{firstTask.name}<ArrowRight size={16} /></Link></Button>
          </div>
          {otherTasks.length > 0 && <ul className="mt-3 divide-y">
            {otherTasks.map((task) => (
              <li
                key={task.to}
                className="flex items-center justify-between gap-6 py-4"
              >
                <div className="flex items-start gap-3">
                  <task.icon className="mt-1 size-5 text-primary" />
                  <div>
                    <h3 className="font-medium">{task.name}</h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {task.description}
                    </p>
                  </div>
                </div>
                <Button variant="outline" asChild>
                  <Link to={task.to} aria-label={`进入${task.name}`}>
                    进入
                    <ArrowRight size={16} />
                  </Link>
                </Button>
              </li>
            ))}
          </ul>}
          </>
        ) : (
          <p className="saas-note">
            当前账号暂未获得业务任务权限，请联系本店管理员。
          </p>
        )}
      </section>
      <section className="saas-section">
        <h2>业务入口与身份</h2>
        <dl className="saas-definition">
          <div>
            <dt>俱乐部老板 / 店长</dt>
            <dd>本店管理账号登录后台，查看已授权的经营和产品配置。</dd>
          </div>
          <div>
            <dt>客服</dt>
            <dd>本店员工账号登录后台，负责派单、咨询与获授权审核。</dd>
          </div>
          <div>
            <dt>陪玩人员</dt>
            <dd>使用本店陪玩入口，处理本人接单、履约、报单与收益。</dd>
          </div>
          <div>
            <dt>消费老板</dt>
            <dd>使用本店老板端 H5 微信登录，查看本人订单与权益。</dd>
          </div>
        </dl>
        <p className="saas-note">
          平台人员通过独立平台身份进入统一运营中心。店内管理员与平台管理员的授权分别管理；更换店内身份需要重新登录。
        </p>
      </section>
    </div>
  )
}
