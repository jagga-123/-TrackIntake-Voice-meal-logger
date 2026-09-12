import axios from 'axios';

import {
  clearSession,
  getAccessToken,
  getRefreshToken,
  saveSession,
  setAccessToken,
} from './tokenStorage.js';

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';

/** Dispatched on `window` when the session cannot be recovered. */
export const UNAUTHORIZED_EVENT = 'trackintake:unauthorized';

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30_000,
});

/**
 * The preview call runs speech-to-text and an LLM round-trip server-side. On a
 * small shared instance the first one also pays for waking the server and
 * loading the Whisper model, so it gets a far longer budget than other calls.
 */
const PREVIEW_TIMEOUT_MS = 300_000;

// --- Interceptors ------------------------------------------------------------

api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token && !config.skipAuth) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let refreshInFlight = null;

/**
 * Exchange the refresh token for a new access token, de-duplicating
 * concurrent refreshes so parallel 401s trigger a single request.
 * @returns {Promise<string>} the new access token
 */
function refreshAccessToken() {
  if (!refreshInFlight) {
    refreshInFlight = axios
      .post(`${API_BASE_URL}/auth/token/refresh/`, { refresh: getRefreshToken() })
      .then(({ data }) => {
        setAccessToken(data.access);
        return data.access;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

function endSession() {
  clearSession();
  window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
}

api.interceptors.response.use(undefined, async (error) => {
  const { config, response } = error;
  if (!config || config.skipAuth || response?.status !== 401) {
    return Promise.reject(error);
  }
  if (config.retried || !getRefreshToken()) {
    endSession();
    return Promise.reject(error);
  }
  try {
    const access = await refreshAccessToken();
    return api({ ...config, retried: true, headers: { ...config.headers, Authorization: `Bearer ${access}` } });
  } catch (refreshError) {
    endSession();
    return Promise.reject(refreshError);
  }
});

// --- Endpoints ---------------------------------------------------------------

/**
 * Obtain a JWT pair and persist it.
 * @param {string} username
 * @param {string} password
 */
export async function login(username, password) {
  const { data } = await api.post('/auth/token/', { username, password }, { skipAuth: true });
  saveSession(data);
  return data;
}

const EXTENSION_BY_MIME = {
  'audio/webm': 'webm',
  'audio/mp4': 'm4a',
  'audio/ogg': 'ogg',
  'audio/wav': 'wav',
  'audio/mpeg': 'mp3',
};

/**
 * Map a MediaRecorder MIME type (possibly with codecs) to a filename extension.
 * @param {string} mimeType
 */
function extensionFor(mimeType) {
  const base = mimeType.split(';')[0].trim();
  return EXTENSION_BY_MIME[base] ?? 'webm';
}

/**
 * Upload a recording and receive an editable, unsaved meal preview.
 * @param {Blob} audioBlob
 */
export async function previewVoiceMeal(audioBlob) {
  const form = new FormData();
  form.append('audio', audioBlob, `recording.${extensionFor(audioBlob.type)}`);
  const { data } = await api.post('/meal-log/voice/preview/', form, {
    timeout: PREVIEW_TIMEOUT_MS,
  });
  return data;
}

/**
 * Persist a (possibly edited) preview as a meal log.
 * @param {object} payload `{ meal_type, transcript, logged_at?, items: [...] }`
 */
export async function confirmMeal(payload) {
  const { data } = await api.post('/meal-log/voice/confirm/', payload);
  return data;
}

/**
 * Fetch the current user's meal logs, newest first.
 * @param {{ page?: number, pageSize?: number }} [options]
 */
export async function listMealLogs({ page = 1, pageSize = 10 } = {}) {
  const { data } = await api.get('/meal-log/', { params: { page, page_size: pageSize } });
  return data;
}

// --- Error helpers -----------------------------------------------------------

/**
 * Pull the first human-readable string out of a DRF validation `details` tree.
 * @param {unknown} details
 * @returns {string | null}
 */
function firstDetail(details) {
  if (typeof details === 'string') return details;
  if (Array.isArray(details)) return firstDetail(details[0]);
  if (details && typeof details === 'object') return firstDetail(Object.values(details)[0]);
  return null;
}

/**
 * Turn an axios error into a sentence suitable for the UI.
 * @param {unknown} error
 * @param {string} [fallback]
 */
export function getApiErrorMessage(error, fallback = 'Something went wrong. Please try again.') {
  // No response at all: a timeout, a dropped connection, or a proxy error whose
  // response carries no CORS headers. The browser cannot tell us which.
  if (!error?.response) {
    return error?.code === 'ECONNABORTED'
      ? 'The server took too long to answer. Try a shorter recording, then try again.'
      : 'Lost contact with the server. It may still be waking up, so please try again in a moment.';
  }
  const body = error.response.data?.error;
  if (!body) return fallback;
  return firstDetail(body.details) ?? body.message ?? fallback;
}
