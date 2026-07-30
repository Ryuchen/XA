import { useCallback, useEffect, useRef, useState } from 'react';
import Taro from '@tarojs/taro';

/**
 * 语音卡试听 hook：页面级单例 InnerAudioContext，供陪玩卡片试听语音介绍。
 * 返回 playingId（当前正在播放的陪玩 id）与 toggle（点击试听/暂停）。
 * 再次点击同一人或切换他人会停止上一段；组件卸载时销毁音频实例，避免泄漏。
 */
export const useVoicePreview = () => {
  const audioRef = useRef<Taro.InnerAudioContext | null>(null);
  const [playingId, setPlayingId] = useState<number | null>(null);
  const playingIdRef = useRef<number | null>(null);

  const setPlaying = useCallback((id: number | null) => {
    playingIdRef.current = id;
    setPlayingId(id);
  }, []);

  const ensureAudio = useCallback(() => {
    if (audioRef.current) return audioRef.current;
    const audio = Taro.createInnerAudioContext();
    audio.onEnded(() => setPlaying(null));
    audio.onStop(() => setPlaying(null));
    audio.onError(() => {
      setPlaying(null);
      Taro.showToast({ title: '语音播放失败', icon: 'none' });
    });
    audioRef.current = audio;
    return audio;
  }, [setPlaying]);

  const stop = useCallback(() => {
    audioRef.current?.stop();
    setPlaying(null);
  }, [setPlaying]);

  const toggle = useCallback((id: number, url?: string) => {
    if (!url) {
      Taro.showToast({ title: '该陪玩暂无语音介绍', icon: 'none' });
      return;
    }
    if (playingIdRef.current === id) {
      stop();
      return;
    }
    const audio = ensureAudio();
    audio.stop();
    audio.src = url;
    audio.play();
    setPlaying(id);
  }, [ensureAudio, stop, setPlaying]);

  useEffect(() => () => {
    audioRef.current?.destroy();
    audioRef.current = null;
  }, []);

  return { playingId, toggle, stop };
};
