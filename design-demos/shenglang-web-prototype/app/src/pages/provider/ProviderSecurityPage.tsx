import { useState } from 'react'
import { ArrowLeft, CheckCircle2, KeyRound, MessageCircle, ShieldCheck, Smartphone } from 'lucide-react'
import { useNavigate } from 'react-router'
import { OtpBinding } from '@/components/auth/OtpBinding'
import { ProviderFrame } from '@/components/provider/ProviderFrame'
import { useStore } from '@/components/store/store-context'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose } from '@/components/ui/dialog'
import { ProviderDialogContent as DialogContent, ProviderDialogFooter as DialogFooter, ProviderDialogHeader } from '@/components/provider/ProviderDialog'
import { cn } from '@/lib/utils'
import { ProviderHeader, ProviderNotice, ProviderPage, ProviderSection, ProviderState } from '@/pages/provider/ProviderShared'
import type { ProviderScenario } from '@/pages/provider/provider-data'

export function ProviderSecurityPage({ scenario, onRetry }: { scenario: ProviderScenario; onRetry: () => void }) {
  const navigate = useNavigate()
  const { store } = useStore()
  const [otpBound, setOtpBound] = useState(false)
  const [otpOpen, setOtpOpen] = useState(false)
  const [notice, setNotice] = useState('')

  return <ProviderFrame><ProviderPage>
    <ProviderHeader title="账号安全" description={`管理${store.shortName}账号的登录身份、手机号与二次验证。`} action={<Button type="button" variant="ghost" className="h-11" onClick={() => navigate('/provider/mine')}><ArrowLeft className="size-4" />个人中心</Button>} />
    {scenario !== 'default' ? <ProviderState scenario={scenario} onRetry={onRetry} emptyTitle="账号安全信息暂不可用" emptyDescription="请稍后重新加载，现有绑定信息不会改变。" /> : <ProviderSection>
      {notice && <ProviderNotice>{notice}</ProviderNotice>}
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <section className="overflow-hidden rounded-[12px] border bg-card" aria-labelledby="provider-identity-title">
          <div className="flex items-center gap-3 border-b p-4 md:p-5"><ShieldCheck className="size-5 text-primary" /><div className="min-w-0 flex-1"><p className="text-xs text-muted-foreground">本店账号</p><h2 id="provider-identity-title" className="mt-1 font-semibold">登录身份与联系方式</h2></div><Badge variant="outline" className="border-success/25 bg-success/10 text-success">已验证</Badge></div>
          <div className="divide-y px-4 md:px-5">
            <div className="flex min-h-20 items-center gap-3"><MessageCircle className="size-5 shrink-0 text-primary" /><div className="min-w-0 flex-1"><p className="text-sm font-medium">当前微信已绑定</p><p className="mt-1 text-xs text-muted-foreground">仅用于{store.shortName}陪玩账号登录</p></div><CheckCircle2 className="size-4 text-success" /></div>
            <div className="flex min-h-20 items-center gap-3"><Smartphone className="size-5 shrink-0 text-primary" /><div className="min-w-0 flex-1"><p className="text-sm font-medium tabular-nums">138 **** 8000</p><p className="mt-1 text-xs text-muted-foreground">微信授权手机号 · 已完成短信验证</p></div><CheckCircle2 className="size-4 text-success" /></div>
          </div>
        </section>

        <section className="overflow-hidden rounded-[12px] border bg-card" aria-labelledby="provider-otp-title">
          <div className="flex items-center gap-3 border-b p-4 md:p-5"><KeyRound className="size-5 text-primary" /><div className="min-w-0 flex-1"><p className="text-xs text-muted-foreground">登录保护</p><h2 id="provider-otp-title" className="mt-1 font-semibold">二次验证</h2></div><Badge variant="outline" className={cn(otpBound ? 'border-success/25 bg-success/10 text-success' : 'border-warning/25 bg-warning/10 text-warning')}>{otpBound ? '已开启' : '未开启'}</Badge></div>
          <div className="p-4 md:p-5">
            <div className="rounded-[8px] bg-muted/45 p-4"><p className="text-sm font-medium">{otpBound ? 'OTP 动态验证已开启' : '为此账号增加一层登录保护'}</p><p className="mt-2 text-sm leading-6 text-muted-foreground">{otpBound ? '后续登录时需输入验证器生成的 6 位动态验证码。' : '跳过登录时的 OTP 设置不会影响接单；可在这里随时继续绑定。'}</p></div>
            <Button type="button" variant={otpBound ? 'outline' : 'default'} className="mt-4 h-11 w-full" onClick={() => { setNotice(''); setOtpOpen(true) }}><KeyRound className="size-4" />{otpBound ? '重新绑定验证器' : '绑定 OTP 并开启'}</Button>
            <p className="mt-3 text-xs leading-5 text-muted-foreground">验证器绑定仅属于当前门店账号，不与其他门店共享。</p>
          </div>
        </section>
      </div>
      <p className="mt-5 text-center text-xs leading-5 text-muted-foreground">原型仅更新示例状态，不连接真实验证器。</p>
    </ProviderSection>}

    <Dialog open={otpOpen} onOpenChange={setOtpOpen}><DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><ProviderDialogHeader icon={KeyRound} title="开启二次验证" description={`绑定验证器后，登录${store.shortName}陪玩工作台时需额外输入 OTP 动态验证码。`} /><OtpBinding onComplete={() => { setOtpBound(true); setOtpOpen(false); setNotice('二次验证已开启，后续登录将需要输入 OTP 动态验证码。') }} /><DialogFooter><DialogClose asChild><Button type="button" variant="ghost" className="h-11 w-full">取消，保留当前状态</Button></DialogClose></DialogFooter></DialogContent></Dialog>
  </ProviderPage></ProviderFrame>
}
