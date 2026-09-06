import { useCallback, useEffect, useRef, useState } from 'react';

export const RecorderState = Object.freeze({
  IDLE: 'idle',
  RECORDING: 'recording',
  PROCESSING: 'processing',
});

// Preference order: Opus in WebM (Chrome, Firefox, Edge) then MP4/AAC (Safari).
const PREFERRED_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
];

const TIMER_TICK_MS = 200;

function isRecordingSupported() {
  return (
    typeof navigator !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof MediaRecorder !== 'undefined'
  );
}

function pickMimeType() {
  return PREFERRED_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) ?? '';
}

/**
 * Translate a getUserMedia rejection into a message the user can act on.
 * @param {unknown} error
 */
function describePermissionError(error) {
  switch (error?.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'Microphone access was blocked. Allow the microphone in your browser settings and try again.';
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'No microphone was found on this device.';
    case 'NotReadableError':
      return 'The microphone is in use by another application.';
    default:
      return 'Could not start recording. Please check your microphone.';
  }
}

/**
 * Record microphone audio with the MediaRecorder API.
 *
 * State machine: `idle` → `recording` → `processing` → `idle`. The hook enters
 * `processing` when a recording finishes and stays there until the
 * `onRecordingComplete` handler's returned promise settles.
 *
 * @param {object} options
 * @param {(blob: Blob) => (void | Promise<void>)} options.onRecordingComplete
 *   Receives the finished clip. Not called for cancelled recordings.
 * @param {number} [options.maxDurationSec=60] Auto-stop after this many seconds.
 */
export function useVoiceRecorder({ onRecordingComplete, maxDurationSec = 60 }) {
  const [state, setState] = useState(RecorderState.IDLE);
  const [durationSec, setDurationSec] = useState(0);
  const [error, setError] = useState(null);

  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const timerRef = useRef(null);
  const discardRef = useRef(false);
  const mountedRef = useRef(true);
  const onCompleteRef = useRef(onRecordingComplete);
  onCompleteRef.current = onRecordingComplete;

  const isSupported = isRecordingSupported();

  const releaseResources = useCallback(() => {
    clearInterval(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
  }, []);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop();
    }
  }, []);

  const cancel = useCallback(() => {
    discardRef.current = true;
    stop();
  }, [stop]);

  const start = useCallback(async () => {
    if (!isSupported) {
      setError('This browser does not support audio recording.');
      return;
    }
    if (recorderRef.current) return; // already recording

    setError(null);
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (permissionError) {
      setError(describePermissionError(permissionError));
      return;
    }

    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunksRef.current = [];
    discardRef.current = false;

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };

    recorder.onstop = async () => {
      const discarded = discardRef.current;
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
      releaseResources();
      if (!mountedRef.current) return;

      setDurationSec(0);
      if (discarded || blob.size === 0) {
        setState(RecorderState.IDLE);
        return;
      }
      setState(RecorderState.PROCESSING);
      try {
        await onCompleteRef.current?.(blob);
      } finally {
        if (mountedRef.current) setState(RecorderState.IDLE);
      }
    };

    recorderRef.current = recorder;
    streamRef.current = stream;
    recorder.start(250);
    setState(RecorderState.RECORDING);

    const startedAt = Date.now();
    timerRef.current = setInterval(() => {
      const elapsed = (Date.now() - startedAt) / 1000;
      setDurationSec(elapsed);
      if (elapsed >= maxDurationSec) stop();
    }, TIMER_TICK_MS);
  }, [isSupported, maxDurationSec, releaseResources, stop]);

  // Release the microphone if the component unmounts mid-recording.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      discardRef.current = true;
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== 'inactive') recorder.stop();
      releaseResources();
    };
  }, [releaseResources]);

  return { state, durationSec, error, isSupported, start, stop, cancel };
}
