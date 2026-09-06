import PropTypes from 'prop-types';
import { useCallback, useState } from 'react';

import { useVoiceRecorder } from '../../hooks/useVoiceRecorder.js';
import { confirmMeal, getApiErrorMessage, previewVoiceMeal } from '../../services/mealApi.js';
import MealPreviewCard, { MEAL_TYPES } from './MealPreviewCard.jsx';
import styles from './VoiceMealLogger.module.css';
import VoiceRecorder from './VoiceRecorder.jsx';

const Phase = Object.freeze({ RECORD: 'record', PREVIEW: 'preview', SAVED: 'saved' });
const MAX_DURATION_SEC = 60;

const mealTypeLabel = (value) => MEAL_TYPES.find((type) => type.value === value)?.label ?? value;

/**
 * Orchestrates the record → preview → confirm flow and owns the API calls.
 */
export default function VoiceMealLogger({ onSaved = undefined }) {
  const [phase, setPhase] = useState(Phase.RECORD);
  const [preview, setPreview] = useState(null);
  const [previewKey, setPreviewKey] = useState(0);
  const [savedLog, setSavedLog] = useState(null);
  const [saving, setSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  const analyseRecording = useCallback(async (blob) => {
    setErrorMessage(null);
    try {
      const data = await previewVoiceMeal(blob);
      setPreview(data);
      setPreviewKey((key) => key + 1); // remount the card so edits from a previous take are dropped
      setPhase(Phase.PREVIEW);
    } catch (requestError) {
      setErrorMessage(getApiErrorMessage(requestError, 'Could not analyse the recording.'));
    }
  }, []);

  const recorder = useVoiceRecorder({
    onRecordingComplete: analyseRecording,
    maxDurationSec: MAX_DURATION_SEC,
  });

  const handleConfirm = async (payload) => {
    setSaving(true);
    setErrorMessage(null);
    try {
      const log = await confirmMeal(payload);
      setSavedLog(log);
      setPhase(Phase.SAVED);
      onSaved?.(log);
    } catch (requestError) {
      setErrorMessage(getApiErrorMessage(requestError, 'Could not save the meal.'));
    } finally {
      setSaving(false);
    }
  };

  const startOver = () => {
    setPreview(null);
    setSavedLog(null);
    setErrorMessage(null);
    setPhase(Phase.RECORD);
  };

  if (phase === Phase.PREVIEW && preview) {
    return (
      <section className={styles.card}>
        <MealPreviewCard
          key={previewKey}
          preview={preview}
          onConfirm={handleConfirm}
          onRerecord={startOver}
          saving={saving}
          error={errorMessage}
        />
      </section>
    );
  }

  if (phase === Phase.SAVED && savedLog) {
    return (
      <section className={`${styles.card} ${styles.savedCard}`}>
        <span className={styles.savedIcon} aria-hidden="true">
          ✓
        </span>
        <h2 className={styles.cardTitle}>{mealTypeLabel(savedLog.meal_type)} logged</h2>
        <p className={styles.cardSubtitle}>
          {savedLog.items.length} item{savedLog.items.length === 1 ? '' : 's'} ·{' '}
          {Math.round(savedLog.total_macros.calories)} kcal · P {savedLog.total_macros.protein_g}g · C{' '}
          {savedLog.total_macros.carbs_g}g · F {savedLog.total_macros.fats_g}g
        </p>
        <button type="button" className={styles.primaryButton} onClick={startOver}>
          Log another meal
        </button>
      </section>
    );
  }

  return (
    <section className={styles.card}>
      <h2 className={styles.cardTitle}>Log a meal by voice</h2>
      <p className={styles.cardSubtitle}>
        Speak in Hindi, English or Hinglish. You will get to review everything before it is saved.
      </p>
      <VoiceRecorder
        state={recorder.state}
        durationSec={recorder.durationSec}
        maxDurationSec={MAX_DURATION_SEC}
        error={recorder.error ?? errorMessage}
        isSupported={recorder.isSupported}
        onStart={recorder.start}
        onStop={recorder.stop}
        onCancel={recorder.cancel}
      />
    </section>
  );
}

VoiceMealLogger.propTypes = {
  /** Called with the saved meal log after a successful confirm. */
  onSaved: PropTypes.func,
};
