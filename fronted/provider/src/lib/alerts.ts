import { useEffect, useRef, useState } from "react"
import {
  createProviderOrderAlerts,
  useRealtime,
  useSession,
  getStoreSlug,
} from "@xa/shared"

export function useOrderAlerts() {
  const session = useSession("provider"),
    storeSlug = getStoreSlug()
  const [enabled, setEnabled] = useState(() => {
    try {
      return localStorage.getItem("xa_fronted_provider_sound") !== "off"
    } catch {
      return true
    }
  })
  const controller = useRef<ReturnType<
    typeof createProviderOrderAlerts
  > | null>(null)
  useRealtime("provider", (event) => {
    void controller.current?.event(event)
  })
  useEffect(() => {
    let audio: AudioContext | null = null
    let master: GainNode | null = null
    const active = createProviderOrderAlerts({
      enabled,
      isVisible: () => !document.hidden,
      persistEnabled: (value) =>
        localStorage.setItem("xa_fronted_provider_sound", value ? "on" : "off"),
      onEnabledChange: setEnabled,
      audio: {
        isRunning: () => audio?.state === "running",
        unlock() {
          if (!audio || audio.state === "closed") {
            audio = new AudioContext()
            master = audio.createGain()
            master.gain.value = 1
            master.connect(audio.destination)
          }
          if (audio.state !== "running") return audio.resume()
        },
        play() {
          if (!audio || !master) return
          const context = audio,
            start = context.currentTime
          for (const [frequency, volume] of [
            [880, 0.72],
            [1320, 0.28],
          ]) {
            const tone = context.createOscillator(),
              gain = context.createGain()
            tone.frequency.value = frequency
            gain.gain.setValueAtTime(0, start)
            gain.gain.linearRampToValueAtTime(volume, start + 0.01)
            gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.6)
            tone.connect(gain)
            gain.connect(master)
            tone.start(start)
            tone.stop(start + 0.6)
            tone.onended = () => {
              tone.disconnect()
              gain.disconnect()
            }
          }
        },
        close() {
          const context = audio
          audio = null
          master?.disconnect()
          master = null
          return context?.close()
        },
      },
    })
    controller.current = active
    const unlock = () => {
      void active.unlock()
    }
    const visible = () => {
      void active.resume()
    }
    document.addEventListener("click", unlock, true)
    document.addEventListener("touchend", unlock, true)
    document.addEventListener("keydown", unlock, true)
    document.addEventListener("visibilitychange", visible)
    void active.start()
    return () => {
      document.removeEventListener("click", unlock, true)
      document.removeEventListener("touchend", unlock, true)
      document.removeEventListener("keydown", unlock, true)
      document.removeEventListener("visibilitychange", visible)
      active.dispose()
      if (controller.current === active) controller.current = null
    }
  }, [storeSlug, session.revision])
  return { enabled, toggle: () => controller.current?.toggle() }
}
