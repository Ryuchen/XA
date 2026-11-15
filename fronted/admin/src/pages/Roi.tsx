import { DataValue } from "@/components/page-data-state"
import {
  Calculator, ClipboardCheck, HandCoins, Handshake, Headphones, Megaphone,
  ReceiptText, ScrollText, TrendingUp, UsersRound, type LucideIcon,
} from "@/components/ui/icons"
import { FinancialHeader as PageHeader, FinancialSection } from "./Financials"
import { coinText } from "@xa/shared"
import { useState } from "react"
import { Link } from "react-router"
import { Button } from "@/components/ui/button"
import {
  DataPageState,
  DataTable,
  Feedback,
  RefreshButton,
  Status,
  useLoad,
  type Row,
} from "@/components/business"
import {
  PayrollInput,
  PayrollNotice,
  PayrollWarnings,
} from "@/components/PayrollFields"
import { request } from "@/api/request"
import { beijingDay, rmb } from "@/lib/payroll"
import { useAdminAuth } from "@/lib/auth"

export default function RoiPage() {
  const { hasPerm } = useAdminAuth()
  const today = beijingDay()
  const [draft, setDraft] = useState({
    start_date: `${today.slice(0, 7)}-01`,
    end_date: today,
  })
  const [query, setQuery] = useState(draft)
  const report = useLoad(
    async () =>
      (await request<Row>({ url: "/operating-roi/", params: query })).data,
    [query.start_date, query.end_date]
  )
  const data: Row = report.data || {}
  const operating = data?.operating
  const roas = data?.roas
  const missingSource = report.loading ? "loading" : report.error ? "error" : "unread"
  return (
    <DataPageState
      loading={report.loading}
      hasSnapshot={!!report.data}
      error={report.error}
      retry={report.reload}
      skeleton="operating-roi"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            tone="feature"
            tag="经营核算"
            title="经营 ROI 与投流投产比"
            description="人民币核算。投产比与经营 ROI 使用不同订单归属期，分别展示口径和成本完整性。"
            filterDescription="日期统一作用于本页投产比、经营核算与每日成本，均按北京时间。"
            filters={<form
            className="admin-filter-form flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              setQuery({ ...draft })
            }}
          >
            <PayrollInput
              label="开始日期（北京时间）"
              type="date"
              required
              value={draft.start_date}
              onChange={(value) => setDraft({ ...draft, start_date: value })}
            />
            <PayrollInput
              label="结束日期（北京时间）"
              type="date"
              required
              value={draft.end_date}
              onChange={(value) => setDraft({ ...draft, end_date: value })}
            />
            <Button variant="outline" type="submit" disabled={report.loading}>
              查询
            </Button>
          </form>}
          >
            <RefreshButton loading={report.loading} reload={report.reload} />
            {hasPerm("cost:view") && (
              <Button size="sm" variant="outline" asChild>
                <Link to="/operating-costs">成本登记</Link>
              </Button>
            )}
          </PageHeader>

          <Feedback message={report.error} retry={report.reload} />
          {(
            <>
              <p className="text-sm text-muted-foreground">
                统计期间：<DataValue>{data.start_date} 至 {data.end_date}</DataValue>（北京时间）
              </p>
              <PayrollWarnings warnings={data.warnings} />
              {!!roas?.missing_ad_dates?.length && (
                <details className="rounded-lg border px-4 py-3 text-sm">
                  <summary className="cursor-pointer font-medium">
                    {roas.missing_ad_dates.length} 个日期尚未核对投流成本
                  </summary>
                  <p className="mt-3 leading-6">
                    {roas.missing_ad_dates.join("、")}
                  </p>
                  <p className="mt-2 text-muted-foreground">
                    没有投放也需明确登记 0 元，不能把空白当作已核对的零支出。
                  </p>
                </details>
              )}
              {report.loading && report.data && (
                <PayrollNotice>
                  正在读取所选期间，以下为上次结果。
                </PayrollNotice>
              )}
              <FinancialSection title="投流投产比" description={<>订单净消费 ÷ 同期投流费
                    </>} action={<Ratio value={roas?.value} format="multiple" hasBasis={Array.isArray(roas?.missing_ad_dates)}
                      unavailable={roas?.missing_ad_dates?.length ? "投流成本待核对" : "暂不可计算"}
                      readState={missingSource} />} className="fin-roi-card">
                
                <dl className="fin-roi-metrics">
                  <Metric
                    icon={ReceiptText} label="订单净消费"
                    value={rmb(roas?.net_consumption_fen)}
                  />
                  <Metric icon={Megaphone} label="投流成本" value={rmb(roas?.ad_spend_fen)} />
                </dl>
                <details className="border-t pt-3 text-xs text-muted-foreground">
                  <summary className="min-h-8 cursor-pointer content-center font-medium">
                    计算口径
                  </summary>
                  <p className="mt-2 leading-6">
                    {roas?.basis || "按订单创建日统计净消费，扣除累计退款。"}
                    {coinText("；消费可能包含充值赠送浪花，不能等同现金实收。投产比为全店口径，未按渠道归因。")}</p>
                </details>
              </FinancialSection>
              {data.provisional?.estimated && (
                <FinancialSection title="经营 ROI 暂估" description={<>使用已安排的员工薪资预览；尚未确认，不作为最终成本快照。
                      </>} action={<Ratio value={data.provisional.roi} format="percent" hasBasis={typeof data.provisional.complete === "boolean"}
                        unavailable={data.provisional.complete === true ? "暂不可计算" : "待成本核对"} readState={missingSource} />} className="fin-roi-card">
                  
                  <dl className="fin-roi-metrics">
                    <Metric
                      icon={ReceiptText} label="完成 / 结算订单净消费"
                      value={rmb(data.provisional.net_consumption_fen)}
                    />
                    <Metric
                      icon={Headphones} label="陪玩成本（含应付未付）"
                      value={rmb(data.provisional.provider_cost_fen)}
                    />
                    <Metric
                      icon={Handshake} label="推荐人分佣（含应付未付）"
                      value={rmb(data.provisional.referral_cost_fen)}
                    />
                    <Metric
                      icon={HandCoins} label="平台补贴成本（含应付未付）"
                      value={rmb(data.provisional.subsidy_cost_fen)}
                    />
                    <Metric
                      icon={UsersRound} label="员工薪资暂估"
                      value={rmb(data.provisional.salary_cost_fen)}
                    />
                    <Metric
                      icon={Megaphone} label="投流成本"
                      value={rmb(data.provisional.ad_spend_fen)}
                    />
                    <Metric
                      icon={ScrollText} label="其他已录成本"
                      value={rmb(data.provisional.other_cost_fen)}
                    />
                    <Metric
                      icon={Calculator} label="总成本暂估"
                      value={rmb(data.provisional.total_cost_fen)}
                    />
                    <Metric
                      icon={TrendingUp} label="利润暂估"
                      value={rmb(data.provisional.profit_fen)}
                    />
                  </dl>
                  <PayrollNotice>
                    暂估月份：
                    {data.provisional.salary_unconfirmed_months?.join("、") ||
                      "—"}
                    。
                    底薪按当月自然日分摊，已发生绩效和提成按真实业绩日期归集。
                    <Status value={data.provisional.complete} readState={typeof data.provisional.complete === "boolean" ? undefined : missingSource}
                      label={data.provisional.complete ? "暂估依据齐全" : "暂估依据待核对"} tone={data.provisional.complete ? "success" : "warning"} />
                  </PayrollNotice>
                  {(data.provisional.invalid_salary_months || []).map(
                    (item: Row) => (
                      <div key={item.month} className="space-y-2">
                        <h3 className="text-sm font-medium">
                          {item.month} 薪资待补齐
                        </h3>
                        <PayrollWarnings warnings={item.warnings} />
                      </div>
                    )
                  )}
                </FinancialSection>
              )}
              <FinancialSection title="经营 ROI（已确认薪资）" action={<Ratio value={operating?.roi} format="percent" hasBasis={typeof operating?.complete === "boolean"}
                unavailable={operating?.complete === true ? "暂不可计算" : "待成本核对"} readState={missingSource} />} className="fin-roi-card">
                
                <dl className="fin-roi-metrics">
                  <Metric
                    icon={ReceiptText} label="完成 / 结算订单净消费"
                    value={rmb(operating?.net_consumption_fen)}
                  />
                  <Metric
                    icon={Headphones} label="陪玩成本（含应付未付）"
                    value={rmb(operating?.provider_cost_fen)}
                  />
                  <Metric
                    icon={Handshake} label="推荐人分佣（含应付未付）"
                    value={rmb(operating?.referral_cost_fen)}
                  />
                  <Metric
                    icon={HandCoins} label="平台补贴成本（含应付未付）"
                    value={rmb(operating?.subsidy_cost_fen)}
                  />
                  <Metric
                    icon={UsersRound} label="员工薪资（已确认）"
                    value={rmb(operating?.salary_cost_fen)}
                  />
                  <Metric
                    icon={Megaphone} label="投流成本"
                    value={rmb(operating?.ad_spend_fen)}
                  />
                  <Metric
                    icon={ScrollText} label="其他已录成本"
                    value={rmb(operating?.other_cost_fen)}
                  />
                  <Metric
                    icon={Calculator} label="总成本"
                    value={rmb(operating?.total_cost_fen)}
                  />
                  <Metric icon={TrendingUp} label="核算利润" value={rmb(operating?.profit_fen)} />
                  <Metric
                    icon={ClipboardCheck} label="纳入订单"
                    value={operating?.order_count == null ? "—" : `${operating.order_count} 单`}
                  />
                </dl>
                <PayrollNotice>
                  <span className="mr-2">成本核对</span>
                  <Status value={operating?.complete} readState={typeof operating?.complete === "boolean" ? undefined : missingSource}
                    label={operating?.complete ? "已核对" : "待成本核对"} tone={operating?.complete ? "success" : "warning"} />{" "}
                  所有指标仅覆盖系统内订单、薪资和已登记成本，不代表全量财务利润。
                </PayrollNotice>
                <p className="text-xs text-muted-foreground">
                  平台补贴已自动计入，请勿重复登记为其他成本。
                </p>
                {!!operating?.unknown_cost_order_ids?.length && (
                  <p className="text-sm">
                    陪玩成本待核对订单：
                    {operating.unknown_cost_order_ids.map((id: number) => (
                      <Link
                        key={id}
                        className="mr-3 text-primary underline underline-offset-4"
                        to={`/orders?keyword=${id}`}
                      >
                        #{id}
                      </Link>
                    ))}
                  </p>
                )}
                {!!operating?.salary_unconfirmed_months?.length && (
                  <p className="text-sm">
                    薪资待确认月份：
                    {operating.salary_unconfirmed_months.join("、")}。
                    {hasPerm("salary:view") && (
                      <Link
                        className="ml-2 text-primary underline underline-offset-4"
                        to="/payroll"
                      >
                        前往月度核算
                      </Link>
                    )}
                  </p>
                )}
                <details className="border-t pt-3 text-xs text-muted-foreground">
                  <summary className="min-h-8 cursor-pointer content-center font-medium">
                    计算口径
                  </summary>
                  <div className="mt-2 space-y-2 leading-6">
                    <p>
                      经营 ROI
                      =（净消费－陪玩成本－推荐人分佣－平台补贴－员工薪资－投流费－其他成本）÷
                      总成本。
                    </p>
                    <p>
                      按订单完成 /
                      结算日归集，退款后按实际留存或可核对分账计算。底薪按当月自然日分摊，绩效及提成按业绩日期归集；充值本金和押金不计营业收入，未结算成本不视为零。
                    </p>
                  </div>
                </details>
              </FinancialSection>
              <section className="space-y-3">
                
                <DataTable
                  title="每日成本明细"
                  description="按统计日期核对已登记投流与其他运营成本；未登记不视为零"
                  rows={data.daily || []} loading={report.loading} error={report.error} retry={report.reload} snapshotAvailable={Array.isArray(data.daily)}
                  columns={[
                    { key: "day", label: "日期" },
                    {
                      key: "ad_spend_fen",
                      label: "投流成本",
                      render: (row) =>
                        !Array.isArray(roas?.missing_ad_dates) ? <Status readState={missingSource} /> : roas.missing_ad_dates.includes(row.day)
                          ? <Status label="未登记" tone="warning" />
                          : rmb(row.ad_spend_fen),
                    },
                    {
                      key: "other_cost_fen",
                      label: "其他成本",
                      render: (row) => rmb(row.other_cost_fen),
                    },
                  ]}
                />
              </section>
              <details className="rounded-xl border p-4 text-sm">
                <summary className="cursor-pointer font-medium">
                  数据来源与对照
                </summary>
                <div className="mt-4 space-y-3 leading-6">
                  <p>
                    同期账务已实现平台留存：
                    {rmb(operating?.realized_shop_income_fen)}
                    。该指标按真实收入流水及冲正归集，不与订单分账成本重复扣减。
                  </p>
                  {Object.entries(data.basis || {}).map(([key, value]) => (
                    <p key={key}>
                      {typeof value === "string"
                        ? value
                        : JSON.stringify(value)}
                    </p>
                  ))}
                </div>
              </details>
            </>
          )}
        </div>
      }
    </DataPageState>
  )
}

function Ratio({ value, format, hasBasis, unavailable, readState }: {
  value: unknown; format: "multiple" | "percent"; hasBasis: boolean; unavailable: string; readState: "loading" | "error" | "unread"
}) {
  if (value === null && hasBasis) return <Status label={unavailable} tone={unavailable === "暂不可计算" ? "muted" : "warning"} />
  if (!(typeof value === "number" || (typeof value === "string" && value.trim())) || !Number.isFinite(Number(value))) return <Status readState={readState} />
  const ratio = Number(value)
  return <strong className="text-xl tabular-nums">{format === "multiple" ? `${ratio.toFixed(2)} 倍` : `${(ratio * 100).toFixed(2)}%`}</strong>
}

function Metric({ label, value, icon: Icon }: { label: string; value: string; icon: LucideIcon }) {
  return (
    <div className="min-w-0">
      <dt className="flex items-center justify-between gap-2 text-xs text-muted-foreground">{label}<Icon size={16} className="shrink-0 text-primary" /></dt>
      <dd className="mt-1 font-medium tabular-nums"><DataValue>{value}</DataValue></dd>
    </div>
  )
}
