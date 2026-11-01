import { defineStore } from 'pinia'

export type QueueStatus = 'idle' | 'loading' | 'ready' | 'review' | 'error'

// In-memory only: the authoritative count is refreshed from the server.
export const useStaffDispatchQueue = defineStore('staff-dispatch-queue', {
  state: () => ({ status: 'idle' as QueueStatus, count: null as number | null }),
  actions: {
    update(status: QueueStatus, count: number | null) {
      this.status = status
      this.count = status === 'ready' ? count : null
    },
  },
})
