import {
  createReservationRequest,
  executeReservationRequest,
  getSession,
  getStoreSlug,
  readReservationRequest,
  reservationRequestNotice,
  type ReservationRequestIntent,
  type ReservationRequestSnapshot,
} from "@xa/shared"
import { useState } from "react"
import "./reservation-replacement.css"
import { CalendarClock, LoaderCircle, UserRoundCheck, UsersRound } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { formatProviderName, ProviderNameLabel } from "./provider-name"
import { ProviderPickerCard } from "./provider-picker-card"
import { EmptySurface } from "./visual-states"
import { api, date, Panel, useLoad, type Row } from "./ui"
import {
  canOpenReservationReplacement,
  readReservationReplacementCandidates,
  reservationReplacementCandidateForTarget,
  reservationReplacementContextChanged,
  reservationReplacementOriginalName,
  reservationReplacementOtherSeat,
  reservationReplacementProviderName,
  reservationReplacementScopeIsCurrent,
  reservationSeats,
  reservationSeatMatchesReplacement,
  type ReplacementCandidate,
  type ReservationLike,
  type ReservationReplacementCandidateSnapshot,
  type ReservationSeatLike,
} from "./reservation-replacement-model"

const seatStatus: Record<string, string> = {
  VACANT: "待补位",
  PENDING: "等待确认",
  CONFIRMED: "已确认",
}

const targetName = (candidate: ReplacementCandidate | null | undefined, storePrefix?: string) =>
  formatProviderName(candidate?.display_name || candidate?.nickname, storePrefix)

class ReplacementPrecheckError extends Error {}

function candidateErrorTitle(error: string): string {
  if (error.includes("（403）")) return "无权读取补位候选"
  if (error.includes("（404）")) return "预约或候选不可见"
  if (error.includes("（409）")) return "候选快照与预约状态冲突"
  if (error.includes("网络读取失败")) return "候选快照暂未读取成功"
  return "候选陪玩读取失败"
}

export function ReservationReplacementOriginalProvider({
  seat,
  snapshot,
  storePrefix,
}: {
  seat: ReservationSeatLike | null
  snapshot: ReservationReplacementCandidateSnapshot | null
  storePrefix?: string
}) {
  const matchingSnapshot = snapshot && seat?.number === snapshot.seatNumber ? snapshot : null
  const name = matchingSnapshot
    ? matchingSnapshot.originalProvider?.display_name || ""
    : reservationReplacementOriginalName(seat)
  return name
    ? <ProviderNameLabel value={name} prefix={storePrefix} />
    : <>原身份未随空席返回</>
}

export function ReservationReplacementCandidateResults({
  loading,
  error,
  candidates,
  selectedId,
  disabled,
  storePrefix,
  onSelect,
  reload,
}: {
  loading: boolean
  error?: string
  candidates: ReplacementCandidate[]
  selectedId: number | null
  disabled: boolean
  storePrefix?: string
  onSelect: (id: number) => void
  reload: () => void
}) {
  if (loading) return <div className="boss-replacement-candidate-state" role="status"><LoaderCircle size={20} className="animate-spin" /><p>正在读取本店候选陪玩…</p></div>
  if (error) return <div className="boss-feedback" role="alert"><strong>{candidateErrorTitle(error)}</strong><p>{error}</p><p>尚未发起补位邀请，请按提示核对后重试。</p><Button variant="outline" disabled={disabled} onClick={reload}>重新读取候选</Button></div>
  if (!candidates.length) return <EmptySurface page="companion-picker" title="暂无可用补位候选" description="服务端在本次只读快照中未返回符合该服务、完整预约时段和档期冲突规则的候选。空列表是真实结果，可稍后重读或联系客服。" />
  return <div className="boss-companion-list boss-picker-list boss-replacement-candidate-list" aria-label="补位候选陪玩">
    {candidates.map(candidate => <ProviderPickerCard
      key={candidate.account_id}
      item={candidate}
      selected={selectedId === candidate.account_id}
      disabled={disabled}
      storePrefix={storePrefix}
      onToggle={() => onSelect(candidate.account_id)}
    />)}
  </div>
}

export function ReservationReplacementPanel({
  reservation,
  requestKey,
  owner,
  snapshot,
  busy,
  operationPending,
  storePrefix,
  run,
  onSnapshot,
  onNotice,
  onRefresh,
}: {
  reservation: Row | null
  requestKey: string
  owner: string | number | undefined
  snapshot: ReservationRequestSnapshot
  busy: boolean
  operationPending: boolean
  storePrefix?: string
  run: (action: () => Promise<unknown>, success?: string) => Promise<unknown>
  onSnapshot: (snapshot: ReservationRequestSnapshot) => void
  onNotice: (message: string) => void
  onRefresh: () => Promise<Row | null>
}) {
  const [seatNumber, setSeatNumber] = useState<1 | 2 | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const reservationId = typeof reservation?.id === "number" ? reservation.id : 0
  const original = snapshot.intent?.action === "replace_provider" ? snapshot.intent : null
  const sessionRevision = getSession("boss").revision
  const storeSlug = getStoreSlug()
  const candidateLoad = useLoad<ReservationReplacementCandidateSnapshot | null>(async () => {
    if (seatNumber === null || !reservation) return null
    return readReservationReplacementCandidates(
      path => api.get<unknown>(path),
      reservation as ReservationLike,
      seatNumber,
    )
  }, [seatNumber, requestKey, reservationId, reservation?.updated_at], false)
  const candidateSnapshot = candidateLoad.data
  const candidates = candidateSnapshot?.list ?? []
  const selectedCandidate = candidates.find(candidate => candidate.account_id === selectedId) ?? null
  const selectedSeat = seatNumber === null ? null : reservationSeats(reservation as ReservationLike).find(seat => seat.number === seatNumber) ?? null
  const otherSeat = seatNumber === null || !reservation ? null : reservationReplacementOtherSeat(reservation as ReservationLike, seatNumber)
  const showSeats = reservation?.required_provider_count === 2 && reservationSeats(reservation as ReservationLike).length > 0

  function assertCurrentScope() {
    const session = getSession("boss")
    if (!reservationReplacementScopeIsCurrent({
      reservationId,
      owner,
      store: storeSlug,
      sessionOwner: owner,
      sessionRevision,
      currentStore: getStoreSlug(),
      currentOwner: session.user?.id,
      currentRevision: session.revision,
    })) {
      throw new ReplacementPrecheckError("门店或登录账号已切换，旧补位意图未提交；请重新读取当前预约")
    }
  }

  async function refreshOnly() {
    await onRefresh()
    onSnapshot(readReservationRequest(localStorage, requestKey))
  }

  async function sendReplacement(originalRequest?: ReservationRequestIntent) {
    const replacing = originalRequest || original
    if (replacing && (replacing.review || replacing.blocked))
      throw new ReplacementPrecheckError("原补位请求仍待核对，不能换号、换席位或换候选人")
    const target = replacing?.targetProviderAccountId ?? selectedCandidate?.account_id ?? null
    const number = replacing?.seatNumber ?? seatNumber
    if (!reservation || !target || (number !== 1 && number !== 2)) {
      onNotice("请先选择一位候选陪玩，再确认补位。")
      return
    }
    if (replacing && (replacing.reservationId !== reservationId || !target)) {
      throw new ReplacementPrecheckError("原补位意图与当前预约不一致，未提交")
    }

    let attempted = false
    let succeeded = false
    let invitedName = replacing ? `账号 #${target}` : targetName(selectedCandidate, storePrefix)
    try {
      try {
        assertCurrentScope()
        const latest = await onRefresh()
        assertCurrentScope()
        if (!latest) throw new Error("预约读取失败，请重新加载")
        const alreadyInvited = reservationSeatMatchesReplacement(
          latest as ReservationLike,
          reservationId,
          number,
          target,
        )
        if (!alreadyInvited) {
          if (!canOpenReservationReplacement(latest as ReservationLike, number))
            throw new Error(replacing
              ? "原补位席位或预约状态已变化，原请求仍保留，未重复提交"
              : "当前席位已不允许补位，请重新核对预约")
          if (reservationReplacementContextChanged(
            reservation as ReservationLike,
            latest as ReservationLike,
            number,
          )) throw new Error("预约服务、时段或另一席已变化，原请求未提交，请重新核对")
          const latestCandidates = await readReservationReplacementCandidates(
            path => api.get<unknown>(path),
            latest as ReservationLike,
            number,
          )
          assertCurrentScope()
          const provider = reservationReplacementCandidateForTarget(latestCandidates, target, number)
          if (!provider) throw new Error(replacing
            ? "原目标已不在最新候选快照中，原请求仍保留，未重复提交"
            : "候选陪玩已失效，请从最新名单重新选择")
          invitedName = targetName(provider, storePrefix) || invitedName
        }
      } catch (error) {
        if (error instanceof ReplacementPrecheckError) throw error
        throw new ReplacementPrecheckError((error as Error)?.message || "提交前核对失败，尚未发起邀请")
      }

      const request = replacing || createReservationRequest({
        role: "boss",
        accountId: owner!,
        action: "replace_provider",
        reservationId,
        seatNumber: number,
        targetProviderAccountId: target,
        payload: { seat_number: number, provider_account_id: target },
        store: storeSlug,
      })
      attempted = true
      await executeReservationRequest({
        storage: localStorage,
        key: requestKey,
        request,
        post: (path, payload) => {
          assertCurrentScope()
          return api.post<Row>(path, payload)
        },
        acceptsResult: result => reservationSeatMatchesReplacement(
          result as ReservationLike,
          reservationId,
          number,
          target,
        ),
      })
      succeeded = true
      setSeatNumber(null)
      setSelectedId(null)
      onNotice(`已邀请 ${invitedName} 补位，另一席保持不变。`)
      try {
        await onRefresh()
      } catch {
        onNotice(`已邀请 ${invitedName} 补位，但预约刷新失败；请重新加载核对，另一席未被本操作修改。`)
      }
    } catch (error) {
      if (attempted) {
        setSeatNumber(null)
        setSelectedId(null)
      }
      const message = error instanceof ReplacementPrecheckError
        ? `补位邀请未提交：${error.message}`
        : (() => {
            const notice = reservationRequestNotice(error)
            return `${notice.title}：${notice.detail}`
          })()
      onNotice(message)
      throw error
    } finally {
      onSnapshot(readReservationRequest(localStorage, requestKey))
      if (attempted && !succeeded) {
        try { await onRefresh() } catch { /* Keep the original request error visible. */ }
      }
    }
  }

  if (!showSeats && !original) return null
  const recoveryBlocked = !!original?.review || !!original?.blocked
  return <>
    {original && <div className="boss-feedback" role="alert">
      <strong>{recoveryBlocked ? "补位结果待核对" : "补位结果未知"}</strong>
      <p>原请求：预约 #{original.reservationId} · 席位 {original.seatNumber} · 目标陪玩账号 #{original.targetProviderAccountId}</p>
      <p>请求编号 {original.decisionKey}</p>
      <p>{recoveryBlocked
        ? "原请求已锁定，不能换号或改成另一席位、候选人。请刷新预约或联系客服核对。"
        : "只能按原预约、原席位和原目标同号恢复；候选人失效或账号切换时不会提交旧意图。"}</p>
      <div className="boss-actions">
        {!recoveryBlocked && <Button disabled={busy || !reservation} onClick={() => void run(() => sendReplacement(original), "").catch(() => {})}>同号重试原请求</Button>}
        <Button variant="outline" disabled={busy} onClick={() => void run(refreshOnly, "").catch(() => {})}>刷新预约记录</Button>
      </div>
    </div>}
    {showSeats && <Panel icon={UsersRound} title="双陪席位" className="boss-reservation-seats-panel">
      <p className="boss-reservation-seats-note">一人拒绝或超时后，只补空出的席位；另一席继续保留。</p>
      <div className="boss-reservation-seats">
        {reservationSeats(reservation as ReservationLike).map(seat => {
          const number = seat.number === 1 || seat.number === 2 ? seat.number : 0
          const vacant = seat.status === "VACANT"
          const currentName = reservationReplacementProviderName(seat)
          return <article key={String(seat.number)} className="boss-reservation-seat" data-status={String(seat.status || "UNKNOWN")}>
            <div className="boss-reservation-seat-heading"><strong>席位 {number || "未知"}</strong><span>{seatStatus[String(seat.status)] || "状态待核"}</span></div>
            <p>{vacant ? "当前空席" : currentName ? <ProviderNameLabel value={currentName} prefix={storePrefix} /> : "陪玩身份待核"}</p>
            {vacant && <small>原陪玩：<ReservationReplacementOriginalProvider seat={seat} snapshot={candidateSnapshot} storePrefix={storePrefix} /></small>}
            {seat.status === "CONFIRMED" && <small>该席保留，不在本次替换范围内</small>}
            {number > 0 && vacant && canOpenReservationReplacement(reservation as ReservationLike, number) && <Button
              variant="outline"
              disabled={busy || operationPending}
              onClick={() => { setSelectedId(null); setSeatNumber(number as 1 | 2) }}
            >重选陪玩</Button>}
          </article>
        })}
      </div>
    </Panel>}
    <Dialog open={seatNumber !== null} onOpenChange={open => { if (!open && !busy) { setSeatNumber(null); setSelectedId(null) } }}>
      <DialogContent className="boss-replacement-dialog sm:max-w-2xl" closeDisabled={busy} showDefaultFooter={false}>
        <DialogHeader>
          <DialogTitle>为席位 {seatNumber} 重选陪玩</DialogTitle>
          <DialogDescription>仅从本店该服务及原预约完整时段的真实候选快照中选择，不能填写任意账号。提交前会重读预约和候选，最终由服务端锁内复核。</DialogDescription>
        </DialogHeader>
        <div className="boss-replacement-context">
          <div><span>当前替换席</span><strong>席位 {seatNumber}</strong><small>原陪玩：<ReservationReplacementOriginalProvider seat={selectedSeat} snapshot={candidateSnapshot} storePrefix={storePrefix} /></small></div>
          <div><span>另一席保留</span><strong>{reservationReplacementProviderName(otherSeat) || "身份待核"}</strong><small>{otherSeat?.status === "CONFIRMED" ? "已确认，不会被替换" : "不在本次操作范围内"}</small></div>
          <div><span>预约时段</span><strong>{date(candidateSnapshot?.startTime || reservation?.start_time)}</strong><small>至 {date(candidateSnapshot?.endTime || reservation?.end_time)}</small></div>
        </div>
        <p className="boss-replacement-rule"><CalendarClock size={16} />候选是无锁只读快照，不是预约令牌，也不会保留档期。本次只邀请空席陪玩，不修改服务、原预约时段、金额或另一席。</p>
        {seatNumber !== null && reservation && !canOpenReservationReplacement(reservation as ReservationLike, seatNumber)
          ? <div className="boss-feedback" role="alert"><strong>当前已无损位权限</strong><p>预约动作或空席状态已变化，请关闭后刷新预约。</p></div>
          : <ReservationReplacementCandidateResults
              loading={candidateLoad.loading}
              error={candidateLoad.error}
              candidates={candidates}
              selectedId={selectedId}
              disabled={busy || operationPending || candidateLoad.loading}
              storePrefix={storePrefix}
              reload={candidateLoad.reload}
              onSelect={id => setSelectedId(current => current === id ? null : id)}
            />}
        {selectedId !== null && !selectedCandidate && !candidateLoad.loading && <p role="alert" className="boss-replacement-selection-error">原候选已失效，请从最新名单重新选择。</p>}
        <div className="boss-replacement-target" aria-live="polite"><UserRoundCheck size={18} /><div><span>候选目标</span><strong>{selectedCandidate ? targetName(selectedCandidate, storePrefix) : "尚未选择"}</strong></div></div>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={() => { setSeatNumber(null); setSelectedId(null) }}>返回预约</Button>
          <Button disabled={busy || operationPending || !selectedCandidate || !!candidateLoad.error} onClick={() => void run(() => sendReplacement(), "").catch(() => {})}>
            {busy && <LoaderCircle size={16} className="animate-spin" />}{busy ? "正在核对并邀请…" : "确认邀请补位"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </>
}
