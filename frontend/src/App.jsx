import { useCallback, useEffect, useState } from 'react';

import styles from './App.module.css';
import LoginForm from './components/LoginForm/LoginForm.jsx';
import MealHistory from './components/MealHistory/MealHistory.jsx';
import VoiceMealLogger from './components/VoiceMealLogger/VoiceMealLogger.jsx';
import { UNAUTHORIZED_EVENT } from './services/mealApi.js';
import { clearSession, hasSession } from './services/tokenStorage.js';

/**
 * Application shell: header, auth gate, the voice logger and recent history.
 */
export default function App() {
  const [authenticated, setAuthenticated] = useState(hasSession);
  const [historyVersion, setHistoryVersion] = useState(0);

  const signOut = useCallback(() => {
    clearSession();
    setAuthenticated(false);
  }, []);

  // The API client fires this when a refresh fails, i.e. the session is dead.
  useEffect(() => {
    window.addEventListener(UNAUTHORIZED_EVENT, signOut);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, signOut);
  }, [signOut]);

  const handleSaved = useCallback(() => setHistoryVersion((version) => version + 1), []);

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.logo} aria-hidden="true">
            TI
          </span>
          <div>
            <h1 className={styles.title}>TrackIntake</h1>
            <p className={styles.subtitle}>Voice meal logger</p>
          </div>
        </div>
        {authenticated && (
          <button type="button" className={styles.signOut} onClick={signOut}>
            Sign out
          </button>
        )}
      </header>

      <main className={styles.main}>
        {authenticated ? (
          <>
            <VoiceMealLogger onSaved={handleSaved} />
            <MealHistory refreshKey={historyVersion} />
          </>
        ) : (
          <LoginForm onSuccess={() => setAuthenticated(true)} />
        )}
      </main>

      <footer className={styles.footer}>
        Audio → Whisper → LLM → nutrition table → preview → confirm
      </footer>
    </div>
  );
}
