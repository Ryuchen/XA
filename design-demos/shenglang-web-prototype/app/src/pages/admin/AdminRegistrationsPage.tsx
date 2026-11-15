import { useMemo, useState, type FormEvent } from 'react'
import {
  Banknote,
  ChevronRight,
  CircleAlert,
  Clock3,
  FileCheck2,
  RotateCcw,
  Search,
  ShieldCheck,
  UserCheck,
  UsersRound,
} from 'lucide-react'
import type { AdminRegistrationDetailScenario, AdminRegistrationsScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  adminDepositModeLabel,
  adminRegistrations,
  adminRegistrationStatusLabel,
  type AdminRegistration,
  type AdminRegistrationStatus,
} from '@/pages/admin/admin-registration-data'
import { cn } from '@/lib/utils'

interface AdminRegistrationsPageProps {
  scenario: AdminRegistrationsScenario
  registrations?: AdminRegistration[]
  onScenarioChange: (scenario: AdminRegistrationsScenario) => void
  onOpen: (registration: AdminRegistration, scenario: AdminRegistrationDetailScenario) => void
}

function detailScenario(registration: AdminRegistration): AdminRegistrationDetailScenario {
  if (registration.status === 'APPROVED') return 'approved'
  if (registration.status === 'REJECTED') return 'rejected'
  return registration.depositMode === 'EARNINGS' ? 'earnings' : 'default'
}

export function AdminRegistrationsPage({ scenario, registrations = adminRegistrations, onScenarioChange, onOpen }: AdminRegistrationsPageProps) {
  const initialStatus = scenario === 'approved' ? 'APPROVED' : scenario === 'rejected' ? 'REJECTED' : scenario === 'all' ? 'ALL' : 'PENDING'
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<'ALL' | AdminRegistrationStatus>(initialStatus)

  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return registrations.filter(item => {
      if (status !== 'ALL' && item.status !== status) return false
      if (keyword && !`${item.username} ${item.displayName} ${item.realName} ${item.phone} ${item.id}`.toLowerCase().includes(keyword.toLowerCase())) return false
      return true
    })
  }, [keyword, registrations, scenario, status])

  function search(event: FormEvent) {
    event.preventDefault()
    setKeyword(keywordInput.trim())
  }

  function changeStatus(value: string) {
    const next = value as 'ALL' | AdminRegistrationStatus
    setStatus(next)
    if (scenario !== 'default' && scenario !== 'refresh-error' && scenario !== 'empty') onScenarioChange('default')
  }

  function clearFilters() {
    setKeywordInput('')
    setKeyword('')
    setStatus('PENDING')
    onScenarioChange('default')
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="注册申请加载中"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">注册申请加载失败</CardTitle><CardDescription className="mt-2">请检查网络或审核权限后重试，当前没有展示推测申请。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  const pendingCount = registrations.filter(item => item.status === 'PENDING').length
  const cashPending = registrations.filter(item => item.status === 'PENDING' && item.depositMode === 'CASH').length

  return <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div>
          <div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">注册审核</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">示例快照</Badge></div>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">核对入驻资料与押金方式，通过申请后开通陪玩账号。</p>
        </div>
        <dl className="grid grid-cols-3 gap-5 md:min-w-[360px]">
          {[[pendingCount, '等待审核'], [cashPending, '现金待核验'], [registrations.filter(item => item.status === 'REJECTED').length, '待补充资料']].map(([value, label]) => <div key={label} className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{value}</dd><dt className="mt-1 text-[11px] text-blue-100/65">{label}</dt></div>)}
        </dl>
      </div>
    </section>

    <Alert><ShieldCheck className="size-4" /><AlertTitle>私密材料仅限授权审核</AlertTitle><AlertDescription>请核对证件正反面、姓名与号码；现金缴纳还须核实实际到账，不能仅凭凭证图片通过。</AlertDescription></Alert>
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>申请更新失败，当前显示上次成功读取的列表</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">现有筛选已保留，进入详情前请重新核对申请状态。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}

    <Card><CardContent className="py-4">
      <form data-admin-filter onSubmit={search} className="grid gap-3 md:grid-cols-[minmax(260px,1fr)_180px_auto]">
        <div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="搜索注册申请" value={keywordInput} onChange={event => { setKeywordInput(event.target.value); if (!event.target.value) setKeyword('') }} placeholder="账号 / 手机号 / 姓名 / 昵称" className="h-11 pl-9" /></div>
        <Select value={status} onValueChange={changeStatus}><SelectTrigger className="h-11 w-full" aria-label="审核状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="PENDING">待审核</SelectItem><SelectItem value="APPROVED">已通过</SelectItem><SelectItem value="REJECTED">需补充资料</SelectItem><SelectItem value="ALL">全部状态</SelectItem></SelectContent></Select>
        <div className="flex items-center gap-2">
          <Button type="submit" className="h-11 min-w-24 whitespace-nowrap">查询申请</Button>
          <Button type="button" variant="outline" className="h-11 min-w-20 whitespace-nowrap" onClick={clearFilters}>重置</Button>
        </div>
      </form>
      {keywordInput.trim() !== keyword && <p role="status" className="mt-2 text-xs text-warning-foreground">搜索内容已更改，列表尚未更新；请点击查询申请。</p>}
    </CardContent></Card>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="text-base">陪玩开户申请</CardTitle><CardDescription className="mt-1">本页 {rows.length} 笔 · 证件与身份资料均为明确示例</CardDescription></div><div className="flex items-center gap-2 text-xs text-muted-foreground"><FileCheck2 className="size-4" />按提交时间倒序</div></CardHeader>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1080px]">
        <TableHeader><TableRow><TableHead>申请 / 账号</TableHead><TableHead>申报身份</TableHead><TableHead>押金方式</TableHead><TableHead>审核材料</TableHead><TableHead>状态 / 提交时间</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
        <TableBody>{rows.map(item => {
          const materialCount = [item.idFrontAvailable, item.idBackAvailable, item.depositMode === 'CASH' && item.depositProofAvailable].filter(Boolean).length
          const requiredCount = item.depositMode === 'CASH' ? 3 : 2
          return <TableRow key={item.id}>
            <TableCell><div className="flex items-center gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-sm font-semibold text-primary">{item.displayName.slice(0, 1)}</span><div><button type="button" onClick={() => onOpen(item, detailScenario(item))} className="text-sm font-medium text-primary hover:underline">{item.displayName}</button><p className="mt-1 font-mono text-[11px] text-muted-foreground">#{item.id} · {item.username}</p></div></div></TableCell>
            <TableCell><p className="text-sm">{item.realName}</p><p className="mt-1 text-xs text-muted-foreground">{item.phone}</p><p className="mt-1 font-mono text-[11px] text-muted-foreground">{item.idNumberMasked}</p></TableCell>
            <TableCell><div className="flex items-center gap-2"><Banknote className="size-4 text-primary" /><span className="text-xs font-medium">{adminDepositModeLabel[item.depositMode]}</span></div><p className="mt-1.5 text-xs text-muted-foreground">应缴 {item.depositRequired} 元</p>{item.status === 'APPROVED' && <p className="mt-1 text-[11px] text-muted-foreground">{item.depositMode === 'CASH' ? `登记 ${item.depositPaid} 元` : '不登记现金押金'}</p>}</TableCell>
            <TableCell><div className="flex items-center gap-2"><span className={cn('grid size-7 place-items-center rounded-lg', materialCount === requiredCount ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning-foreground')}>{materialCount === requiredCount ? <UserCheck className="size-4" /> : <CircleAlert className="size-4" />}</span><div><p className="text-xs font-medium">{materialCount} / {requiredCount} 份可读取</p><p className="mt-1 text-[11px] text-muted-foreground">{materialCount === requiredCount ? '进入详情人工核对' : '存在材料读取异常'}</p></div></div></TableCell>
            <TableCell><Badge variant="outline" className={cn('whitespace-nowrap', item.status === 'PENDING' && 'border-warning/30 bg-warning/10 text-warning-foreground', item.status === 'APPROVED' && 'border-success/30 bg-success/10 text-success')}>{adminRegistrationStatusLabel[item.status]}</Badge><p className="mt-2 flex items-center gap-1 whitespace-nowrap text-[11px] text-muted-foreground"><Clock3 className="size-3" />{item.submittedAt}</p></TableCell>
            <TableCell className="text-right"><Button variant={item.status === 'PENDING' ? 'default' : 'ghost'} size="sm" onClick={() => onOpen(item, detailScenario(item))}>{item.status === 'PENDING' ? '查看并审核' : '查看详情'}<ChevronRight className="size-3.5" /></Button></TableCell>
          </TableRow>
        })}</TableBody>
      </Table>{rows.length === 0 && <div className="flex min-h-72 flex-col items-center justify-center p-8 text-center"><span className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground"><UsersRound className="size-5" /></span><h2 className="mt-4 text-base font-semibold">没有匹配的申请</h2><p className="mt-2 text-sm text-muted-foreground">调整关键词或切换审核状态后再查看。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>返回待审核</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>不连接真实申请、身份信息或押金记录。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>
  </div>
}
