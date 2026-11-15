import { ClipboardList, Headphones, Home, MessageSquare, UserRound } from 'lucide-react'
import { useNavigate } from 'react-router'
import { MobileDock } from '@/components/prototype/MobileDock'

const tabs = [
  { key: 'home', label: '首页', icon: Home },
  { key: 'companions', label: '陪玩', icon: Headphones },
  { key: 'orders', label: '订单', icon: ClipboardList },
  { key: 'messages', label: '消息', icon: MessageSquare, unread: 3 },
  { key: 'mine', label: '我的', icon: UserRound },
]

interface BottomDockProps {
  active: 'home' | 'companions' | 'orders' | 'messages' | 'mine'
  onNavigate: (key: string, label: string) => void
}

export function BottomDock({ active, onNavigate }: BottomDockProps) {
  const navigate = useNavigate()
  function navigateTab(key: string, label: string) {
    if (key === 'messages' || key === 'mine') return navigate(`/${key}`)
    if (key === 'orders') return navigate('/orders', { state: { fromDock: true } })
    onNavigate(key, label)
  }
  return <MobileDock items={tabs} active={active} ariaLabel="老板端底部导航" onNavigate={navigateTab} />
}
