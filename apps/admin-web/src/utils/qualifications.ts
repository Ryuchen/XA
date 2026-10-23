import { qualificationOptionsApi } from '@/api/modules'
let pending: Promise<any> | null = null
export const qualificationOptions = () => pending || (pending = qualificationOptionsApi.get().then((r: any) => r.data).catch(e => { pending = null; throw e }))
export const scopeGrades = (options: any, row: any) => options?.scopes?.find((s: any) => s.provider_kind === row.provider_kind && s.platform === row.platform && s.security_level === row.security_level)?.grades || []
