import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import { defaultStoreSlug, prototypeStores, type StoreInfo, type StoreProduct } from '@/components/store/store-data'

function readStoreSlug() {
  if (typeof window === 'undefined') return defaultStoreSlug
  const candidate = new URLSearchParams(window.location.search).get('store')
  return prototypeStores.some(store => store.slug === candidate && store.status === 'ACTIVE') ? candidate! : defaultStoreSlug
}

interface StoreContextValue {
  store: StoreInfo
  stores: StoreInfo[]
  switchStore: (slug: string, product: StoreProduct) => void
}

const StoreContext = createContext<StoreContextValue | null>(null)

export function StoreProvider({ children }: { children: ReactNode }) {
  const [slug] = useState(readStoreSlug)
  const value = useMemo<StoreContextValue>(() => ({
    store: prototypeStores.find(store => store.slug === slug) || prototypeStores[0],
    stores: prototypeStores,
    switchStore(nextSlug, product) {
      if (!prototypeStores.some(store => store.slug === nextSlug && store.status === 'ACTIVE')) return
      const url = new URL(window.location.href)
      url.search = ''
      url.searchParams.set('store', nextSlug)
      const currentHash = window.location.hash
      url.hash = product === 'boss'
        ? currentHash.includes('/login') ? '/login' : '/home'
        : product === 'provider'
          ? currentHash.includes('/provider-register') ? '/provider-register' : currentHash.includes('/provider-login') ? '/provider-login' : '/provider'
          : '/admin-login'
      // 完整页面导航用于销毁旧店详情、草稿、弹窗、实时连接和待处理原型操作。
      window.location.assign(url.href)
    },
  }), [slug])

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>
}

export function useStore() {
  const value = useContext(StoreContext)
  if (!value) throw new Error('useStore must be used within StoreProvider')
  return value
}
