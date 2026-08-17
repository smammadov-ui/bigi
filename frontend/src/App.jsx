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
        <span className="brand-sub">Third-party debtor declaration generator</span>
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

      {/* Both screens stay MOUNTED (hidden via CSS) so navigating to Settings
          never discards an in-progress case on Home (audit B18). */}
      <main className="content">
        <div style={{ display: screen === 'home' ? 'block' : 'none' }}>
          <Home />
        </div>
        <div style={{ display: screen === 'settings' ? 'block' : 'none' }}>
          <Settings />
        </div>
      </main>
    </div>
  );
}
