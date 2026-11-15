import { useState } from 'react'
import { AlertCircle, ArrowLeft, Camera, Check, CheckCircle2, ChevronRight, Gamepad2, LoaderCircle, LogIn, RefreshCw, Save, ShieldCheck, UserRound } from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

export type ProfileScenario = 'default' | 'guest' | 'loading' | 'error' | 'dirty' | 'saving' | 'save-error' | 'avatar-saving' | 'avatar-error' | 'phone-empty' | 'empty-games' | 'success'
interface ProfilePageProps { scenario: ProfileScenario; onLogin: () => void; onBack: () => void; onHome: () => void; onCompanions: () => void; onOrders: () => void; onSecurity: () => void; onRetry: () => void }
interface GameProfile { id: number; name: string; region: string; nickname: string; uid: string }
const initialGames: GameProfile[] = [{ id: 1, name: '王者荣耀', region: '微信区', nickname: '海浪收藏家', uid: 'SL10241' }, { id: 2, name: '英雄联盟', region: '艾欧尼亚', nickname: '听海', uid: '' }, { id: 3, name: '和平精英', region: '', nickname: '', uid: '' }]
const avatarChoices = [{ initial: '海', label: '海洋蓝', className: 'bg-primary/15 text-primary' }, { initial: '浪', label: '深空蓝', className: 'bg-blue-800 text-blue-50' }, { initial: '声', label: '晴空蓝', className: 'bg-sky-400/15 text-sky-500' }]

export function ProfilePage({ scenario, onLogin, onBack, onHome, onCompanions, onOrders, onSecurity, onRetry }: ProfilePageProps) {
  const [nickname, setNickname] = useState('海浪收藏家')
  const [games, setGames] = useState<GameProfile[]>(scenario === 'empty-games' ? [] : initialGames)
  const [selected, setSelected] = useState(0)
  const [avatar, setAvatar] = useState(0)
  const [avatarOpen, setAvatarOpen] = useState(false)
  const [gameOpen, setGameOpen] = useState(false)
  const [dirty, setDirty] = useState(['dirty', 'save-error'].includes(scenario))
  const [saveMessage, setSaveMessage] = useState(scenario === 'success' ? '资料已保存，下单时自动回填' : '')
  const [saveError, setSaveError] = useState(scenario === 'save-error' ? '保存失败，请检查网络后重试。已填写的内容仍保留在本页。' : '')
  const [avatarMessage, setAvatarMessage] = useState(scenario === 'avatar-error' ? '头像更新失败，请重新选择。其他资料未受影响。' : '')
  const saving = scenario === 'saving'
  const avatarSaving = scenario === 'avatar-saving'
  const busy = saving || avatarSaving
  const game = games[selected]
  const saveState = avatarSaving ? '上传中' : saving ? '保存中' : saveError ? '需重试' : dirty ? '待保存' : '已同步'

  function markDirty() { setDirty(true); setSaveMessage(''); setSaveError('') }
  function updateGame(field: 'region' | 'nickname' | 'uid', value: string) { setGames(items => items.map((item, index) => index === selected ? { ...item, [field]: value } : item)); markDirty() }
  function chooseGame(index: number) { setSelected(index); setGameOpen(false); markDirty() }
  function chooseAvatar(index: number) { setAvatar(index); setAvatarOpen(false); setAvatarMessage('头像已更新（本地演示）') }
  function save() {
    if (busy) return
    const trimmed = nickname.trim()
    if (!trimmed) { setSaveError('请填写昵称后再保存'); setSaveMessage(''); return }
    setNickname(trimmed)
    setGames(items => items.map(item => ({ ...item, region: item.region.trim(), nickname: item.nickname.trim(), uid: item.uid.trim() })))
    setDirty(false); setSaveError(''); setSaveMessage('资料已保存，下单时自动回填')
  }
  function handleDockNavigate(key: string) { if (key === 'home') onHome(); else if (key === 'companions') onCompanions(); else if (key === 'orders') onOrders(); else if (key === 'mine') onBack() }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-32 md:pb-12">
        <div className="flex items-center justify-between px-4 py-2 md:px-6"><Button type="button" variant="ghost" className="h-11 rounded-[8px] px-3 text-sm" onClick={onBack}><ArrowLeft className="size-4" />个人中心</Button><Badge variant="outline" className="rounded-md text-xs">示例资料</Badge></div>
        {scenario === 'guest' ? <GuestState onLogin={onLogin} /> : scenario === 'loading' ? <div className="grid gap-6 px-4 md:grid-cols-[320px_minmax(0,1fr)] md:px-6" aria-busy="true" aria-label="正在加载个人与游戏资料"><Skeleton className="h-96 rounded-[12px]" /><Skeleton className="h-[520px] rounded-[12px]" /></div> : scenario === 'error' ? <ErrorState onRetry={onRetry} /> : (
          <>
            <header className="flex items-start justify-between gap-4 px-4 pb-6 pt-2 md:px-6"><div><h1 className="text-[26px] font-semibold leading-[1.3] tracking-[-0.03em]">个人与游戏资料</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">完善账号与游戏信息，下次下单自动回填。</p></div><Badge variant="outline" className={cn('mt-1 shrink-0 rounded-md text-xs', saveError ? 'border-destructive/25 text-destructive' : dirty ? 'border-warning/25 text-warning' : 'text-primary')}><span className="size-1.5 rounded-full bg-current" />{saveState}</Badge></header>
            <form className="grid items-start gap-6 px-4 pb-6 md:grid-cols-[320px_minmax(0,1fr)] md:px-6 xl:grid-cols-[360px_minmax(0,1fr)]" onSubmit={event => { event.preventDefault(); save() }}>
              <aside className="space-y-6">
                <section className="rounded-[12px] border bg-card p-4 shadow-sm md:p-5" aria-labelledby="profile-account-title">
                  <div><h2 id="profile-account-title" className="text-base font-semibold">账户资料</h2><p className="mt-1 text-xs text-muted-foreground">头像与昵称用于订单沟通</p></div>
                  <button type="button" className="mt-4 flex min-h-[88px] w-full items-center gap-3 rounded-[8px] bg-muted/45 p-3 text-left transition-colors hover:bg-muted/70 disabled:opacity-60" onClick={() => setAvatarOpen(true)} disabled={busy} aria-label="选择微信示例头像，选择后自动更新">
                    <span className="relative shrink-0"><Avatar className="size-14 border"><AvatarFallback className={cn('text-xl font-semibold', avatarChoices[avatar].className)}>{avatarChoices[avatar].initial}</AvatarFallback></Avatar><span className="absolute -bottom-1 -right-1 grid size-6 place-items-center rounded-full border-2 border-card bg-primary text-primary-foreground">{avatarSaving ? <LoaderCircle className="size-3 animate-spin" /> : <Camera className="size-3" />}</span></span>
                    <span className="min-w-0 flex-1"><span className="block text-sm font-medium">选择微信头像</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">{avatarSaving ? '正在上传，请稍候' : '选择后自动保存 · 本地示例'}</span></span><ChevronRight className="size-4 text-muted-foreground" />
                  </button>
                  {avatarMessage && <p role={avatarMessage.includes('失败') ? 'alert' : 'status'} className={cn('mt-3 flex gap-2 rounded-[8px] p-3 text-xs leading-5', avatarMessage.includes('失败') ? 'bg-destructive/10 text-destructive' : 'bg-primary/5 text-primary')}>{avatarMessage.includes('失败') ? <AlertCircle className="mt-0.5 size-4 shrink-0" /> : <CheckCircle2 className="mt-0.5 size-4 shrink-0" />}{avatarMessage}</p>}
                  <div className="mt-4"><div className="mb-2 flex items-center gap-2"><Label htmlFor="profile-nickname" className="text-sm">昵称</Label><span className="text-xs text-destructive">必填</span><span className="ml-auto text-xs text-muted-foreground">{Array.from(nickname).length}/50</span></div><Input id="profile-nickname" value={nickname} maxLength={50} disabled={busy} onChange={event => { setNickname(event.target.value); markDirty() }} placeholder="你的昵称" className="h-12 rounded-[8px] bg-background text-sm" aria-describedby="profile-nickname-note" /><p id="profile-nickname-note" className="mt-2 text-xs text-muted-foreground">50 字以内，将展示给陪玩与客服</p></div>
                  <div className="mt-4 border-t pt-4"><p className="text-sm font-medium">手机号</p><p className="mt-2 text-sm tabular-nums">{scenario === 'phone-empty' ? '未设置' : '138 **** 8000'}</p><p className="mt-1 text-xs text-muted-foreground">需微信授权并通过短信验证</p><Button type="button" variant="outline" className="mt-3 h-11 w-full rounded-[8px]" onClick={onSecurity} disabled={busy}><ShieldCheck className="size-4" />前往账号安全</Button></div>
                </section>
                <p className="px-2 text-center text-xs leading-5 text-muted-foreground">本地示例 · 不读取微信资料，不修改真实账号</p>
              </aside>

              <div className="space-y-6">
                {game ? <section className="rounded-[12px] border bg-card p-4 shadow-sm md:p-5" aria-labelledby="profile-game-title">
                  <div><h2 id="profile-game-title" className="text-base font-semibold">常用游戏</h2><p className="mt-1 text-xs text-muted-foreground">选择默认游戏，并完善接单所需信息</p></div>
                  <Label className="mb-2 mt-4 block text-sm">默认游戏</Label>
                  <button type="button" className="flex min-h-14 w-full items-center gap-3 rounded-[8px] border bg-background px-3 text-left disabled:opacity-60" onClick={() => setGameOpen(true)} disabled={busy} aria-label={`选择默认游戏，当前${game.name}`}><Gamepad2 className="size-5 text-primary" /><span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{game.name}</span><span className="mt-1 block text-xs text-muted-foreground">下单时优先使用</span></span><span className="text-xs font-medium text-primary">切换</span><ChevronRight className="size-4 text-muted-foreground" /></button>
                  <div className="grid gap-x-4 md:grid-cols-2"><GameInput id="profile-region" label="游戏大区" value={game.region} placeholder="例如微信区、QQ区" disabled={busy} onChange={value => updateGame('region', value)} /><GameInput id="profile-game-nickname" label="游戏昵称" value={game.nickname} placeholder="用于联系和识别" disabled={busy} onChange={value => updateGame('nickname', value)} /><GameInput id="profile-uid" label="游戏 UID" value={game.uid} placeholder="填写游戏账号编号" disabled={busy} onChange={value => updateGame('uid', value)} /></div>
                  <p className="mt-4 rounded-[8px] bg-muted/45 p-3 text-xs leading-5 text-muted-foreground">只需填写常玩游戏；保存后，下单时仍可修改。</p>
                </section> : <section className="rounded-[12px] border border-dashed p-8 text-center"><Gamepad2 className="mx-auto size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">暂无可用游戏分类</h2><p className="mt-2 text-sm text-muted-foreground">账户资料仍可正常保存，游戏分类恢复后再补充。</p></section>}

                <section className="rounded-[12px] border bg-card p-4 shadow-sm md:p-5" aria-labelledby="profile-save-title"><div className="flex items-start justify-between gap-4"><div><h2 id="profile-save-title" className="text-base font-semibold">{avatarSaving ? '正在保存头像' : saving ? '正在保存资料' : saveError ? '保存失败' : dirty ? '有未保存的修改' : saveMessage ? '资料已保存' : '资料已同步'}</h2><p className="mt-1 text-xs leading-5 text-muted-foreground">{avatarSaving ? '头像会自动保存，其他修改不受影响' : saving ? '请保持当前页面，完成后会自动同步' : dirty && !saveError ? '保存后将在下次下单时自动回填' : saveMessage || '头像自动保存，其他资料请手动保存'}</p></div><CheckCircle2 className={cn('size-5 shrink-0', saveError ? 'text-destructive' : dirty ? 'text-warning' : 'text-primary')} /></div>{saveError && <p role="alert" className="mt-3 rounded-[8px] bg-destructive/10 p-3 text-xs leading-5 text-destructive">{saveError}</p>}<Button type="submit" className="mt-4 h-12 w-full rounded-[8px]" disabled={busy}>{saving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}{saving ? '保存中' : saveError ? '重试保存' : dirty ? '保存修改' : '保存资料'}</Button></section>
              </div>
            </form>
          </>
        )}
      </main>

      <Dialog open={avatarOpen} onOpenChange={setAvatarOpen}><DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><DialogTitle className="text-left text-xl">选择微信示例头像</DialogTitle><DialogDescription className="text-left text-sm leading-6">正式环境将使用微信头像并自动保存；这里仅更新本地外观，不读取真实微信资料。</DialogDescription></DialogHeader><div className="grid grid-cols-3 gap-3">{avatarChoices.map((choice, index) => <button type="button" key={choice.label} className={cn('flex min-h-[104px] flex-col items-center justify-center gap-2 rounded-[8px] border p-2 text-xs', avatar === index && 'border-primary bg-primary/5')} onClick={() => chooseAvatar(index)} aria-pressed={avatar === index}><Avatar className="size-12"><AvatarFallback className={cn('text-lg font-semibold', choice.className)}>{choice.initial}</AvatarFallback></Avatar><span>{choice.label}</span>{avatar === index && <Check className="size-4 text-primary" />}</button>)}</div><DialogFooter><DialogClose asChild><Button type="button" variant="outline" className="h-11 w-full rounded-[8px]">取消</Button></DialogClose></DialogFooter></DialogContent></Dialog>
      <Dialog open={gameOpen} onOpenChange={setGameOpen}><DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><DialogTitle className="text-left text-xl">选择默认游戏</DialogTitle><DialogDescription className="text-left text-sm leading-6">切换后可分别编辑大区、游戏昵称和 UID，保存时同步默认游戏。</DialogDescription></DialogHeader><div className="space-y-2">{games.map((item, index) => <button type="button" key={item.id} className={cn('flex min-h-14 w-full items-center gap-3 rounded-[8px] border px-4 text-left', selected === index && 'border-primary bg-primary/5')} onClick={() => chooseGame(index)} aria-pressed={selected === index}><Gamepad2 className="size-4 text-primary" /><span className="flex-1 text-sm font-medium">{item.name}</span>{selected === index ? <Check className="size-4 text-primary" /> : <ChevronRight className="size-4 text-muted-foreground" />}</button>)}</div><DialogFooter><DialogClose asChild><Button type="button" variant="outline" className="h-11 w-full rounded-[8px]">取消</Button></DialogClose></DialogFooter></DialogContent></Dialog>
    </PhoneFrame>
  )
}

function GuestState({ onLogin }: { onLogin: () => void }) {
  return <section className="px-4 pt-4 md:px-6"><div className="mx-auto max-w-xl rounded-[12px] border bg-card p-6 text-center shadow-sm md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><UserRound className="size-6" /></div><h1 className="mt-4 text-xl font-semibold">登录后编辑资料</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">完善账号与常用游戏信息，下次下单自动回填。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}><LogIn className="size-4" />微信快捷登录</Button></div></section>
}
function ErrorState({ onRetry }: { onRetry: () => void }) {
  return <section className="px-4 pt-4 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h1 className="mt-4 text-xl font-semibold">个人资料暂时没有加载成功</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试。加载失败不会覆盖已保存的账号与游戏资料。</p><Button type="button" className="mt-6 h-11 rounded-[8px]" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div></section>
}
function GameInput({ id, label, value, placeholder, disabled, onChange }: { id: string; label: string; value: string; placeholder: string; disabled: boolean; onChange: (value: string) => void }) {
  return <div className="mt-4"><div className="mb-2 flex items-center gap-2"><Label htmlFor={id} className="text-sm">{label}</Label><span className="text-xs text-muted-foreground">选填</span></div><Input id={id} value={value} maxLength={50} disabled={disabled} onChange={event => onChange(event.target.value)} placeholder={placeholder} className="h-12 rounded-[8px] bg-background text-sm" /></div>
}
