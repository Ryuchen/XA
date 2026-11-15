import { useState } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'
import { ProviderPrototypeShell } from '@/components/provider/ProviderPrototypeShell'
import { ProviderWorkbenchPage } from '@/pages/provider/ProviderWorkbenchPage'
import { ProviderReportsPage, ProviderReservationsPage } from '@/pages/provider/ProviderFulfillmentPages'
import { ProviderPassPage, ProviderPointsPage, ProviderWalletPage } from '@/pages/provider/ProviderAccountPages'
import { ProviderEvaluationsPage, ProviderMessagesPage, ProviderSupportPage } from '@/pages/provider/ProviderCommunicationPages'
import { ProviderAuditionsPage, ProviderMinePage, ProviderProfilePage, ProviderSkillsPage } from '@/pages/provider/ProviderProfilePages'
import { ProviderSecurityPage } from '@/pages/provider/ProviderSecurityPage'
import { ProviderLoginPage, ProviderRegisterPage } from '@/pages/provider/ProviderAuthPages'
import { ProviderApplicationDetailPage, ProviderApplicationsPage, ProviderSupportChatPage, ProviderWithdrawAccountsPage } from '@/pages/provider/ProviderWorkflowPages'
import { ProviderAuditionInvitePage } from '@/pages/provider/ProviderAuditionInvitePage'
import type { ProviderScenario } from '@/pages/provider/provider-data'

export function ProviderArea({ dark, onThemeChange }: { dark: boolean; onThemeChange: (dark: boolean) => void }) {
  const location = useLocation()
  const [scenarioState, setScenarioState] = useState<{ path: string; value: ProviderScenario }>({ path: location.pathname, value: 'default' })
  const scenario = scenarioState.path === location.pathname ? scenarioState.value : 'default'
  const setScenario = (value: ProviderScenario) => setScenarioState({ path: location.pathname, value })
  const retry = () => setScenario('default')
  return <ProviderPrototypeShell dark={dark} onThemeChange={onThemeChange} scenario={scenario} onScenarioChange={setScenario}>
    <Routes>
      <Route path="/provider-login" element={<ProviderLoginPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider-register" element={<ProviderRegisterPage key={scenario} scenario={scenario} />} />
      <Route path="/provider" element={<ProviderWorkbenchPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/applications" element={<ProviderApplicationsPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/applications/:orderId" element={<ProviderApplicationDetailPage key={`${location.pathname}-${scenario}`} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/reports" element={<ProviderReportsPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/reservations" element={<ProviderReservationsPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/messages" element={<ProviderMessagesPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/support-chat" element={<ProviderSupportChatPage key={`${location.search}-${scenario}`} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/wallet" element={<ProviderWalletPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/withdraw-accounts" element={<ProviderWithdrawAccountsPage key={`${location.search}-${scenario}`} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/mine" element={<ProviderMinePage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/profile" element={<ProviderProfilePage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/security" element={<ProviderSecurityPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/skills" element={<ProviderSkillsPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/evaluations" element={<ProviderEvaluationsPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/pass" element={<ProviderPassPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/points" element={<ProviderPointsPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/support" element={<ProviderSupportPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/auditions" element={<ProviderAuditionsPage key={scenario} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/audition" element={<ProviderAuditionInvitePage key={`${location.search}-${scenario}`} scenario={scenario} onRetry={retry} />} />
      <Route path="/provider/*" element={<Navigate to="/provider" replace />} />
    </Routes>
  </ProviderPrototypeShell>
}
