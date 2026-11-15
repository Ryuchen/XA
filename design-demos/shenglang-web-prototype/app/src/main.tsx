import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router'
import 'misans/lib/Normal/MiSans-Normal.min.css'
import 'misans/lib/Normal/MiSans-Medium.min.css'
import 'misans/lib/Normal/MiSans-Demibold.min.css'
import 'misans/lib/Normal/MiSans-Bold.min.css'
import './index.css'
import App from './App.tsx'
import { StoreProvider } from '@/components/store/store-context'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <StoreProvider>
        <App />
      </StoreProvider>
    </HashRouter>
  </StrictMode>,
)
