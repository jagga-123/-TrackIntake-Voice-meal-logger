import PropTypes from 'prop-types';
import { useEffect, useState } from 'react';

import { getApiErrorMessage, listMealLogs } from '../../services/mealApi.js';
import styles from './MealHistory.module.css';

const PAGE_SIZE = 8;

const dateFormatter = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'short',
  hour: 'numeric',
  minute: '2-digit',
});

const formatQuantity = (quantity) => Number(quantity).toLocaleString('en-IN', { maximumFractionDigits: 2 });

/**
 * Recent meal logs for the signed-in user, re-fetched whenever `refreshKey` changes.
 */
export default function MealHistory({ refreshKey }) {
  const [status, setStatus] = useState('loading');
  const [logs, setLogs] = useState([]);
  const [count, setCount] = useState(0);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setStatus('loading');
    listMealLogs({ pageSize: PAGE_SIZE })
      .then((data) => {
        if (cancelled) return;
        setLogs(data.results);
        setCount(data.count);
        setStatus('ready');
      })
      .catch((requestError) => {
        if (cancelled) return;
        setError(getApiErrorMessage(requestError, 'Could not load your meal history.'));
        setStatus('error');
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  return (
    <section className={styles.card}>
      <header className={styles.header}>
        <h2 className={styles.title}>Recent meals</h2>
        {status === 'ready' && <span className={styles.count}>{count} logged</span>}
      </header>

      {status === 'loading' && <p className={styles.muted}>Loading…</p>}
      {status === 'error' && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {status === 'ready' && logs.length === 0 && (
        <p className={styles.muted}>Nothing logged yet. Your first voice meal will appear here.</p>
      )}

      {status === 'ready' && logs.length > 0 && (
        <ul className={styles.list}>
          {logs.map((log) => (
            <li key={log.id} className={styles.row}>
              <div className={styles.rowMain}>
                <span className={`${styles.pill} ${styles[log.meal_type]}`}>{log.meal_type}</span>
                <time dateTime={log.logged_at} className={styles.time}>
                  {dateFormatter.format(new Date(log.logged_at))}
                </time>
                {log.source === 'voice' && (
                  <span className={styles.source} title="Logged by voice">
                    🎙
                  </span>
                )}
              </div>
              <p className={styles.items}>
                {log.items.map((item) => `${formatQuantity(item.quantity)} ${item.unit} ${item.name}`).join(' · ')}
              </p>
              <p className={styles.macros}>
                <strong>{Math.round(log.total_macros.calories)} kcal</strong> · P {log.total_macros.protein_g}g · C{' '}
                {log.total_macros.carbs_g}g · F {log.total_macros.fats_g}g
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

MealHistory.propTypes = {
  /** Bump to trigger a refetch (e.g. after a meal is saved). */
  refreshKey: PropTypes.number.isRequired,
};
