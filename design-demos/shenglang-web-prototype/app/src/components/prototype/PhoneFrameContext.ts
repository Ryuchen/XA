import { createContext, useContext } from 'react'

export const PhoneFrameContext = createContext<HTMLElement | null>(null)

export function usePhoneFrameContainer() {
  return useContext(PhoneFrameContext)
}
