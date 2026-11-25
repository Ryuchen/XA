import { coinUnit } from "@xa/shared"
import { Check, ChevronRight } from "lucide-react"
import { Link } from "react-router"
import { formatProviderName, ProviderNameLabel } from "./provider-name"
import { Image, money, type Row } from "./ui"

/** The existing boss picker card, also used by the reservation vacancy dialog. */
export function ProviderPickerCard({
  item,
  selected,
  disabled = false,
  storePrefix,
  onToggle,
  detailTo,
}: {
  item: Row
  selected: boolean
  disabled?: boolean
  storePrefix?: string
  onToggle: () => void
  detailTo?: string
}) {
  const name = formatProviderName(item.nickname, storePrefix)
  return <article className={"boss-picker-card" + (selected ? " selected" : "") + (!item.can_order ? " unavailable" : "")}>
    <button
      type="button"
      className="boss-picker-card-main"
      aria-pressed={selected}
      disabled={disabled || (!selected && !item.can_order)}
      onClick={onToggle}
      aria-label={(selected ? "取消选择 " : "选择 ") + name}
    >
      <span className="boss-picker-portrait">
        <Image src={item.avatar} alt={name} />
        {selected && <span><Check size={14} /></span>}
      </span>
      <span className="boss-picker-card-name">
        <strong><ProviderNameLabel value={item.nickname} prefix={storePrefix} /></strong>
        <small>{item.gender === "MALE" ? "男陪" : item.gender === "FEMALE" ? "女陪" : ""}</small>
      </span>
      <span className="boss-picker-card-level">{item.level_name || item.rank || "查看接单资格"}</span>
      {item.monthly_order_amount != null && <span className="boss-picker-monthly"><small>本月接单额</small><strong>{money(item.monthly_order_amount)} <small>{coinUnit()}</small></strong></span>}
      {!item.can_order && <span className="boss-picker-unavailable">{item.order_unavailable_reason || "暂不可约"}</span>}
    </button>
    {item.voice_card_url && <audio className="boss-picker-audio" controls preload="none" src={item.voice_card_url} aria-label={name + "的语音介绍"} />}
    {detailTo && <Link className="boss-picker-card-detail" to={detailTo}>查看详情<ChevronRight size={12} /></Link>}
  </article>
}
