import { coinText, coinUnit, useCurrentStore } from "@xa/shared"
import { formatProviderName, ProviderName } from "./provider-name"
import { useState } from "react"
import { Link } from "react-router"
import { Button } from "@/components/ui/button"
import {
  Check,
  Crown,
  Gift,
  Heart,
  Minus,
  Plus,
  UsersRound,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  api,
  Choice,
  SearchField,
  Explanation,
  Image,
  Info,
  money,
  Panel,
  Pending,
  queryUrl,
  readIntent,
  requestId,
  rows,
  State,
  storeIntent,
  submitIntent,
  BackLink,
  Title,
  useAction,
  useConfirm,
  useLoad,
  useQuery,
  type Intent,
  type Row,
} from "./ui"

export function GiftsPage() {
  const store = useCurrentStore()
  const query = useQuery(),
    orderId = query.get("order_id"),
    providerId = query.get("provider_id"),
    [search, setSearch] = useState(""),
    [keyword, setKeyword] = useState(""),
    [category, setCategory] = useState(""),
    [recipientId, setRecipientId] = useState(providerId || ""),
    [basket, setBasket] = useState<Record<number, number>>({}),
    [naming, setNaming] = useState(""),
    [receipt, setReceipt] = useState<Row | null>(null),
    [intent, setIntent] = useState<Intent | null>(() => readIntent("gift")),
    { busy, run } = useAction(),
    { confirm, dialog } = useConfirm()
  const contextPath = queryUrl(
    "/orders/gifts/context/",
    orderId
      ? { order_id: orderId }
      : providerId
        ? { provider_id: providerId }
        : { browse: 1, search: keyword }
  )
  const load = useLoad(() => api.get<any>(contextPath), [contextPath]),
    context = load.data
  const gifts = rows(context?.gifts),
    visibleGifts = gifts.filter(
      (item) =>
        !category || (item.service_category_name || "常规礼物") === category
    ),
    recipients = rows(context?.recipients),
    selected = gifts
      .filter((item) => Number(basket[item.id]) > 0)
      .map<Row>((item) => ({ ...item, quantity: basket[item.id] })),
    total = selected.reduce((sum, item) => sum + item.price * item.quantity, 0),
    count = selected.reduce((sum, item) => sum + item.quantity, 0),
    namingDays = selected.reduce(
      (sum, item) => sum + Number(item.gift_naming_days || 0) * item.quantity,
      0
    ),
    recipient =
      recipients.find((item) => String(item.id) === recipientId) ||
      ((orderId || providerId) && recipients.length === 1
        ? recipients[0]
        : null)
  function refreshIntent() {
    setIntent(readIntent("gift"))
  }
  function change(id: number, amount: number) {
    setBasket((values) => ({
      ...values,
      [id]: Math.max(0, Math.min(Number(context?.max_quantity || 1), amount)),
    }))
  }
  async function send(original: Intent) {
    try {
      const result = await submitIntent("gift", original)
      setReceipt(result)
      setBasket({})
      setNaming("")
      load.reload()
    } finally {
      refreshIntent()
    }
  }
  async function lookup() {
    if (!intent) return
    const result = await api.post<any>("/orders/gifts/receipt/", {
      path: intent.path,
      payload: intent.payload,
      client_request_id: intent.id,
    })
    if (result.status === "COMMITTED" && result.fundResult?.code === 0) {
      storeIntent("gift", null)
      setReceipt(result.fundResult.data)
      refreshIntent()
      load.reload()
    } else
      throw new Error(
        "暂未查到确定结果，请保留凭证，稍后重试原请求或联系客服核对"
      )
  }
  async function prepare() {
    if (!context || !recipient || !selected.length)
      throw new Error("请选择收礼陪玩与礼物")
    if (readIntent("gift")) {
      refreshIntent()
      throw new Error("请先核对上次赠送")
    }
    if (selected.length > 30) throw new Error("一次最多选择 30 款礼物")
    if (namingDays && !naming.trim()) throw new Error("请填写想展示的冠名")
    if (!context.wallet_active || typeof context.prepaid_balance !== "number" || total > context.prepaid_balance)
      throw new Error("本金余额不足或暂不可用，礼物只能使用本金支付")
    const current = await api.get<any>(contextPath),
      currentRecipient = rows(current.recipients).find(
        (item) => item.id === recipient.id
      )
    if (!currentRecipient)
      throw new Error("收礼陪玩状态已变化，请刷新后重新选择")
    if (!current.wallet_active || typeof current.prepaid_balance !== "number" || current.prepaid_balance < total)
      throw new Error("本金余额不足或暂不可用，请联系客服核对")
    if (
      selected.some((item) => {
        const fresh = rows(current.gifts).find((row) => row.id === item.id)
        return (
          !fresh ||
          fresh.price !== item.price ||
          fresh.gift_naming_days !== item.gift_naming_days
        )
      })
    ) {
      load.reload()
      throw new Error("礼物信息已更新，请重新核对后再赠送")
    }
    const payload = {
      items: selected.map((item) => ({
        gift_service_id: item.id,
        quantity: item.quantity,
      })),
      provider_id: recipient.id,
      expected_amount: total,
      naming_title: naming.trim(),
    }
    confirm({
      title: "确认赠送礼物",
      description: (
        <>
          <p>
            送给 <ProviderName value={recipient.nickname} />：
            {selected
              .map((item) => `${item.name} × ${item.quantity}`)
              .join("、")}
            。
          </p>
          <p>
            共 {count} 件，将从本金余额扣除 {money(total)} {coinText(" 浪花。")}
          </p>
          {namingDays > 0 && (
            <p>
              冠名「{naming.trim()}」 · {namingDays}{" "}
              天，最终有效时间以赠送结果为准。
            </p>
          )}
        </>
      ),
      confirm: "确认赠送",
      action: async () => {
        if (readIntent("gift")) throw new Error("请先核对上次赠送")
        const original = {
          id: requestId("gift"),
          path: orderId
            ? `/orders/orders/${orderId}/tip/`
            : "/orders/gifts/send/",
          display: {
            name: `赠送给 ${formatProviderName(recipient.nickname, store.provider_name_prefix)}`,
            details: selected
              .map((item) => `${item.name} × ${item.quantity}`)
              .join("、"),
            amount: total,
          },
          payload,
        }
        storeIntent("gift", original)
        setIntent(original)
        await send(original)
      },
    })
  }
  const categories = [
    "",
    ...new Set(gifts.map((item) => item.service_category_name || "常规礼物")),
  ]
  const draftLocked = busy || !!intent || load.loading || !!load.error
  const canSend =
    !draftLocked &&
    !!recipient &&
    selected.length > 0 &&
    context?.wallet_active &&
    typeof context.prepaid_balance === "number" && total <= context.prepaid_balance &&
    !(namingDays > 0 && !naming.trim())
  return (
    <div className="boss-gifts-page">
      <BackLink to={orderId ? "/order-detail?id=" + orderId : "/mine"}>
        {orderId ? "返回订单" : "个人中心"}
      </BackLink>
      <Title
        title="把心意，送给 TA"
        description="挑选常规好礼、节日限定或专属冠名。"
        action={
          <Button asChild variant="outline">
            <Link to="/orders?type=gift">查看礼物单</Link>
          </Button>
        }
      />
      <Pending
        busy={busy}
        intent={intent}
        retry={() => {
          if (intent) void run(() => send(intent), "").catch(() => {})
        }}
        resolve={() => void run(lookup, "").catch(() => {})}
      />
      {receipt && (
        <Panel className="boss-gift-receipt">
          <span className="boss-gift-receipt-icon">
            <Check size={28} />
          </span>
          <h2>心意已送达</h2>
          <p className="boss-gift-receipt-amount">
            {money(receipt.amount)}
            <small>{coinUnit()}</small>
          </p>
          {receipt.quantity != null && (
            <p className="boss-muted">共 {receipt.quantity} 件心意</p>
          )}
          <Info
            entries={[
              [
                "订单",
                receipt.order_no ||
                  (Array.isArray(receipt.orders)
                    ? `已生成 ${receipt.orders.length} 笔礼物单`
                    : ""),
              ],
              [
                "冠名时长",
                receipt.naming_days ? receipt.naming_days + " 天" : undefined,
              ],
            ]}
          />
          <div className="boss-actions" style={{ marginTop: 16 }}>
            {receipt.id ? (
              <Button asChild>
                <Link to={"/order-detail?id=" + receipt.id}>查看礼物单</Link>
              </Button>
            ) : (
              <Button asChild>
                <Link to="/orders?type=gift">查看礼物单</Link>
              </Button>
            )}
            <Button asChild variant="outline">
              <Link to="/points?tab=claims">查看赠礼权益</Link>
            </Button>
            <Button variant="ghost" onClick={() => setReceipt(null)}>
              继续挑选
            </Button>
          </div>
        </Panel>
      )}
      {!receipt && (
        <State {...load}>
          {context && (
            <>
              <div className="boss-gifts-layout boss-gifts-source-layout">
                <section
                  className="boss-gift-recipient-card"
                  aria-labelledby="gift-recipient-heading"
                >
                  <header>
                    <h2 id="gift-recipient-heading">
                      <UsersRound size={16} />
                      收礼陪玩
                    </h2>
                    <span>这份心意，送给谁</span>
                  </header>
                  <div className="boss-gift-recipient-row">
                    {!orderId && !providerId && (
                      <form
                        className="boss-gift-recipient-search"
                        onSubmit={(event) => {
                          event.preventDefault()
                          if (!draftLocked) setKeyword(search)
                        }}
                      >
                        <fieldset
                          disabled={draftLocked}
                          className="boss-gift-recipient-search-fields"
                        >
                          <SearchField
                            value={search}
                            onChange={setSearch}
                            placeholder="搜索陪玩昵称"
                            label="搜索收礼陪玩昵称"
                          />
                          <Button
                            type="submit"
                            variant="outline"
                            disabled={draftLocked}
                          >
                            搜索
                          </Button>
                        </fieldset>
                      </form>
                    )}
                    <div className="boss-gift-recipient-choice">
                      {recipients.length ? (
                        <Choice
                          label="收礼陪玩"
                          value={recipient?.id || recipientId}
                          onChange={setRecipientId}
                          disabled={draftLocked}
                          options={[
                            { value: "", label: "请选择收礼陪玩" },
                            ...recipients.map((item) => ({
                              value: item.id,
                              label: formatProviderName(item.nickname, store.provider_name_prefix),
                            })),
                          ]}
                        />
                      ) : (
                        <div className="boss-gift-recipient-empty">
                          {keyword
                            ? "没有找到该昵称的陪玩，请更换关键词。"
                            : "暂无可收礼的陪玩，请稍后刷新查看。"}
                        </div>
                      )}
                    </div>
                  </div>
                  {context.source_order && (
                    <p className="boss-gift-source-order">
                      来自服务订单 {context.source_order.order_no}
                    </p>
                  )}
                </section>
                <section
                  className="boss-gift-catalog"
                  aria-labelledby="gift-catalog-heading"
                >
                  <header className="boss-gift-catalog-heading">
                    <div>
                      <h2 id="gift-catalog-heading">挑选心意</h2>
                      <p>可跨款多选，每款最多 {context.max_quantity} 件</p>
                    </div>
                    <Badge variant="secondary">{count} 件已选</Badge>
                  </header>
                  <nav className="boss-gift-filterbar" aria-label="礼物分类">
                    <div
                      style={
                        {
                          "--gift-filter-columns": categories.length,
                        } as React.CSSProperties
                      }
                    >
                      {categories.map((value) => (
                        <button
                          type="button"
                          key={value || "all"}
                          title={value || "全部"}
                          aria-pressed={category === value}
                          onClick={() => setCategory(value)}
                        >
                          <span>{value || "全部"}</span>
                        </button>
                      ))}
                    </div>
                  </nav>
                  <State
                    compact
                    empty={!visibleGifts.length}
                    loading={load.loading}
                    emptyTitle={
                      category ? "当前分类暂无礼物" : "礼物正在准备中"
                    }
                    emptyDescription={
                      category
                        ? "可以切换其他分类，继续挑选心意。"
                        : "上架后即可挑选并赠送，当前不会产生消费。"
                    }
                    emptyAction={
                      category ? (
                        <Button
                          variant="outline"
                          onClick={() => setCategory("")}
                        >
                          查看全部礼物
                        </Button>
                      ) : undefined
                    }
                  >
                    <div
                      className="boss-gift-source-grid"
                      aria-label="礼物列表"
                    >
                      {visibleGifts.map((item) => {
                        const Icon =
                          item.gift_naming_days > 0
                            ? Crown
                            : item.gift_festival
                              ? Gift
                              : Heart
                        const addLocked =
                          draftLocked ||
                          (basket[item.id] || 0) >= context.max_quantity ||
                          (!basket[item.id] && selected.length >= 30)
                        return (
                          <article
                            className={
                              "boss-gift-tile" +
                              (basket[item.id] ? " selected" : "")
                            }
                            key={item.id}
                          >
                            <button
                              type="button"
                              className="boss-gift-tile-main"
                              disabled={addLocked}
                              onClick={() =>
                                change(item.id, (basket[item.id] || 0) + 1)
                              }
                              aria-label={"添加" + item.name}
                            >
                              <span className="boss-gift-art">
                                {item.cover_url ? (
                                  <Image
                                    src={item.cover_url}
                                    alt={item.name}
                                    className="boss-gift-image"
                                  />
                                ) : (
                                  <Icon size={28} aria-hidden="true" />
                                )}
                              </span>
                              {item.gift_festival && (
                                <span className="boss-gift-festival">
                                  {item.gift_festival}
                                </span>
                              )}
                              <h3>{item.name}</h3>
                              <span className="boss-price">
                                {money(item.price)}
                                <small>{coinUnit()}</small>
                              </span>
                              <p>
                                {item.gift_naming_days > 0
                                  ? `冠名 ${item.gift_naming_days} 天 / 件`
                                  : "送一份欣赏与心意"}
                              </p>
                            </button>
                            <div className="boss-gift-quantity">
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                aria-label={"减少" + item.name}
                                disabled={draftLocked || !basket[item.id]}
                                onClick={() =>
                                  change(item.id, (basket[item.id] || 0) - 1)
                                }
                              >
                                <Minus size={16} />
                              </Button>
                              <span aria-live="polite">
                                {basket[item.id] || 0}
                              </span>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                aria-label={"增加" + item.name}
                                disabled={addLocked}
                                onClick={() =>
                                  change(item.id, (basket[item.id] || 0) + 1)
                                }
                              >
                                <Plus size={16} />
                              </Button>
                            </div>
                          </article>
                        )
                      })}
                    </div>
                  </State>
                </section>
                <aside
                  className="boss-gift-source-aside"
                  aria-label="心意清单与赠礼说明"
                >
                  {count > 0 && (
                    <section
                      className="boss-gift-checklist"
                      aria-labelledby="gift-checklist-heading"
                    >
                      <header>
                        <h2 id="gift-checklist-heading">心意清单</h2>
                        <span>已选 {count} 件</span>
                      </header>
                      <div className="boss-gift-checklist-lines">
                        {selected.map((item) => (
                          <div key={item.id}>
                            <p>
                              {item.name}
                              <span>× {item.quantity}</span>
                            </p>
                            <strong>
                              {money(item.price * item.quantity)} {coinUnit()}
                            </strong>
                          </div>
                        ))}
                      </div>
                      {namingDays > 0 && (
                        <div className="boss-gift-naming">
                          <div>
                            <Label htmlFor="gift-naming">
                              <Crown size={16} />
                              专属冠名
                            </Label>
                            <span>累计 {namingDays} 天</span>
                          </div>
                          <Input
                            id="gift-naming"
                            aria-label="冠名内容"
                            value={naming}
                            onChange={(event) => setNaming(event.target.value)}
                            maxLength={30}
                            required
                            placeholder="例如 擅长王者荣耀，可开黑…"
                            disabled={draftLocked}
                          />
                          {!naming.trim() && (
                            <p className="boss-gift-warning" role="alert">
                              请填写想展示的冠名
                            </p>
                          )}
                          <p>
                            每件礼物的时长按数量累加，最终有效时间以赠送结果为准。
                          </p>
                        </div>
                      )}
                      <dl className="boss-gift-totals">
                        <div>
                          <dt>可用于赠礼的本金</dt>
                          <dd>
                            {typeof context.prepaid_balance === "number" ? `${money(context.prepaid_balance)} ${coinUnit()}` : "暂不可用"}
                          </dd>
                        </div>
                        <div>
                          <dt>合计</dt>
                          <dd>
                            {money(total)} {coinUnit()}
                          </dd>
                        </div>
                      </dl>
                      {!recipient && (
                        <p className="boss-gift-warning">请先选择收礼陪玩</p>
                      )}
                      {!context.wallet_active && (
                        <div className="boss-gift-error" role="alert">
                          <p>当前钱包不可用，请联系客服核对后再赠送。</p>
                          <Button asChild variant="outline">
                            <Link to="/support">联系客服</Link>
                          </Button>
                        </div>
                      )}
                      {typeof context.prepaid_balance !== "number" && (
                        <div className="boss-gift-error" role="alert"><p>本金余额暂不可用，请稍后重试或联系客服核对。</p></div>
                      )}
                      {typeof context.prepaid_balance === "number" && total > context.prepaid_balance && (
                        <div className="boss-gift-error" role="alert">
                          <p>本金余额不足，礼物只能使用本金支付。已选礼物会保留。</p>
                          <Button asChild variant="ghost">
                            <Link to="/support?scene=recharge">
                              联系客服充值
                            </Link>
                          </Button>
                        </div>
                      )}
                    </section>
                  )}
                  {!count && !context.wallet_active && (
                    <div className="boss-gift-error" role="alert">
                      <p>当前钱包不可用，请联系客服核对后再赠送。</p>
                      <Button asChild variant="outline">
                        <Link to="/support">联系客服</Link>
                      </Button>
                    </div>
                  )}
                  <Explanation
                    title="赠礼与冠名说明"
                    returnLabel="返回礼物商城"
                  >
                    礼物可跨款多选，数量上限、限量与价格以当前礼物信息为准。冠名天数随数量累计；确认赠送前请核对收礼陪玩、冠名内容与实际应付。赠送结果未明确时，请先查询原结果并核对礼物单和钱包，避免重复赠送。
                  </Explanation>
                </aside>
              </div>
              {visibleGifts.length > 0 && (
                <section className="boss-gift-dock" aria-label="赠礼结算栏">
                  <div>
                    <div className="boss-gift-dock-amount">
                      <p>已选 {count} 件 · 合计</p>
                      <strong>
                        {money(total)}
                        <small>{coinUnit()}</small>
                      </strong>
                    </div>
                    <Button
                      disabled={!canSend}
                      onClick={() => void run(prepare, "").catch(() => {})}
                    >
                      {busy ? "正在核对…" : intent ? "请先核对赠送" : "送给 TA"}
                    </Button>
                  </div>
                </section>
              )}
            </>
          )}
        </State>
      )}
      {dialog}
    </div>
  )
}
