export type ReservationSeatLike = {
  number?: unknown
  status?: unknown
  account_id?: unknown
  name?: unknown
  display_name?: unknown
  previous_provider_name?: unknown
  previous_provider?: { name?: unknown; display_name?: unknown } | null
  [key: string]: unknown
}

export type ReservationLike = {
  id?: unknown
  status?: unknown
  required_provider_count?: unknown
  allowed_actions?: unknown
  service?: { id?: unknown; name?: unknown } | null
  start_time?: unknown
  end_time?: unknown
  seats?: unknown
  [key: string]: unknown
}

export type ReplacementCandidate = {
  id: number
  account_id: number
  nickname?: unknown
  display_name?: unknown
  can_order: true
  [key: string]: unknown
}

export type ReplacementOriginalProvider = {
  account_id: number
  display_name: string
}

/** An unlocked read snapshot, never a reservation/version token. */
export type ReservationReplacementCandidateSnapshot = {
  seatNumber: 1 | 2
  serviceId: number
  startTime: string
  endTime: string
  originalProvider: ReplacementOriginalProvider | null
  list: ReplacementCandidate[]
}

const positiveId = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value > 0

const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value)

function validTime(value: unknown): value is string {
  return typeof value === "string" && value.trim() === value && Number.isFinite(Date.parse(value))
}

function sameInstant(left: string, right: unknown): boolean {
  return validTime(right) && Date.parse(left) === Date.parse(right)
}

export function reservationReplacementScopeIsCurrent({
  reservationId,
  owner,
  store,
  sessionOwner,
  sessionRevision,
  currentStore,
  currentOwner,
  currentRevision,
}: {
  reservationId: number
  owner: string | number | undefined
  store: string
  sessionOwner: string | number | undefined
  sessionRevision: number
  currentStore: string
  currentOwner: string | number | undefined
  currentRevision: number
}): boolean {
  return positiveId(reservationId) && !!owner &&
    currentStore === store &&
    String(currentOwner || "") === String(owner) &&
    String(sessionOwner || "") === String(owner) &&
    currentRevision === sessionRevision
}

export function reservationSeats(reservation: ReservationLike | null | undefined): ReservationSeatLike[] {
  return Array.isArray(reservation?.seats)
    ? reservation.seats.filter((seat): seat is ReservationSeatLike => !!seat && typeof seat === "object")
    : []
}

export function reservationSeat(
  reservation: ReservationLike | null | undefined,
  seatNumber: number,
): ReservationSeatLike | null {
  return reservationSeats(reservation).find(seat => seat.number === seatNumber) ?? null
}

/** The action hint alone is insufficient: the requested seat must still be vacant. */
export function canOpenReservationReplacement(
  reservation: ReservationLike | null | undefined,
  seatNumber: number,
): boolean {
  const allowed = Array.isArray(reservation?.allowed_actions) ? reservation.allowed_actions : []
  return reservation?.status === "PENDING" &&
    reservation.required_provider_count === 2 &&
    allowed.includes("replace_provider") &&
    reservationSeat(reservation, seatNumber)?.status === "VACANT"
}

export function vacantReservationReplacementSeats(
  reservation: ReservationLike | null | undefined,
): ReservationSeatLike[] {
  return reservationSeats(reservation).filter(seat =>
    (seat.number === 1 || seat.number === 2) &&
    canOpenReservationReplacement(reservation, seat.number))
}

export function reservationReplacementCandidatePath(reservationId: number, seatNumber: number): string {
  if (!positiveId(reservationId) || (seatNumber !== 1 && seatNumber !== 2))
    throw new Error("预约或席位编号无效，无法读取补位候选")
  return `/orders/reservations/${reservationId}/replacement-candidates/?seat_number=${seatNumber}`
}

function errorStatus(error: unknown): number {
  const value = error as { status?: unknown; code?: unknown; body?: { code?: unknown } }
  return [value?.body?.code, value?.code, value?.status]
    .find((code): code is number => typeof code === "number" && code >= 400) ?? 0
}

export function reservationReplacementCandidateReadMessage(error: unknown): string {
  const status = errorStatus(error)
  const reason = error instanceof Error && error.message ? error.message : "服务未返回可用候选"
  if (status === 403) return `无权读取补位候选（403）。${reason}。请核对当前账号或重新登录。`
  if (status === 404) return `预约或补位候选不可见（404）。${reason}。请返回预约列表核对，不要提交旧意图。`
  if (status === 409) return `预约或空席状态已变化（409）。${reason}。请刷新预约后重新读取候选。`
  if (status === 400) return `补位候选请求未被接受（400）。${reason}。请关闭后重新打开补位。`
  if (status === 401) return `登录状态已变化（401）。${reason}。请重新登录后再读取候选。`
  return `候选快照网络读取失败。${reason}。尚未发起补位邀请，请检查网络或服务状态后重试。`
}

export function parseReservationReplacementCandidateSnapshot(
  value: unknown,
  reservation: ReservationLike,
  seatNumber: 1 | 2,
): ReservationReplacementCandidateSnapshot {
  if (!record(value)) throw new Error("补位候选快照格式无效，请重新读取")
  const reservationId = reservation.id
  const serviceId = reservation.service?.id
  if (!positiveId(reservationId) || !positiveId(serviceId) ||
      !validTime(reservation.start_time) || !validTime(reservation.end_time) ||
      Date.parse(reservation.end_time) <= Date.parse(reservation.start_time)) {
    throw new Error("原预约资料不完整，无法核对补位候选快照")
  }
  if (value.seat_number !== seatNumber || value.service_id !== serviceId)
    throw new Error("补位候选快照的预约席位或服务不一致，已拒绝使用")
  if (!validTime(value.start_time) || !validTime(value.end_time) ||
      !sameInstant(value.start_time, reservation.start_time) ||
      !sameInstant(value.end_time, reservation.end_time)) {
    throw new Error("补位候选快照未匹配原预约完整起止时间，已拒绝使用")
  }

  let originalProvider: ReplacementOriginalProvider | null = null
  if (value.original_provider !== null) {
    if (!record(value.original_provider) ||
        !positiveId(value.original_provider.account_id) ||
        typeof value.original_provider.display_name !== "string" ||
        !value.original_provider.display_name.trim()) {
      throw new Error("原陪玩公开资料格式无效，不能猜测原陪玩身份")
    }
    originalProvider = {
      account_id: value.original_provider.account_id,
      display_name: value.original_provider.display_name,
    }
  }
  if (!Array.isArray(value.list)) throw new Error("补位候选列表格式无效，不能按空列表处理")
  const occupied = new Set(reservationSeats(reservation)
    .map(seat => seat.account_id)
    .filter(positiveId))
  const seen = new Set<number>()
  const list = value.list.map((item, index): ReplacementCandidate => {
    if (!record(item) || !positiveId(item.id) || !positiveId(item.account_id) ||
        item.id !== item.account_id || item.can_order !== true ||
        typeof item.nickname !== "string" || typeof item.display_name !== "string" ||
        occupied.has(item.account_id) || seen.has(item.account_id)) {
      throw new Error(`第 ${index + 1} 位补位候选资料无效或与另一席重复，已拒绝使用快照`)
    }
    seen.add(item.account_id)
    return { ...item, id: item.account_id, account_id: item.account_id, can_order: true }
  })
  return {
    seatNumber,
    serviceId,
    startTime: value.start_time,
    endTime: value.end_time,
    originalProvider,
    list,
  }
}

export async function readReservationReplacementCandidates(
  get: (path: string) => Promise<unknown>,
  reservation: ReservationLike,
  seatNumber: 1 | 2,
): Promise<ReservationReplacementCandidateSnapshot> {
  if (!canOpenReservationReplacement(reservation, seatNumber))
    throw new Error("当前预约或席位已不允许补位，请刷新预约")
  const reservationId = reservation.id
  if (!positiveId(reservationId)) throw new Error("预约编号无效，无法读取补位候选")
  let value: unknown
  try {
    value = await get(reservationReplacementCandidatePath(reservationId, seatNumber))
  } catch (error) {
    throw new Error(reservationReplacementCandidateReadMessage(error))
  }
  return parseReservationReplacementCandidateSnapshot(value, reservation, seatNumber)
}

export function reservationReplacementCandidateForTarget(
  snapshot: ReservationReplacementCandidateSnapshot,
  targetProviderAccountId: number,
  seatNumber: 1 | 2,
): ReplacementCandidate | null {
  if (snapshot.seatNumber !== seatNumber) return null
  return snapshot.list.find(candidate =>
    candidate.account_id === targetProviderAccountId && candidate.can_order === true) ?? null
}

export function reservationReplacementOriginalName(seat: ReservationSeatLike | null | undefined): string {
  if (!seat) return ""
  const values = [
    seat.previous_provider?.display_name,
    seat.previous_provider?.name,
    seat.previous_provider_name,
    seat.display_name,
    seat.name,
  ]
  return values.find(value => typeof value === "string" && value.trim())?.toString().trim() || ""
}

export function reservationReplacementProviderName(seat: ReservationSeatLike | null | undefined): string {
  if (!seat) return ""
  return [seat.display_name, seat.name]
    .find(value => typeof value === "string" && value.trim())?.toString().trim() || ""
}

export function reservationReplacementOtherSeat(
  reservation: ReservationLike,
  seatNumber: number,
): ReservationSeatLike | null {
  return reservationSeat(reservation, seatNumber === 1 ? 2 : 1)
}

function seatDecisionSnapshot(seat: ReservationSeatLike | null) {
  if (!seat) return null
  return {
    number: seat.number,
    status: seat.status,
    account_id: seat.account_id,
    name: seat.name,
  }
}

/** A new invitation must be reviewed again if its service, window or other seat changed. */
export function reservationReplacementContextChanged(
  before: ReservationLike,
  latest: ReservationLike,
  seatNumber: number,
): boolean {
  return before.id !== latest.id ||
    before.required_provider_count !== latest.required_provider_count ||
    before.service?.id !== latest.service?.id ||
    before.start_time !== latest.start_time ||
    before.end_time !== latest.end_time ||
    JSON.stringify(seatDecisionSnapshot(reservationReplacementOtherSeat(before, seatNumber))) !==
      JSON.stringify(seatDecisionSnapshot(reservationReplacementOtherSeat(latest, seatNumber)))
}

export function reservationSeatMatchesReplacement(
  reservation: ReservationLike | null | undefined,
  reservationId: number,
  seatNumber: number,
  targetProviderAccountId: number,
): boolean {
  const seat = reservationSeat(reservation, seatNumber)
  return reservation?.id === reservationId &&
    reservation.required_provider_count === 2 &&
    seat?.account_id === targetProviderAccountId &&
    (seat.status === "PENDING" || seat.status === "CONFIRMED")
}
