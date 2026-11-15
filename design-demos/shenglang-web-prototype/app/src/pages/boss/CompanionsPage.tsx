import { useMemo, useState } from 'react'
import {
  AlertCircle,
  AudioLines,
  Check,
  CircleOff,
  Gamepad2,
  MapPin,
  Play,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  UsersRound,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { HeaderSupportButton } from '@/components/prototype/HeaderSupportButton'
import { PageHeader } from '@/components/prototype/PageHeader'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { SearchField } from '@/components/prototype/SearchField'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'

export type CompanionScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'service'

type Companion = {
  id: number
  nickname: string
  initial: string
  gender: 'MALE' | 'FEMALE'
  level: string
  game: string
  rank: string
  city: string
  bio: string
  canOrder: boolean
  voice: boolean
  monthly: number
  price: number
  tags: string[]
  kind: string
  platform: string
  security: string
  grade: string
  tone: string
}

const companions: Companion[] = [
  { id: 1, nickname: '小澈', initial: '澈', gender: 'FEMALE', level: '声浪王牌', game: '王者荣耀', rank: '荣耀王者', city: '杭州', bio: '温柔指挥，擅长射手与辅助，娱乐上分都可以。', canOrder: true, voice: true, monthly: 3280, price: 48, tags: ['声音清甜', '荣耀王者', '擅长辅助'], kind: '娱乐陪玩', platform: '手游', security: 'ANY', grade: '王者', tone: 'from-sky-400/30 to-blue-600/20 text-sky-100' },
  { id: 2, nickname: '阿屿', initial: '屿', gender: 'MALE', level: '资深陪玩', game: '王者荣耀', rank: '最强王者', city: '上海', bio: '节奏稳定，打野位，认真沟通不压力。', canOrder: true, voice: false, monthly: 2160, price: 42, tags: ['打野', '稳定上分', '耐心沟通'], kind: '技术陪玩', platform: '手游', security: 'ANY', grade: '王者', tone: 'from-indigo-400/25 to-slate-600/25 text-indigo-100' },
  { id: 3, nickname: '桃乐丝', initial: '桃', gender: 'FEMALE', level: '人气陪玩', game: '和平精英', rank: '超级王牌', city: '成都', bio: '轻松欢乐，战术配合，喜欢慢慢打也可以。', canOrder: true, voice: true, monthly: 1890, price: 38, tags: ['欢乐开黑', '超级王牌', '会照顾新人'], kind: '娱乐陪玩', platform: '手游', security: 'ANY', grade: '钻石', tone: 'from-cyan-400/25 to-teal-600/20 text-cyan-100' },
  { id: 4, nickname: '北夜', initial: '北', gender: 'MALE', level: '声浪王牌', game: '英雄联盟', rank: '超凡大师', city: '南京', bio: '峡谷中单，复盘清晰，双排与技术提升。', canOrder: false, voice: true, monthly: 3560, price: 56, tags: ['超凡大师', '中单', '复盘指导'], kind: '技术陪玩', platform: '端游', security: '机密', grade: '星耀', tone: 'from-blue-300/25 to-violet-600/20 text-blue-100' },
  { id: 5, nickname: '森森', initial: '森', gender: 'FEMALE', level: '新锐陪玩', game: '金铲铲之战', rank: '璀璨钻石', city: '广州', bio: '阵容交流，轻松聊天，陪你一起研究新版本。', canOrder: true, voice: true, monthly: 960, price: 32, tags: ['阵容交流', '新版本', '轻松陪伴'], kind: '娱乐陪玩', platform: '手游', security: 'ANY', grade: '钻石', tone: 'from-emerald-300/25 to-sky-600/20 text-emerald-100' },
  { id: 6, nickname: '凌云', initial: '凌', gender: 'MALE', level: '资深陪玩', game: '英雄联盟', rank: '傲世宗师', city: '武汉', bio: '上单位，认真严谨，可按需提供对局复盘。', canOrder: true, voice: false, monthly: 1420, price: 0, tags: ['傲世宗师', '上单', '技术提升'], kind: '技术陪玩', platform: '端游', security: '绝密', grade: '王者', tone: 'from-slate-300/20 to-blue-700/25 text-slate-100' },
]

const categories = ['全部', '王者荣耀', '英雄联盟', '和平精英', '金铲铲之战']

interface FilterSelectProps {
  label: string
  value: string
  onChange: (value: string) => void
  options: string[]
  disabled?: boolean
}

function FilterSelect({ label, value, onChange, options, disabled }: FilterSelectProps) {
  return (
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger className="h-12 min-h-11 w-full rounded-[8px] bg-muted/45 px-3 text-left shadow-none" aria-label={label}>
        <span className="flex min-w-0 flex-1 flex-col items-start leading-tight">
          <span className="text-[10px] font-normal text-muted-foreground">{label}</span>
          <SelectValue />
        </span>
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => <SelectItem key={option} value={option} className="min-h-11">{option}</SelectItem>)}
      </SelectContent>
    </Select>
  )
}

interface CompanionsPageProps {
  scenario: CompanionScenario
  onLogin: () => void
  onHome: () => void
  onDetail: () => void
  onPlatform: () => void
  onRetry: () => void
}

export function CompanionsPage({ scenario, onLogin, onHome, onDetail, onPlatform, onRetry }: CompanionsPageProps) {
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState('全部')
  const [gender, setGender] = useState('不限')
  const [sort, setSort] = useState('默认排序')
  const [voiceOnly, setVoiceOnly] = useState(false)
  const [kind, setKind] = useState('全部方向')
  const [platform, setPlatform] = useState('端游 / 手游')
  const [security, setSecurity] = useState('不限难度')
  const [grade, setGrade] = useState('不限档位')
  const [voiceDemo, setVoiceDemo] = useState<number | null>(null)

  const serviceMode = scenario === 'service'
  const visible = useMemo(() => {
    if (scenario === 'empty' || scenario === 'error' || scenario === 'loading') return []
    const query = keyword.trim().toLowerCase()
    const filtered = companions.filter((item) => {
      if (query && ![item.nickname, item.rank, item.bio, item.city].join(' ').toLowerCase().includes(query)) return false
      if (!serviceMode && category !== '全部' && item.game !== category) return false
      if (gender !== '不限' && item.gender !== (gender === '女陪' ? 'FEMALE' : 'MALE')) return false
      if (voiceOnly && !item.voice) return false
      if (!serviceMode && kind !== '全部方向' && item.kind !== kind) return false
      if (!serviceMode && platform !== '端游 / 手游' && item.platform !== platform) return false
      if (!serviceMode && security !== '不限难度' && item.security !== security) return false
      if (!serviceMode && grade !== '不限档位' && item.grade !== grade) return false
      return true
    })
    if (sort === '默认排序') return filtered
    return [...filtered].sort((a, b) => {
      if ((a.price > 0) !== (b.price > 0)) return a.price > 0 ? -1 : 1
      return sort === '时价低到高' ? a.price - b.price : b.price - a.price
    })
  }, [category, gender, grade, keyword, kind, platform, scenario, security, serviceMode, sort, voiceOnly])

  const hasFilters = Boolean(keyword || voiceOnly || sort !== '默认排序' || gender !== '不限' || (!serviceMode && (category !== '全部' || kind !== '全部方向' || platform !== '端游 / 手游' || security !== '不限难度' || grade !== '不限档位')))

  function resetFilters() {
    setKeyword('')
    setCategory('全部')
    setGender('不限')
    setSort('默认排序')
    setVoiceOnly(false)
    setKind('全部方向')
    setPlatform('端游 / 手游')
    setSecurity('不限难度')
    setGrade('不限档位')
  }

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') {
      document.querySelector('[data-testid="phone-scroll"]')?.scrollTo({ top: 0, behavior: 'smooth' })
    }
  }

  const loading = scenario === 'loading'

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="companions" onNavigate={handleDockNavigate} />}>
      <main className="pb-28 md:pb-8">
        <PageHeader
          title="现在，谁有空？"
          description="挑选游戏、听听声音，再约下一局。"
          action={<HeaderSupportButton />}
        >
          <SearchField className="mt-5" value={keyword} onValueChange={setKeyword} placeholder="搜索昵称、段位或城市" ariaLabel="搜索陪玩" />
        </PageHeader>

        <div className="space-y-5 px-4 md:px-6">
          {scenario === 'guest' ? (
            <Card className="rounded-3xl py-0 text-center">
              <CardContent className="p-6">
                <div className="mx-auto grid size-14 place-items-center rounded-[8px] bg-primary/10 text-primary"><UsersRound className="size-7" /></div>
                <h2 className="mt-5 text-xl font-semibold">找到合拍的陪玩</h2>
                <p className="mx-auto mt-2 max-w-[260px] text-sm leading-6 text-muted-foreground">登录后按游戏挑选，试听声音，查看服务记录。</p>
                <Button type="button" className="mt-6 h-12 w-full rounded-xl" onClick={onLogin}>微信快捷登录</Button>
              </CardContent>
            </Card>
          ) : (
            <>
              {serviceMode && (
                <section className="rounded-[8px] border border-primary/20 bg-primary/5 p-4">
                  <p className="text-xs text-muted-foreground">为这项服务挑选陪玩</p>
                  <h2 className="mt-1 text-base font-semibold">王者荣耀 · 娱乐双排</h2>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">不限档位 · 同范围高档可接低档</p>
                  <Button type="button" variant="outline" className="mt-3 h-11 w-full rounded-xl text-xs" onClick={onPlatform}>交给平台派单</Button>
                </section>
              )}

              {!serviceMode && (
                <section aria-label="游戏筛选">
                  <div className="phone-scrollbar -mx-4 overflow-x-auto px-4">
                    <div className="flex w-max gap-2 pb-1">
                      {categories.map((item) => (
                        <button key={item} type="button" aria-pressed={category === item} className={cn('flex h-11 min-w-11 items-center gap-2 rounded-[8px] border bg-card px-4 text-xs font-medium transition-colors hover:bg-accent', category === item && 'border-primary bg-primary text-primary-foreground hover:bg-primary/90')} onClick={() => setCategory(item)}>
                          {item === '全部' ? <Sparkles className="size-4" /> : <Gamepad2 className="size-4" />}
                          {item}
                        </button>
                      ))}
                    </div>
                  </div>
                </section>
              )}

              <section className="grid grid-cols-2 gap-2 md:grid-cols-3" aria-label="陪玩条件筛选">
                {!serviceMode && (
                  <>
                    <FilterSelect label="服务方向" value={kind} onChange={setKind} options={['全部方向', '娱乐陪玩', '技术陪玩']} />
                    <FilterSelect label="平台" value={platform} onChange={setPlatform} options={['端游 / 手游', '手游', '端游']} />
                    <FilterSelect label="难度" value={security} onChange={setSecurity} options={['不限难度', '机密', '绝密']} />
                    <FilterSelect label="最低档位" value={grade} onChange={setGrade} options={['不限档位', '钻石', '星耀', '王者']} />
                  </>
                )}
                <FilterSelect label="性别" value={gender} onChange={setGender} options={['不限', '男陪', '女陪']} />
                <FilterSelect label="排序" value={sort} onChange={setSort} options={['默认排序', '时价低到高', '时价高到低']} />
              </section>

              <section className="grid grid-cols-2 gap-2 md:max-w-md">
                <Button type="button" variant="outline" aria-pressed={voiceOnly} className={cn('h-12 rounded-[8px] px-3 text-xs', voiceOnly && 'border-primary bg-primary/5 text-primary')} onClick={() => setVoiceOnly(!voiceOnly)}>
                  <AudioLines className="size-4" />
                  只看有语音
                  <span className={cn('ml-auto grid size-4 place-items-center rounded-full border', voiceOnly && 'border-primary bg-primary text-primary-foreground')}>{voiceOnly && <Check className="size-3" />}</span>
                </Button>
                <Button type="button" variant="outline" className="h-12 rounded-[8px] text-xs" disabled={!hasFilters} onClick={resetFilters}><RotateCcw className="size-4" />重置筛选</Button>
              </section>

              <div className="flex items-end justify-between gap-3 border-t pt-4">
                <div>
                  <h2 className="text-base font-semibold">陪玩列表</h2>
                  <p className="mt-1 text-xs text-muted-foreground">先了解，再决定和谁组队</p>
                </div>
                <Badge variant="secondary" className="shrink-0 text-[11px]">{visible.length} 位符合条件</Badge>
              </div>

              {loading ? (
                <div className="space-y-3 md:grid md:grid-cols-2 md:gap-4 md:space-y-0 xl:grid-cols-3" aria-label="正在加载陪玩列表" aria-busy="true">
                  {[1, 2, 3].map((item) => <Card key={item} className="rounded-[8px] py-0"><CardContent className="p-4"><div className="flex gap-3"><Skeleton className="size-14 rounded-full" /><div className="flex-1 space-y-2"><Skeleton className="h-5 w-28" /><Skeleton className="h-4 w-40" /></div></div><Skeleton className="mt-4 h-8 w-full" /><Skeleton className="mt-3 h-5 w-32" /></CardContent></Card>)}
                </div>
              ) : scenario === 'error' ? (
                <div role="alert" className="rounded-[8px] border border-destructive/25 bg-destructive/5 p-6 text-center">
                  <AlertCircle className="mx-auto size-7 text-destructive" />
                  <h3 className="mt-3 text-base font-semibold">陪玩列表暂时没有加载成功</h3>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">请检查网络后重试，已选择的筛选条件会保留。</p>
                  <Button type="button" variant="outline" className="mt-5 h-11 w-full" onClick={onRetry}>重新加载</Button>
                </div>
              ) : visible.length === 0 ? (
                <div className="rounded-[8px] border border-dashed p-6 text-center">
                  <CircleOff className="mx-auto size-7 text-muted-foreground" />
                  <h3 className="mt-3 text-base font-semibold">暂时没有符合条件的陪玩</h3>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">试试放宽游戏、语音或资格筛选。</p>
                  <Button type="button" variant="outline" className="mt-5 h-11 w-full" onClick={scenario === 'empty' ? onRetry : resetFilters}>重置筛选</Button>
                </div>
              ) : (
                <div className="space-y-3 md:grid md:grid-cols-2 md:gap-4 md:space-y-0 xl:grid-cols-3">
                  {visible.map((item) => (
                    <Card key={item.id} className="gap-0 overflow-hidden rounded-[8px] py-0 shadow-sm">
                      <CardContent className="p-4">
                        <div className="flex items-center gap-3">
                          <button type="button" className={cn('grid size-14 shrink-0 place-items-center rounded-full bg-gradient-to-br text-xl font-semibold ring-1 ring-inset ring-white/10', item.tone)} onClick={onDetail} aria-label={`查看陪玩 ${item.nickname} 的详情`}>{item.initial}</button>
                          <button type="button" className="min-h-14 min-w-0 flex-1 text-left" onClick={onDetail}>
                            <span className="flex items-center gap-2">
                              <span className="truncate text-base font-semibold">{item.nickname}</span>
                              <span className={cn('shrink-0 rounded-md border px-1.5 py-0.5 text-[10px]', item.canOrder ? 'border-success/25 bg-success/10 text-success' : 'bg-muted text-muted-foreground')}>{item.canOrder ? '可约' : '忙碌'}</span>
                            </span>
                            <span className="mt-1 block truncate text-xs text-muted-foreground">{item.level} · {item.gender === 'FEMALE' ? '女陪' : '男陪'}</span>
                          </button>
                          {item.voice ? (
                            <Button
                              type="button"
                              variant={voiceDemo === item.id ? 'default' : 'outline'}
                              size="icon"
                              className="size-11 shrink-0 rounded-full!"
                              aria-label={voiceDemo === item.id ? `停止${item.nickname}的语音示例` : `播放${item.nickname}的语音示例`}
                              aria-pressed={voiceDemo === item.id}
                              onClick={() => setVoiceDemo(voiceDemo === item.id ? null : item.id)}
                            >
                              {voiceDemo === item.id ? (
                                <span className="flex h-5 items-center gap-[2px]" aria-hidden="true">
                                  {[6, 12, 20, 9, 15, 6].map((height, index) => (
                                    <span key={index} className="voice-wave-bar w-[2px] rounded-full bg-current" style={{ height, animationDelay: `${index * 80}ms` }} />
                                  ))}
                                </span>
                              ) : <Play className="size-4" />}
                            </Button>
                          ) : <span className="grid size-11 shrink-0 place-items-center rounded-full text-muted-foreground"><CircleOff className="size-4" /></span>}
                        </div>

                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {item.tags.slice(0, 3).map((tag, index) => <Badge key={tag} variant={index === 0 ? 'default' : 'secondary'} className={cn('rounded-md px-2 text-[10px] font-normal', index === 0 && 'bg-primary/10 text-primary hover:bg-primary/10')}>{tag}</Badge>)}
                        </div>
                        <div className="mt-3 flex items-center gap-3 text-[11px] text-muted-foreground">
                          <span className="flex items-center gap-1"><ShieldCheck className="size-3.5" />{item.rank}</span>
                          <span className="flex items-center gap-1"><MapPin className="size-3.5" />{item.city}</span>
                          <span className="truncate">{item.game}</span>
                        </div>
                        <Separator className="my-3" />
                        <div className="flex items-end justify-between gap-3">
                          <div>
                            <p className="text-[10px] text-muted-foreground">本月接单额</p>
                            <p className="mt-0.5 text-sm font-semibold tabular-nums">{formatWaveAmount(item.monthly)} <span className="text-[10px] font-normal text-muted-foreground">浪花</span></p>
                          </div>
                          <div className="text-right">
                            <p className="text-[10px] text-muted-foreground">参考时价</p>
                            <p className="mt-0.5 text-sm font-semibold text-primary">{item.price ? `${formatWaveAmount(item.price)} 浪花/小时` : '详询'}</p>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </main>

    </PhoneFrame>
  )
}
