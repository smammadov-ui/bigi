import React, { useState } from 'react';
import Home from './Home.jsx';
import Settings from './Settings.jsx';

// Two-screen SPA: Home (generate the §840 declaration) and Settings
// (LLM / Back-Office / Jira credentials). No router — a tiny state switch.
export default function App() {
  const [screen, setScreen] = useState('home');

  return (
    <div className="shell">
      <nav className="topnav">
        <span className="brand">
          bigi<span className="dot">.</span>
        </span>
        <span className="brand-sub">Drittschuldnererklärung</span>
        <span className="nav-spacer" />
        <div className="navlinks">
          <button
            className={`navlink${screen === 'home' ? ' active' : ''}`}
            onClick={() => setScreen('home')}
          >
            Home
          </button>
          <button
            className={`navlink${screen === 'settings' ? ' active' : ''}`}
            onClick={() => setScreen('settings')}
          >
            Settings
          </button>
        </div>
      </nav>

      <main className="content">
        {screen === 'home' ? <Home /> : <Settings />}
      </main>
    </div>
  );
}
