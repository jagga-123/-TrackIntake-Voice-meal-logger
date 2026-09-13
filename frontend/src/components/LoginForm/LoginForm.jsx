import PropTypes from 'prop-types';
import { useState } from 'react';

import { getApiErrorMessage, login } from '../../services/mealApi.js';
import styles from './LoginForm.module.css';

/**
 * Minimal username/password form that exchanges credentials for a JWT pair.
 * Accounts are created with `python manage.py createsuperuser`.
 */
export default function LoginForm({ onSuccess }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username.trim(), password);
      onSuccess();
    } catch (requestError) {
      setError(
        requestError.response?.status === 401
          ? 'Incorrect username or password.'
          : getApiErrorMessage(requestError, 'Could not sign in.'),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className={styles.card} onSubmit={handleSubmit}>
      <h2 className={styles.title}>Sign in</h2>
      <p className={styles.subtitle}>Use the account created with <code>manage.py createsuperuser</code>.</p>

      <label className={styles.field}>
        <span>Username</span>
        <input
          type="text"
          autoComplete="username"
          // Without these, mobile keyboards auto-capitalise the first letter
          // and may autocorrect it, silently turning "demo" into "Demo" as
          // you type - the login then fails with a password that is correct.
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          required
        />
      </label>

      <label className={styles.field}>
        <span>Password</span>
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
      </label>

      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}

      <button type="submit" className={styles.submit} disabled={submitting || !username || !password}>
        {submitting ? 'Signing in…' : 'Sign in'}
      </button>
    </form>
  );
}

LoginForm.propTypes = {
  onSuccess: PropTypes.func.isRequired,
};
