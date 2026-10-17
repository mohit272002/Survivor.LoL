import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { applySync, useClockOffset, usePingLoop } from '../hooks/useSyncedVideo';
import { usePovMesh } from '../hooks/usePovMesh';

type Peer = { participant_id: string; display_name: string; role: string; camera_on: boolean };
type Media = { id: string; filename: string; kind: string; stream_url: string };

export default function Room() {
  const { code = '' } = useParams();
  const sess = JSON.parse(sessionStorage.getItem(`room:${code.toUpperCase()}`) || '{}');
  const token: string = sess.token || '';
  const role: string = sess.role || 'guest';
  const selfId: string = sess.pid || '';
  const myName: string = sess.name || 'Guest';

  const [peers, setPeers] = useState<Peer[]>([]);
  const [media, setMedia] = useState<Media[]>([]);
  const [currentStream, setCurrentStream] = useState<string | null>(null);
  const [chat, setChat] = useState<{ name: string; text: string }[]>([]);
  const [chatText, setChatText] = useState('');
  const [cameraOn, setCameraOn] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState('');
  const videoRef = useRef<HTMLVideoElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  // join-in-sync: server's hello_ack carries the authoritative playback snapshot;
  // we apply it once (a) the clock offset is known via ping/pong and (b) the
  // <video> element exists and is loaded.
  const pendingSyncRef = useRef<any>(null);
  const offsetReadyRef = useRef(false);
  const applyPendingRef = useRef<() => void>(() => {});
  applyPendingRef.current = () => {
    const s = pendingSyncRef.current;
    if (!s) return;
    const v = videoRef.current;
    if (!v || !v.currentSrc) { return; } // wait for the video element to mount
    if (!offsetReadyRef.current && s.action === 'play') { return; } // need offset to project correctly
    pendingSyncRef.current = null;
    applySync(v, s.action, s.position, s.server_ts, offsetRef.current, s.rate ?? 1);
  };

  const authed = useCallback(async (path: string, opts: RequestInit = {}) => {
    const r = await fetch(path, { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }, [token]);

  const send = (obj: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(obj));
    }
  };
  const { offsetRef, ping, onPong } = useClockOffset((ts) => send({ type: 'ping', client_ts: ts }));
  usePingLoop(!!token, ping);

  const { localStream, remoteStreams, onSignal, toggleCamera } = usePovMesh({
    peers, selfId,
    sendSignal: (to, kind, payload) => send({ type: 'signal', to, kind, payload }),
  });
  const onSignalRef = useRef(onSignal);
  onSignalRef.current = onSignal;

  // WebSocket lifecycle
  useEffect(() => {
    if (!token) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/rooms/${code.toUpperCase()}?token=${encodeURIComponent(token)}&name=${encodeURIComponent(myName)}`);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === 'roster' || m.type === 'peer_left') setPeers(m.peers || []);
      else if (m.type === 'hello_ack') {
        setPeers(m.peers || []);
        if (m.sync?.media_id) setCurrentStream(`/api/media/${m.sync.media_id}/stream?code=${code.toUpperCase()}&token=${encodeURIComponent(token)}`);
        pendingSyncRef.current = m.sync;
        // measure clock offset ASAP so we can project the join position correctly
        send({ type: 'ping', client_ts: Date.now() });
      } else if (m.type === 'sync') {
        if (m.action === 'media' && m.media_id) {
          // New media selected: load it and hold at 0:00. Do NOT treat as "play".
          pendingSyncRef.current = null;
          setCurrentStream(`/api/media/${m.media_id}/stream?code=${code.toUpperCase()}&token=${encodeURIComponent(token)}`);
          const v = videoRef.current;
          if (v) { v.pause(); v.currentTime = 0; }
          return;
        }
        applySync(videoRef.current, m.action, m.position, m.server_ts, offsetRef.current, m.rate ?? 1);
      } else if (m.type === 'signal') {
        void onSignalRef.current(m).catch((reason: unknown) => {
          setError(`Camera connection failed: ${String(reason)}`);
        });
      }
      else if (m.type === 'pong') {
        onPong(m.client_ts, m.server_ts);
        offsetReadyRef.current = true;
        applyPendingRef.current();
      }
      else if (m.type === 'chat') setChat((c) => [...c, { name: m.name, text: m.text }].slice(-50));
      else if (m.type === 'camera') setPeers((p) => p.map((x) => x.participant_id === m.from ? { ...x, camera_on: m.on } : x));
      else if (m.type === 'error') setError(m.message || 'The room could not complete that action.');
    };
    ws.onclose = () => {
      if (wsRef.current === ws) wsRef.current = null;
    };
    return () => {
      ws.close();
      if (wsRef.current === ws) wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, token]);

  const streamUrl = (mid: string) => `/api/media/${mid}/stream?code=${code.toUpperCase()}&token=${encodeURIComponent(token)}`;

  const selectMedia = async (id: string) => {
    // set the video locally right away — don't wait for the WS broadcast
    setCurrentStream(streamUrl(id));
    pendingSyncRef.current = null;
    const v = videoRef.current;
    if (v) { v.pause(); v.currentTime = 0; }
    await authed(`/api/rooms/${code.toUpperCase()}/media/select`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ media_id: id }),
    });
  };

  const refreshFiles = useCallback(async (): Promise<Media[]> => {
    const files = await authed(`/api/rooms/${code.toUpperCase()}/files`);
    setMedia(files);
    return files;
  }, [authed, code]);
  useEffect(() => {
    void refreshFiles().catch((reason: unknown) => {
      setError(`Could not load room media: ${String(reason)}`);
    });
  }, [refreshFiles]);

  const upload = async (f: File) => {
    const uploaded = await authed(`/api/rooms/${code.toUpperCase()}/media/upload`, {
      method: 'POST', body: (() => { const fd = new FormData(); fd.append('file', f); return fd; })(),
    });
    const files = await refreshFiles();
    // auto-select the freshly uploaded file so it's immediately playable
    const target = files.find((m) => m.id === uploaded.id) ?? files[files.length - 1];
    if (target) await selectMedia(target.id);
  };

  const hostControl = (type: 'play' | 'pause' | 'seek') => {
    const v = videoRef.current;
    const pos = v?.currentTime || 0;
    // act immediately inside the user gesture (autoplay policy)…
    if (type === 'play') {
      void v?.play().catch((reason: unknown) => {
        setError(`Could not play this video: ${String(reason)}`);
      });
    }
    else if (type === 'pause') v?.pause();
    // …then broadcast the authoritative command with current time + rate
    send({ type, position: pos, rate: v?.playbackRate || 1 });
  };

  const toggleCam = async () => {
    setError('');
    try {
      const stream = await toggleCamera(!cameraOn);
      setCameraOn(!!stream);
      send({ type: 'camera', on: !!stream });
    } catch (reason) {
      setError(`Could not access your camera: ${String(reason)}`);
    }
  };

  if (!token) return <main className="room-page"><section className="panel empty-state"><span className="action-icon join-icon">↗</span><h1>You're not in a room yet</h1><p>No session found. Head back and join with the room code.</p><a className="primary-button button-link" href="/">Back to home <span aria-hidden="true">→</span></a></section></main>;

  return (
    <main className="room-page">
      <header className="room-header">
        <a className="brand" href="/" aria-label="survivor.lol home">
          <span className="brand-mark" aria-hidden="true"><span /><span /></span>
          <span>survivor<span className="brand-dot">.lol</span></span>
        </a>
        <div className="room-heading">
          <div><p className="card-kicker">YOUR WATCH PARTY</p><h1>Room <span className="badge room-code">{code.toUpperCase()}</span></h1></div>
          <span className="role-pill">{role === 'host' ? 'HOST' : 'GUEST'}</span>
        </div>
        <button
          className="ghost invite-button"
          onClick={() => {
            void navigator.clipboard.writeText(`${location.origin}/room/${code.toUpperCase()}`)
              .then(() => setError('Invite link copied.'))
              .catch((reason: unknown) => setError(`Could not copy invite link: ${String(reason)}`));
          }}
        >
          <span aria-hidden="true">↗</span> Copy invite link
        </button>
      </header>
      {error && <p role="status" className="error room-error">{error}</p>}

      <div className="room-layout">
        <div className="room-main-column">
          <section className="panel player-panel">
            <div className="section-heading">
              <div><p className="card-kicker">THE BIG SCREEN</p><h2>Now watching</h2></div>
              {currentStream && <span className="watching-pill"><span className="live-dot" /> LIVE TOGETHER</span>}
            </div>
            {currentStream ? (
              <>
                <div className="video-frame">
                  <video
                    ref={videoRef}
                    src={currentStream}
                    controls={false}
                    playsInline
                    onLoadedMetadata={(event) => {
                      setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0);
                      applyPendingRef.current();
                    }}
                    onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
                  />
                </div>
                {role === 'host' && (
                  <div className="row player-controls">
                    <span className="small time-label">{formatTime(currentTime)}</span>
                    <input
                      className="seek"
                      aria-label="Seek video"
                      type="range"
                      min={0}
                      max={duration || 0}
                      step={0.1}
                      value={Math.min(currentTime, duration || 0)}
                      disabled={!duration}
                      onChange={(event) => {
                        const next = Number(event.target.value);
                        if (videoRef.current) videoRef.current.currentTime = next;
                        setCurrentTime(next);
                        send({ type: 'seek', position: next, rate: videoRef.current?.playbackRate || 1 });
                      }}
                    />
                    <span className="small time-label">{formatTime(duration)}</span>
                  </div>
                )}
              </>
            ) : (
              <div className="empty-player"><span className="empty-play" aria-hidden="true">▶</span><h3>No video selected</h3><p>{role !== 'host' ? 'Your host will pick something to watch.' : 'Upload a video below to get the watch party started.'}</p></div>
            )}
            {role === 'host' && (
              <div className="host-controls">
                <div className="control-buttons">
                  <button className="primary-button compact-button" onClick={() => hostControl('play')} disabled={!currentStream}><span aria-hidden="true">▶</span> Play</button>
                  <button className="ghost compact-button" onClick={() => hostControl('pause')}><span aria-hidden="true">Ⅱ</span> Pause</button>
                </div>
                <label className="upload-button">
                  <span aria-hidden="true">＋</span> Upload video
                  <input type="file" accept="video/*" onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void upload(file).catch((reason: unknown) => setError(`Upload failed: ${String(reason)}`));
                    e.target.value = '';
                  }} />
                </label>
              </div>
            )}
          </section>

          <section className="panel media-panel">
            <div className="section-heading">
              <div><p className="card-kicker">PICK THE NEXT THING</p><h2>Room library</h2></div>
              <span className="item-count">{media.length} {media.length === 1 ? 'video' : 'videos'}</span>
            </div>
            {role === 'host' && media.length > 0 ? (
              <div className="media-list">
                {media.map((m) => <button key={m.id} className={`media-item${currentStream === streamUrl(m.id) ? ' selected' : ''}`} onClick={() => {
                  void selectMedia(m.id).catch((reason: unknown) => setError(`Could not select video: ${String(reason)}`));
                }}><span className="media-play" aria-hidden="true">▶</span><span className="media-filename">{m.filename}</span><span className="media-arrow" aria-hidden="true">↗</span></button>)}
              </div>
            ) : <p className="library-hint">{role === 'host' ? 'Your uploaded videos will show up here.' : 'Your host controls the room library.'}</p>}
          </section>

          <section className="panel camera-panel">
            <div className="section-heading">
              <div><p className="card-kicker">SEE THE REACTIONS</p><h2>POV cameras</h2></div>
              <button className={`ghost compact-button${cameraOn ? ' camera-active' : ''}`} onClick={() => void toggleCam()}><span aria-hidden="true">{cameraOn ? '■' : '◉'}</span> {cameraOn ? 'Stop camera' : 'Share camera'}</button>
            </div>
            <div className="pov-grid">
              {localStream && <div className="camera-tile"><video ref={(v) => { if (v && v.srcObject !== localStream) v.srcObject = localStream; }} autoPlay muted playsInline /><div className="camera-caption"><span className="live-dot" /> You</div></div>}
              {Object.entries(remoteStreams).filter(([pid]) => peers.some((p) => p.participant_id === pid && p.camera_on)).map(([pid, s]) => (
                <div key={pid} className="camera-tile"><video ref={(v) => { if (v && v.srcObject !== s) v.srcObject = s; }} autoPlay playsInline /><div className="camera-caption">{peers.find((p) => p.participant_id === pid)?.display_name || pid.slice(0, 6)}</div></div>
              ))}
              {!localStream && !peers.some((p) => p.camera_on) && <div className="camera-empty"><span aria-hidden="true">◉</span> Turn on your camera to watch reactions together.</div>}
            </div>
          </section>
        </div>

        <aside className="room-side-column">
          <section className="panel people-panel">
            <div className="section-heading"><div><p className="card-kicker">THE CREW</p><h2>Watching with you</h2></div><span className="people-count">{peers.length}</span></div>
            {peers.length ? <div className="people-list">{peers.map((p) => <div key={p.participant_id} className="person-row"><span className="person-avatar">{p.display_name.slice(0, 1).toUpperCase()}</span><span className="person-name">{p.display_name}{p.participant_id === selfId && <small>YOU</small>}</span><span className="person-indicators">{p.role === 'host' && <span title="Host">✦</span>}{p.camera_on && <span title="Camera on">◉</span>}</span></div>)}</div> : <p className="library-hint">Waiting for your crew to arrive.</p>}
          </section>

          <section className="panel chat-panel">
            <div className="section-heading"><div><p className="card-kicker">SAY IT IN THE MOMENT</p><h2>Room chat</h2></div><span className="chat-status"><span className="live-dot" /> LIVE</span></div>
            <div className="chat">
              {chat.length ? chat.map((c, i) => <div key={i} className={`chat-message${c.name === myName ? ' own-message' : ''}`}><b>{c.name}</b><span>{c.text}</span></div>) : <div className="chat-empty"><span aria-hidden="true">✳</span><p>It's a little quiet in here.</p><small>Be the one to break the ice.</small></div>}
            </div>
            <div className="chat-compose">
              <input value={chatText} onChange={(e) => setChatText(e.target.value)} placeholder="Send a message…" aria-label="Chat message" onKeyDown={(e) => { if (e.key === 'Enter') sendChat(); }} />
              <button className="send-button" onClick={sendChat} aria-label="Send message">↑</button>
            </div>
          </section>
          <a className="room-home-link" href="/">← Back to survivor.lol</a>
        </aside>
      </div>
    </main>
  );

  function sendChat() {
    const text = chatText.trim();
    if (!text) return;
    send({ type: 'chat', text });
    setChatText('');
  }
}

function formatTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60).toString().padStart(2, '0');
  return `${minutes}:${remainingSeconds}`;
}
