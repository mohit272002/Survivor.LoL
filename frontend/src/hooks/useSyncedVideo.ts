/** useSyncedVideo: latency-compensated playback.
 * Server sends {action, position, server_ts, rate}; we estimate clock offset with
 * ping/pong (RTT/2) and project position forward so joins land in sync.
 * Small drift (<0.25s) is fixed via playbackRate nudging to avoid jumps.
 */
import { useEffect, useRef } from 'react';

export function useClockOffset(sendPing: (ts: number) => void) {
  const offsetRef = useRef(0); // server - client clock (ms)
  const ping = () => sendPing(Date.now());
  const onPong = (clientTs: number, serverTs: number) => {
    const now = Date.now();
    const rtt = now - clientTs;
    offsetRef.current = serverTs - (clientTs + rtt / 2);
  };
  return { offsetRef, ping, onPong };
}

/** Play, falling back to muted-autoplay if the browser blocks a non-gesture
 *  play() (autoplay policy), then unmute on the first user click. */
async function safePlay(video: HTMLVideoElement) {
  try {
    await video.play();
  } catch (err) {
    if ((err as DOMException)?.name === 'NotAllowedError') {
      video.muted = true;
      try { await video.play(); } catch { /* user must tap manually */ }
      const unmute = () => { video.muted = false; window.removeEventListener('pointerdown', unmute); };
      window.addEventListener('pointerdown', unmute, { once: true });
    }
  }
}

export function applySync(
  video: HTMLVideoElement | null,
  action: string,
  position: number,
  serverTs: number,
  offsetMs: number,
  rate = 1,
) {
  if (!video) return;
  const serverNow = Date.now() + offsetMs;
  const elapsed = Math.max(0, (serverNow - serverTs) / 1000);
  const isPause = action === 'pause';
  // While playing, content advances in real time from the authoritative anchor.
  let target = isPause ? position : position + elapsed * rate;
  const dur = video.duration;
  if (Number.isFinite(dur) && dur > 0 && target > dur - 0.05) target = Math.max(0, dur - 0.05);
  const drift = target - video.currentTime;
  if (Math.abs(drift) > 0.6 || !Number.isFinite(video.currentTime)) {
    video.currentTime = Math.max(0, target); // hard seek on large drift / seek events
  } else if (Math.abs(drift) > 0.06) {
    video.playbackRate = drift > 0 ? 1.05 : 0.95; // gentle nudge
    setTimeout(() => { if (video) video.playbackRate = rate; }, 800);
  }
  if (isPause) video.pause();
  else void safePlay(video);
}

export function usePingLoop(enabled: boolean, ping: () => void) {
  useEffect(() => {
    if (!enabled) return;
    const t = setInterval(ping, 5000);
    ping();
    return () => clearInterval(t);
  }, [enabled]);
}