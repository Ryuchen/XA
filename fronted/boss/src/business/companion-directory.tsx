import { useEffect, useRef, useState, type ReactNode } from "react"
import { Link } from "react-router"
import { AlertCircle, AudioLines, CircleOff, LoaderCircle, MapPin, Play, RefreshCw, ShieldCheck } from "lucide-react"
import { coinUnit, useCurrentStore } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Image, money, rows, type Row } from "./ui"
import { formatProviderName, ProviderName } from "./provider-name"

export function DirectoryFilter({ label, value, onChange, options, disabled = false }: {
  label: string
  value: unknown
  onChange: (value: string) => void
  options: Row[]
  disabled?: boolean
}) {
  const empty = "__directory_all__"
  return <Select disabled={disabled} value={String(value ?? "") || empty} onValueChange={next => onChange(next === empty ? "" : next)}>
    <SelectTrigger className="boss-directory-filter" aria-label={label}>
      <span><span className="boss-directory-filter-label">{label}</span><SelectValue /></span>
    </SelectTrigger>
    <SelectContent>{options.map((item, index) => <SelectItem className="min-h-11" key={String(item.value ?? item.id ?? index)} value={String(item.value ?? item.id ?? "") || empty}>{item.label ?? item.name ?? item.title}</SelectItem>)}</SelectContent>
  </Select>
}

export function DirectoryState({ data, loading, error, empty, reload, emptyAction, children }: {
  data: unknown
  loading: boolean
  error?: string
  empty?: boolean
  reload: () => unknown
  emptyAction?: ReactNode
  children: ReactNode
}) {
  if (loading && data == null) return <div className="boss-companion-list boss-directory-skeleton" aria-label="正在加载陪玩列表" aria-busy="true">{[1, 2, 3].map(key => <section key={key}><div><Skeleton className="size-14 shrink-0 rounded-full" /><div><Skeleton className="h-5 w-28" /><Skeleton className="h-4 w-40" /></div></div><Skeleton className="mt-4 h-8 w-full" /><Skeleton className="mt-3 h-5 w-32" /></section>)}</div>
  if (error && data == null) return <section role="alert" className="boss-directory-state has-error"><AlertCircle className="size-7" /><h3>陪玩列表暂时没有加载成功</h3><p>{error}。已选择的筛选条件会保留，请重试。</p><Button type="button" variant="outline" className="mt-5 h-11 w-full" onClick={() => void reload()}><RefreshCw className="size-4" />重新加载</Button></section>
  if (!loading && !error && empty) return <section className="boss-directory-state"><CircleOff className="size-7" /><h3>暂时没有符合条件的陪玩</h3><p>试试放宽游戏、语音或资格筛选。</p>{emptyAction}</section>
  return <>{error && <section className="boss-directory-read-notice" role="alert"><p>{error}。当前保留上次读取结果。</p><Button type="button" variant="outline" onClick={() => void reload()}>重新加载</Button></section>}{loading && <p role="status" className="boss-directory-loading"><LoaderCircle className="size-4 animate-spin" />正在更新陪玩列表…</p>}{children}</>
}

export function DirectoryCard({ item, to, activeVoiceId, onVoiceChange }: { item: Row; to: string; activeVoiceId: number | null; onVoiceChange: (id: number | null) => void }) {
  const store = useCurrentStore()
  const name = formatProviderName(item.nickname, store.provider_name_prefix)
  const audio = useRef<HTMLAudioElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  useEffect(() => {
    const element = audio.current
    return () => element?.pause()
  }, [item.voice_card_url])
  useEffect(() => {
    if (activeVoiceId !== item.id) { audio.current?.pause(); setExpanded(false) }
  }, [activeVoiceId, item.id])
  async function playVoice() {
    const element = audio.current
    if (!element || loading) return
    if (playing) { element.pause(); setExpanded(false); onVoiceChange(null); return }
    onVoiceChange(item.id)
    setExpanded(true)
    setLoading(true)
    if (error) element.load()
    setError("")
    try { await element.play() }
    catch { setError("语音暂时无法播放，请稍后重试。") }
    finally { setLoading(false) }
  }
  function closeVoice() {
    audio.current?.pause()
    setExpanded(false)
    if (activeVoiceId === item.id) onVoiceChange(null)
  }
  return <article className="boss-companion-card boss-directory-card">
    <div className="boss-companion-identity">
      <Link className="boss-directory-portrait" to={to} aria-label={`查看陪玩 ${name} 的详情`}>
        {item.avatar ? <Image src={item.avatar} alt={name} className="boss-companion-avatar" /> : <span>{String(item.nickname || "陪").slice(0, 1)}</span>}
      </Link>
      <Link className="boss-companion-name" to={to} title={item.bio || undefined}>
        <span><strong><ProviderName value={item.nickname} /></strong><Badge variant="outline" className={item.can_order ? "boss-available" : "boss-directory-unavailable"}>{item.can_order ? "可约" : "暂不可约"}</Badge></span>
        <small>{[item.level_name, item.gender === "MALE" ? "男陪" : item.gender === "FEMALE" ? "女陪" : ""].filter(Boolean).join(" · ") || "服务资料待完善"}</small>
      </Link>
      {item.voice_card_url ? <Button type="button" variant={playing ? "default" : "outline"} size="icon" className="boss-directory-voice" disabled={loading} aria-busy={loading} aria-label={`${playing ? "暂停" : "播放"}${name}的语音介绍`} onClick={() => void playVoice()}>{loading ? <LoaderCircle className="size-4 animate-spin" /> : playing ? <span className="boss-voice-wave" aria-hidden="true">{[6, 12, 20, 9, 15, 6].map((height, index) => <i key={index} style={{ height, animationDelay: `${index * 80}ms` }} />)}</span> : <Play className="size-4" />}</Button> : <span className="boss-directory-no-voice" aria-label="暂无语音介绍"><CircleOff className="size-4" /></span>}
    </div>
    {item.voice_card_url && <audio ref={audio} preload="none" src={item.voice_card_url} className="sr-only" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => { setPlaying(false); setExpanded(false); if (activeVoiceId === item.id) onVoiceChange(null) }} onError={() => { setLoading(false); setPlaying(false); setError("语音暂时无法播放，请稍后重试。") }} />}
    {expanded && <div className={"boss-directory-voice-status" + (error ? " has-error" : "")} role={error ? "alert" : "status"}>
      <AudioLines className="size-4" /><span>{error || (loading ? "正在加载语音…" : playing ? "正在播放语音介绍" : "语音已暂停")}</span><Button type="button" variant="ghost" className="h-11 px-2 text-xs" onClick={closeVoice}>关闭</Button>
    </div>}
    {rows(item.display_tags).length > 0 && <div className="boss-companion-tags boss-directory-tags">{rows(item.display_tags).slice(0, 3).map((tag, index) => <Badge variant="secondary" className={index === 0 ? "is-primary" : undefined} key={tag.id || tag.key || tag.title}>{tag.title}</Badge>)}</div>}
    <div className="boss-companion-facts">
      {item.rank && <span><ShieldCheck size={14} /><span className="boss-directory-fact-value">{item.rank}</span></span>}
      {item.city && <span><MapPin size={14} /><span className="boss-directory-fact-value">{item.city}</span></span>}
      <span className="boss-directory-games">{(item.game_category_names || []).join("、")}</span>
    </div>
    <div className="boss-companion-prices">
      <div><small>本月接单额</small><strong>{item.monthly_order_amount != null ? <>{money(item.monthly_order_amount)} <small>{coinUnit()}</small></> : "—"}</strong></div>
      <div className="right"><small>参考时价</small><strong className="boss-link-color">{item.hasReferenceHourlyPrice ? <>{money(item.pricePerHour)} <small>{coinUnit()}/小时</small></> : "详询"}</strong></div>
    </div>
  </article>
}
