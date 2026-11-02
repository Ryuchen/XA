import { onUnmounted, ref } from 'vue'

type Prompt = { title: string; description: string; confirmText: string; cancelText: string; reason: boolean }

export function useReportPrompt() {
  const open = ref(false), text = ref(''), error = ref('')
  const prompt = ref<Prompt>({ title: '', description: '', confirmText: '确认', cancelText: '返回', reason: false })
  let resolve: ((value: string | null) => void) | null = null
  function ask(options: Prompt): Promise<string | null> {
    if (resolve) return Promise.resolve(null)
    prompt.value = options; text.value = ''; error.value = ''; open.value = true
    return new Promise(done => { resolve = done })
  }
  function settle(value: string | null) { const done = resolve; resolve = null; open.value = false; done?.(value) }
  function submit() {
    const value = text.value.trim()
    if (prompt.value.reason && (!value || value.length > 255)) { error.value = '驳回原因须为 1–255 字'; return }
    settle(value)
  }
  function cancel() { settle(null) }
  onUnmounted(cancel)
  return { open, text, error, prompt, ask, submit, cancel }
}
