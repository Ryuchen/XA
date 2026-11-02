import { onUnmounted, ref } from 'vue'

type Options = { type?: string; confirmButtonText?: string; cancelButtonText?: string; inputPlaceholder?: string; inputValidator?: (value: string) => boolean | string }

export function useWithdrawalPrompt() {
  const open = ref(false), title = ref(''), message = ref(''), text = ref(''), error = ref('')
  const input = ref(false), confirmText = ref('确认'), cancelText = ref('取消'), placeholder = ref('')
  let pending: { resolve: (value: any) => void; reject: () => void } | null = null
  let validate: Options['inputValidator']
  function show(description: string, heading: string, options: Options = {}, requiresInput = false): Promise<any> {
    if (pending) return Promise.reject(new Error('confirmation already open'))
    title.value = heading; message.value = description; text.value = ''; error.value = ''; input.value = requiresInput
    confirmText.value = options.confirmButtonText || '确认'; cancelText.value = options.cancelButtonText || '取消'
    placeholder.value = options.inputPlaceholder || ''; validate = options.inputValidator; open.value = true
    return new Promise((resolve, reject) => { pending = { resolve, reject: () => reject(new Error('cancelled')) } })
  }
  function close() { const current = pending; pending = null; open.value = false; current?.reject() }
  function submit() {
    const value = text.value.trim(), result = input.value && validate ? validate(value) : true
    if (result !== true) { error.value = typeof result === 'string' ? result : '请填写有效内容'; return }
    const current = pending; pending = null; open.value = false; current?.resolve(input.value ? { value } : undefined)
  }
  onUnmounted(close)
  return { open, title, message, text, error, input, confirmText, cancelText, placeholder, submit, close,
    confirm: (description: string, heading: string, options?: Options) => show(description, heading, options),
    prompt: (description: string, heading: string, options?: Options) => show(description, heading, options, true) }
}
