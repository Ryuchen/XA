import { StoreGate } from "@xa/shared"
import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { TooltipProvider } from '@/components/ui/tooltip'
import './index.css'
import '@xa/shared/theme.css'
const App = lazy(() => import("./App"))
createRoot(document.getElementById('root')!).render(<StrictMode><TooltipProvider>{import.meta.env.VITE_ACCEPTANCE === 'true' && <div role="status" className="xa-acceptance-banner">隔离验收环境 · 测试身份与测试数据 · 微信授权和短信发送使用测试替身</div>}<StoreGate role="boss"><Suspense fallback={<div role="status" className="p-6 text-sm text-muted-foreground">正在载入页面…</div>}><App /></Suspense></StoreGate></TooltipProvider></StrictMode>)
