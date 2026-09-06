import PropTypes from 'prop-types';

import { RecorderState } from '../../hooks/useVoiceRecorder.js';
import styles from './VoiceMealLogger.module.css';

/** Format seconds as `m:ss`. */
function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

function MicIcon() {
  return (
    <svg width="34" height="34" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" />
      <path
        d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width="30" height="30" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="6" y="6" width="12" height="12" rx="3" fill="currentColor" />
    </svg>
  );
}

const LABELS = {
  [RecorderState.IDLE]: 'Tap and describe what you ate',
  [RecorderState.RECORDING]: 'Listening… tap again when you are done',
  [RecorderState.PROCESSING]: 'Transcribing and analysing your meal…',
};

/**
 * Big microphone button with a pulsing ring while recording, a live timer and
 * a cancel action. Purely presentational; state comes from `useVoiceRecorder`.
 */
export default function VoiceRecorder({
  state,
  durationSec,
  maxDurationSec,
  error = null,
  isSupported,
  onStart,
  onStop,
  onCancel,
}) {
  const recording = state === RecorderState.RECORDING;
  const processing = state === RecorderState.PROCESSING;

  return (
    <div className={styles.recorder}>
      <div className={styles.micWrap}>
        {recording && <span className={styles.pulse} aria-hidden="true" />}
        <button
          type="button"
          className={`${styles.micButton} ${recording ? styles.micRecording : ''} ${
            processing ? styles.micProcessing : ''
          }`}
          onClick={recording ? onStop : onStart}
          disabled={processing || !isSupported}
          aria-label={recording ? 'Stop recording' : 'Start recording'}
          aria-pressed={recording}
        >
          {processing ? <span className={styles.spinner} /> : recording ? <StopIcon /> : <MicIcon />}
        </button>
      </div>

      <p className={styles.recorderLabel} aria-live="polite">
        {LABELS[state]}
      </p>

      {recording && (
        <>
          <p className={styles.timer}>
            {formatDuration(durationSec)}
            <span className={styles.timerMax}> / {formatDuration(maxDurationSec)}</span>
          </p>
          <button type="button" className={styles.linkButton} onClick={onCancel}>
            Cancel recording
          </button>
        </>
      )}

      {!isSupported && (
        <p className={styles.errorText} role="alert">
          This browser cannot record audio. Please use a recent Chrome, Edge, Firefox or Safari.
        </p>
      )}
      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}

      <p className={styles.hint}>
        Try: <em>“do roti aur ek katori dal”</em> or <em>“half plate rice with chicken curry”</em>
      </p>
    </div>
  );
}

VoiceRecorder.propTypes = {
  state: PropTypes.oneOf(Object.values(RecorderState)).isRequired,
  durationSec: PropTypes.number.isRequired,
  maxDurationSec: PropTypes.number.isRequired,
  error: PropTypes.string,
  isSupported: PropTypes.bool.isRequired,
  onStart: PropTypes.func.isRequired,
  onStop: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};
