import { ref } from 'vue'

const readEnabled = (key: string) => { try { return localStorage.getItem(key) !== 'off' } catch { return true } }

// Browsers require an ordinary user gesture before audio can play. An old
// alert is never queued for playback after a later gesture.
class StaffNoticeSound {
  readonly enabled
  private context: AudioContext | null = null
  private listening = false
  private playing = false
  constructor(private readonly key: string) { this.enabled = ref(readEnabled(key)) }

  private unlock = () => {
    if (!this.enabled.value) return
    try {
      const Context = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!Context) return
      if (!this.context || this.context.state === 'closed') this.context = new Context()
      if (this.context.state === 'suspended') void this.context.resume().catch(() => {})
    } catch { /* Sound is supplementary to the visible notification. */ }
  }

  prepare() {
    if (this.listening) return
    this.listening = true
    for (const event of ['click', 'touchend', 'keydown']) document.addEventListener(event, this.unlock, true)
  }

  toggle() {
    this.enabled.value = !this.enabled.value
    try { localStorage.setItem(this.key, this.enabled.value ? 'on' : 'off') } catch { /* Keep this session's preference. */ }
    if (this.enabled.value) this.unlock()
  }

  play() {
    if (!this.enabled.value || !this.context || this.context.state !== 'running' || this.playing) return
    try {
      const context = this.context, tone = context.createOscillator(), gain = context.createGain(), start = context.currentTime
      this.playing = true
      tone.type = 'sine'; tone.frequency.value = 880
      gain.gain.setValueAtTime(0, start); gain.gain.linearRampToValueAtTime(.12, start + .008)
      gain.gain.exponentialRampToValueAtTime(.0001, start + .35)
      tone.connect(gain); gain.connect(context.destination)
      tone.onended = () => { tone.disconnect(); gain.disconnect(); this.playing = false }
      tone.start(start); tone.stop(start + .35)
    } catch { this.playing = false }
  }

  dispose() {
    if (this.listening) for (const event of ['click', 'touchend', 'keydown']) document.removeEventListener(event, this.unlock, true)
    this.listening = false
    if (this.context) void this.context.close().catch(() => {})
    this.context = null; this.playing = false
  }
}

export const staffNoticeSound = new StaffNoticeSound('xa_admin_dispatch_sound')
export const chatNoticeSound = new StaffNoticeSound('xa_admin_chat_sound')
