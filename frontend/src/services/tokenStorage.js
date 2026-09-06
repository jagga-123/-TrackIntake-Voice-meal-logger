/**
 * Persistence for the JWT pair. Kept separate from the API client so both the
 * interceptors and the app shell can read session state without a cycle.
 */

const ACCESS_KEY = 'trackintake.access';
const REFRESH_KEY = 'trackintake.refresh';

/** @returns {string | null} */
export function getAccessToken() {
  return localStorage.getItem(ACCESS_KEY);
}

/** @returns {string | null} */
export function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY);
}

/**
 * Store both tokens returned by the token endpoint.
 * @param {{ access: string, refresh: string }} tokens
 */
export function saveSession({ access, refresh }) {
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

/** @param {string} access */
export function setAccessToken(access) {
  localStorage.setItem(ACCESS_KEY, access);
}

export function clearSession() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

/** @returns {boolean} true when a refresh token exists, i.e. a session may be resumable. */
export function hasSession() {
  return Boolean(getRefreshToken());
}
