import { onUnmounted, ref } from 'vue'

type PromptOptions = { title: string; description: string; confirmText: string; reason?: boolean }

export function useReservationPrompt() {
  const open = ref(false), options = ref<PromptOptions>({ title: '', description: '', confirmText: '确认' }), reason = ref('')
  let resolve: ((result: string | null) => void) | null = null
  function ask(next: PromptOptions): Promise<string | null> {
    if (resolve) return Promise.resolve(null)
    options.value = next; reason.value = ''; open.value = true
    return new Promise(done => { resolve = done })
  }
  function settle(value: string | null) { const pending = resolve; resolve = null; open.value = false; pending?.(value) }
  function confirm() { settle(options.value.reason ? reason.value.trim() : '') }
  function cancel() { settle(null) }
  onUnmounted(cancel)
  return { open, options, reason, ask, confirm, cancel }
}
