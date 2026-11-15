import { ClipboardList, Headphones, WalletCards } from 'lucide-react'
import { WeChatLoginCard } from '@/components/auth/WeChatLoginCard'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import type { LoginScenario } from '@/components/prototype/prototype-config'

export function LoginPage({ scenario }: { scenario: LoginScenario }) {
  return <PhoneFrame>
    <main className="min-h-[640px] pb-8 md:grid md:min-h-[calc(100dvh-64px)] md:grid-cols-[minmax(0,480px)_420px] md:grid-rows-[1fr_auto] md:items-center md:justify-center md:gap-x-8 md:gap-y-0 md:px-8 md:py-8 xl:grid-cols-[480px_460px]">
      <section className="px-5 pb-6 pt-4 md:px-0 md:pb-0 md:pt-6" aria-labelledby="login-welcome">
        <Badge variant="outline" className="mt-0 rounded-md border-primary/20 bg-primary/5 text-xs text-primary md:mt-8">微信安全登录</Badge>
        <h1 id="login-welcome" className="mt-4 max-w-lg text-[26px] font-semibold leading-[1.3] tracking-[-0.03em] md:text-[32px]">每一次组队，<br className="hidden md:block" />都从安心登录开始。</h1>
        <p className="mt-3 max-w-md text-sm leading-6 text-muted-foreground">使用微信快捷登录，同步头像与昵称，验证手机号后即可管理订单、浪花与专属服务，也可按需开启二次验证。</p>
        <div className="mt-6 hidden max-w-md grid-cols-3 border-y py-4 md:grid"><LoginBenefit icon={ClipboardList} label="订单进度" /><LoginBenefit icon={WalletCards} label="钱包资产" /><LoginBenefit icon={Headphones} label="专属服务" /></div>
      </section>

      <WeChatLoginCard audience="boss" scenario={scenario} storeName="声浪电竞" profileName="海浪收藏家" />
      <footer className="px-5 pb-2 pt-6 text-center text-xs text-muted-foreground md:col-span-2 md:pb-0 md:pt-4">声浪电竞 · 让每一次组队更安心</footer>
    </main>
  </PhoneFrame>
}

function LoginBenefit({ icon: Icon, label }: { icon: typeof ClipboardList; label: string }) {
  return <div className="flex items-center gap-2 text-sm"><Icon className="size-4 text-primary" /><span>{label}</span></div>
}
