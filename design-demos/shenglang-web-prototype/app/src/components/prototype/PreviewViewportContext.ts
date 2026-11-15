import { createContext } from 'react'

export type PreviewViewport = 'mobile' | 'pc' | 'tablet'

export const PreviewViewportContext = createContext<PreviewViewport | null>(null)
