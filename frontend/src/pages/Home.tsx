import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

const API = '';

export default function Home() {
  const nav = useNavigate();
  const [roomName, setRoomName] = useState('Movie Night');
  const [hostName, setHostName] = useState('Host');
  const [joinCode, setJoinCode] = useState('');
  const [joinName, setJoinName] = useState('Guest');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const createRoom = async () => {
    if (!roomName.trim() || !hostName.trim()) {
      setError('Enter a room name and your name to create a room.');
      return;
    }
    setError('');
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/rooms`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: roomName.trim(), host_name: hostName.trim() }),
      });
      if (!r.ok) throw new Error(await r.text());
      const j = await r.json();
      sessionStorage.setItem(`room:${j.code}`, JSON.stringify({ token: j.token, pid: j.participant_id, role: 'host', name: hostName.trim() }));
      nav(`/room/${j.code}`);
    } catch (e) {
      setError(`Create failed: ${String(e)}`);
    } finally { setBusy(false); }
  };

  const joinRoom = async () => {
    const code = joinCode.trim().toUpperCase();
    if (!code || !joinName.trim()) {
      setError('Enter the room code and your name to join.');
      return;
    }
    setError('');
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/rooms/${code}/join`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: joinName.trim() }),
      });
      if (!r.ok) throw new Error(await r.text());
      const j = await r.json();
      sessionStorage.setItem(`room:${code}`, JSON.stringify({ token: j.token, pid: j.participant_id, role: 'guest', name: joinName.trim() }));
      nav(`/room/${code}`);
    } catch (e) {
      setError(`Join failed: ${String(e)}`);
    } finally { setBusy(false); }
  };

  return (
    <main className="home-page">
      <header className="site-header">
        <a className="brand" href="/" aria-label="survivor.lol home">
          <span className="brand-mark" aria-hidden="true"><span /><span /></span>
          <span>survivor<span className="brand-dot">.lol</span></span>
        </a>
        <span className="header-note"><span className="live-dot" /> Your private watch party</span>
      </header>

      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow"><span className="eyebrow-line" /> GOOD SHOWS ARE BETTER TOGETHER</p>
          <h1>Your people.<br />Your show.<br /><span>Perfectly in sync.</span></h1>
          <p className="hero-description">
            Make a room, invite your favorite people, and press play. Everyone stays right there with you.
          </p>
          <div className="hero-proof">
            <span className="avatar-stack" aria-hidden="true"><i>J</i><i>M</i><i>A</i></span>
            <span>One room. One shared moment.</span>
          </div>
        </div>

        <div className="hero-art" aria-label="A synchronized watch party">
          <div className="art-glow" />
          <div className="art-orbit orbit-one" />
          <div className="art-orbit orbit-two" />
          <div className="art-window">
            <div className="art-window-top"><span /><span /><span /><small>TONIGHT'S WATCH PARTY</small></div>
            <div className="art-screen">
              <div className="screen-horizon" />
              <div className="screen-sun" />
              <div className="screen-mountain mountain-back" />
              <div className="screen-mountain mountain-front" />
              <div className="screen-play"><span /></div>
              <div className="screen-caption">THE GOOD PART <span>NOW PLAYING</span></div>
            </div>
            <div className="art-window-bottom"><span className="mini-avatar">J</span><span className="mini-avatar second">M</span><span className="mini-avatar third">A</span><span className="watching-label">3 friends watching</span><span className="sync-label"><span className="live-dot" /> IN SYNC</span></div>
          </div>
          <div className="floating-note"><span className="note-icon">♫</span><span><b>Right on time</b><small>Every single time</small></span><span className="equalizer"><i /><i /><i /><i /><i /></span></div>
          <span className="sparkle sparkle-one">✳</span><span className="sparkle sparkle-two">✦</span>
        </div>
      </section>

      {error && <p role="alert" className="error home-error">{error}</p>}

      <section className="room-actions" aria-label="Start watching">
        <article className="action-card create-card">
          <div className="card-heading">
            <span className="action-icon create-icon" aria-hidden="true">↗</span>
            <div><p className="card-kicker">YOU'RE HOSTING</p><h2>Start a room</h2></div>
          </div>
          <p className="card-description">Set the scene. Your friends can join in seconds.</p>
          <div className="form-fields">
            <label className="field-label">Room name
              <input value={roomName} onChange={(e) => setRoomName(e.target.value)} placeholder="Movie Night" autoComplete="off" />
            </label>
            <label className="field-label">Your name
              <input value={hostName} onChange={(e) => setHostName(e.target.value)} placeholder="What should we call you?" autoComplete="name" />
            </label>
          </div>
          <button className="primary-button" onClick={() => void createRoom()} disabled={busy}>
            {busy ? 'Getting things ready…' : 'Create your room'} <span aria-hidden="true">→</span>
          </button>
        </article>

        <article className="action-card join-card">
          <div className="card-heading">
            <span className="action-icon join-icon" aria-hidden="true">↙</span>
            <div><p className="card-kicker">GOT AN INVITE?</p><h2>Join a room</h2></div>
          </div>
          <p className="card-description">Got a code from a friend? This is your way in.</p>
          <div className="form-fields">
            <label className="field-label">Room code
              <input className="code-input" value={joinCode} onChange={(e) => setJoinCode(e.target.value.toUpperCase())} placeholder="ENTER YOUR CODE" autoComplete="off" />
            </label>
            <label className="field-label">Your name
              <input value={joinName} onChange={(e) => setJoinName(e.target.value)} placeholder="What should we call you?" autoComplete="name" />
            </label>
          </div>
          <button className="secondary-button" onClick={() => void joinRoom()} disabled={busy}>
            {busy ? 'Finding your room…' : 'Join the watch party'} <span aria-hidden="true">→</span>
          </button>
        </article>
      </section>

      <footer className="home-footer"><span>MADE FOR THE “ARE YOU WATCHING THIS?” TEXTS</span><span>Bring your own popcorn.</span></footer>
    </main>
  );
}
