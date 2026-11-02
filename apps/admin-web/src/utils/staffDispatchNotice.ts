type NoticeOrder = {
  id?: number
  dispatch_route?: string
  status?: string
  payment_status?: string
  reject_count?: number
  auto_cancel_at?: string | null
  staff_dispatch_actionable?: boolean
}

type Dependencies = {
  session: () => number
  allowed: () => boolean
  blocked?: () => boolean
  retrieve: (id: number) => Promise<NoticeOrder>
  listPending: () => Promise<{ list: NoticeOrder[]; total: number; staff_queue_needs_review?: boolean }>
  show: (open: () => void, closed: () => void) => { close: () => void }
  open: () => void
  queue: (status: 'idle' | 'loading' | 'ready' | 'review' | 'error', count: number | null) => void
  schedule?: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>
  cancel?: (timer: ReturnType<typeof setTimeout>) => void
}

function awaitingStaff(order: NoticeOrder): boolean {
  return order.dispatch_route === 'STAFF' && order.status === 'PENDING'
    && order.payment_status === 'PAID'
    // Server checks expiry using its clock; workstation clocks may be wrong.
    && order.staff_dispatch_actionable !== false
}

/** Live events are hints. Re-read current order before showing a notice. */
export function createStaffDispatchNotice(deps: Dependencies) {
  let generation = 0
  const seen = new Set<string>()
  const checking = new Set<string>()
  const versions = new Map<number, number>()
  let notice: { close: () => void } | null = null
  let noticeVersion = 0
  let deferred = false
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let retryAttempt = 0
  let scanning = false
  let scanAgain = false
  let scanAgainForce = false
  let queueReady = false
  let eventTimer: ReturnType<typeof setTimeout> | null = null
  let pollTimer: ReturnType<typeof setTimeout> | null = null

  function cancelRetry() {
    if (retryTimer !== null) (deps.cancel || clearTimeout)(retryTimer)
    retryTimer = null
  }

  function cancelTimer(timer: ReturnType<typeof setTimeout> | null) {
    if (timer !== null) (deps.cancel || clearTimeout)(timer)
  }

  function poll() {
    cancelTimer(pollTimer)
    pollTimer = null
    if (!deps.allowed()) return
    pollTimer = (deps.schedule || setTimeout)(() => {
      pollTimer = null
      void reconcile(false)
    }, 60000)
  }

  function invalidateQueue() {
    if (!deps.allowed() || eventTimer !== null || retryTimer !== null) return
    eventTimer = (deps.schedule || setTimeout)(() => {
      eventTimer = null
      void reconcile(false)
    }, 500)
  }

  function retry() {
    if (!deps.allowed() || retryTimer !== null) return
    cancelTimer(pollTimer)
    cancelTimer(eventTimer)
    pollTimer = null
    eventTimer = null
    const delay = Math.min(30000, 3000 * 2 ** Math.min(retryAttempt++, 4))
    retryTimer = (deps.schedule || setTimeout)(() => {
      retryTimer = null
      void reconcile(true)
    }, delay)
  }

  function reset() {
    generation += 1
    seen.clear()
    checking.clear()
    versions.clear()
    cancelRetry()
    cancelTimer(eventTimer)
    cancelTimer(pollTimer)
    eventTimer = pollTimer = null
    retryAttempt = 0
    scanning = false
    scanAgain = false
    scanAgainForce = false
    queueReady = false
    deferred = false
    deps.queue('idle', null)
    noticeVersion += 1
    notice?.close()
    notice = null
  }

  async function receive(event: NoticeOrder, reconciling = false, forceNotice = false): Promise<boolean> {
    if (!deps.allowed() || !Number.isSafeInteger(event?.id) || !event.id || event.id < 1) return false
    if (!reconciling && event.dispatch_route === 'STAFF') invalidateQueue()
    // One visible alert represents the queue. Coalesce bursts while the first
    // order is being checked; the queue scan still accounts for every order.
    if (!reconciling && awaitingStaff(event) && (notice || checking.size)) return true
    const id = event.id
    const key = `${id}:${event.reject_count || 0}`
    if (awaitingStaff(event) && (checking.has(key) || (seen.has(key) && (!forceNotice || notice)))) return true
    const version = (versions.get(id) || 0) + 1
    versions.set(id, version)
    if (!awaitingStaff(event)) return false
    const currentGeneration = generation
    const session = deps.session()
    checking.add(key)
    try {
      const order = await deps.retrieve(id)
      if (generation !== currentGeneration || session !== deps.session() || !deps.allowed()
          || versions.get(id) !== version || order.id !== id || !awaitingStaff(order)
          || (order.reject_count || 0) !== (event.reject_count || 0)) return false
      seen.add(key)
      if (seen.size > 500) seen.delete(seen.values().next().value!)
      if (deps.blocked?.()) { deferred = true; return true }
      if (!notice) {
        const thisNotice = ++noticeVersion
        notice = deps.show(() => {
          if (generation === currentGeneration && session === deps.session() && deps.allowed()) {
            if (deps.blocked?.()) deferred = true
            else deps.open()
          }
        }, () => { if (noticeVersion === thisNotice) notice = null })
      }
      return true
    } catch {
      if (generation === currentGeneration && session === deps.session()) retry()
      return false
    } finally {
      if (generation === currentGeneration) checking.delete(key)
    }
  }

  // WebSocket messages are hints, not a durable queue. Reconcile when the
  // socket connects or permissions become available, and after failed reads.
  async function reconcile(forceNotice = true) {
    if (!deps.allowed()) return
    if (scanning) { scanAgain = true; scanAgainForce ||= forceNotice; return }
    const currentGeneration = generation
    const session = deps.session()
    scanning = true
    if (!queueReady) deps.queue('loading', null)
    try {
      const page = await deps.listPending()
      if (generation !== currentGeneration || session !== deps.session() || !deps.allowed()) return
      if (!Array.isArray(page?.list) || !Number.isSafeInteger(page.total) || page.total < 0
          || (page.total > 0 && !page.list.length)) throw new Error('待派队列响应无效')
      // Older or malformed backends may omit the completeness marker.  A
      // missing/invalid marker cannot substantiate an exact queue total.
      const needsReview = typeof page.staff_queue_needs_review !== 'boolean' || page.staff_queue_needs_review
      deps.queue(needsReview ? 'review' : 'ready', page.total)
      queueReady = true
      const resolved = !page.list.length || await receive(page.list[0], true, forceNotice)
      if (generation !== currentGeneration || session !== deps.session() || !deps.allowed()) return
      if (resolved) { retryAttempt = 0; cancelRetry(); poll() }
      else retry()
    } catch {
      if (generation === currentGeneration && session === deps.session() && deps.allowed()) {
        queueReady = false
        deps.queue('error', null)
        retry()
      }
    } finally {
      if (generation === currentGeneration) {
        scanning = false
        if (scanAgain) {
          const force = scanAgainForce
          scanAgain = scanAgainForce = false
          void reconcile(force)
        }
      }
    }
  }

  function modalChanged() {
    if (deps.blocked?.()) {
      if (notice) {
        deferred = true
        noticeVersion += 1
        notice.close()
        notice = null
      }
    } else if (deferred && deps.allowed()) {
      deferred = false
      void reconcile(true)
    }
  }

  return { receive, reconcile, reset, modalChanged }
}
