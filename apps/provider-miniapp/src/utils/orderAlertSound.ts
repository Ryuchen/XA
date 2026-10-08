import Taro from '@tarojs/taro';
import bellUrl from '@/assets/new-order-bell.wav';

/** One short bell strike. Unlock web audio during normal user interaction. */
class OrderAlertSound {
  private webAudio: AudioContext | null = null;
  private nativeAudio: Taro.InnerAudioContext | null = null;
  private listening = false;
  private tones = new Set<OscillatorNode>();

  private unlock = () => {
    try {
      const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioContextClass) return;
      if (!this.webAudio || this.webAudio.state === 'closed') this.webAudio = new AudioContextClass();
      if (this.webAudio.state === 'suspended') void this.webAudio.resume().catch(() => {});
    } catch { /* Audio support must never interrupt order operations. */ }
  };

  prepare() {
    if (process.env.TARO_ENV !== 'h5' || this.listening) return;
    this.listening = true;
    for (const event of ['click', 'touchend', 'keydown']) document.addEventListener(event, this.unlock, true);
  }

  play() {
    try {
      if (process.env.TARO_ENV === 'h5') {
        // Do not queue an old alert to play after a later user gesture.
        if (!this.webAudio || this.webAudio.state !== 'running') return;
        this.stop();
        const context = this.webAudio, start = context.currentTime;
        for (const [frequency, volume] of [[880, 0.28], [1320, 0.12], [1760, 0.06]]) {
          const tone = context.createOscillator(), gain = context.createGain();
          tone.type = 'sine';
          tone.frequency.value = frequency;
          gain.gain.setValueAtTime(0, start);
          gain.gain.linearRampToValueAtTime(volume, start + 0.008);
          gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.65);
          tone.connect(gain);
          gain.connect(context.destination);
          this.tones.add(tone);
          tone.onended = () => { tone.disconnect(); gain.disconnect(); this.tones.delete(tone); };
          tone.start(start);
          tone.stop(start + 0.65);
        }
      } else {
        if (!this.nativeAudio) {
          const audio = Taro.createInnerAudioContext();
          audio.src = bellUrl;
          audio.autoplay = false;
          audio.loop = false;
          audio.volume = 0.8;
          audio.onError(() => { if (this.nativeAudio === audio) this.nativeAudio = null; audio.destroy(); });
          this.nativeAudio = audio;
        }
        this.nativeAudio.stop();
        this.nativeAudio.play();
      }
    } catch { /* A blocked or unavailable speaker does not block the order list. */ }
  }

  stop() {
    for (const tone of this.tones) { try { tone.stop(); } catch { /* already ended */ } }
    this.tones.clear();
    try { this.nativeAudio?.stop(); } catch { /* already stopped */ }
  }

  dispose() {
    this.stop();
    this.nativeAudio?.destroy();
    this.nativeAudio = null;
    if (this.webAudio) void this.webAudio.close().catch(() => {});
    this.webAudio = null;
    if (this.listening) {
      for (const event of ['click', 'touchend', 'keydown']) document.removeEventListener(event, this.unlock, true);
      this.listening = false;
    }
  }
}

export const orderAlertSound = new OrderAlertSound();
