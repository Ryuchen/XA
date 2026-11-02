import { onUnmounted, ref } from 'vue'

type Options = { title:string; description:string; confirmText?:string; cancelText?:string }

/** Promise-based confirmation for navigation guards and explicit actions. */
export function useConfirmDialog() {
  const open = ref(false)
  const options = ref<Options>({title:'确认操作',description:''})
  let resolve: ((result:boolean) => void) | null = null
  function ask(next:Options):Promise<boolean> {
    if (resolve) return Promise.resolve(false)
    options.value = next; open.value = true
    return new Promise(done => { resolve = done })
  }
  function settle(value:boolean) { const pending = resolve; resolve = null; open.value = false; pending?.(value) }
  onUnmounted(() => settle(false))
  return { open, options, ask, confirm:() => settle(true), cancel:() => settle(false) }
}
