import { onBeforeUnmount, ref, shallowRef, type VNode } from 'vue'

type Options = { [key:string]:unknown; confirmButtonText?:string; cancelButtonText?:string; closeOnClickModal?:boolean; closeOnPressEscape?:boolean; inputType?:string; inputPlaceholder?:string; inputErrorMessage?:string; inputValidator?:(value:string)=>boolean|string }
type Result = { value:string; action:'confirm' }

/** Controlled shadcn dialog for order confirmations, including rich summaries and optional reasons. */
export function useActionDialog() {
  const open=ref(false),title=ref(''),mode=ref<'confirm'|'prompt'>('confirm'),input=ref(''),error=ref('')
  const message=shallowRef<string|VNode>(''),options=shallowRef<Options>({})
  let pending:{resolve:(result:Result)=>void;reject:(reason:string)=>void}|null=null
  function cancel() { const current=pending;pending=null;open.value=false;current?.reject('cancel') }
  function ask(kind:'confirm'|'prompt',content:string|VNode,heading:string,settings:Options={}) {
    if(pending)return Promise.reject('cancel')
    mode.value=kind;message.value=content;title.value=heading;options.value=settings;input.value='';error.value='';open.value=true
    return new Promise<Result>((resolve,reject)=>{pending={resolve,reject}})
  }
  function accept() {
    if(mode.value==='prompt'&&options.value.inputValidator){const result=options.value.inputValidator(input.value);if(result!==true){error.value=typeof result==='string'?result:options.value.inputErrorMessage||'请检查输入';return}}
    const current=pending;pending=null;open.value=false;current?.resolve({value:input.value,action:'confirm'})
  }
  onBeforeUnmount(cancel)
  return {open,title,mode,input,error,message,options,accept,cancel,confirm:(content:string|VNode,heading:string,settings?:Options)=>ask('confirm',content,heading,settings),prompt:(content:string|VNode,heading:string,settings?:Options)=>ask('prompt',content,heading,settings),close:cancel}
}
