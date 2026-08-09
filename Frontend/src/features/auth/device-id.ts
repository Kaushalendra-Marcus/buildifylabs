/**
 * Guest `device_id` (specs/01 FR3). The backend reuses a guest user row for a
 * repeated device id — so the id must be **stable per browser**, generated once
 * and persisted, or every guest sign-in would create a fresh quota.
 */

const DEVICE_ID_KEY = 'buildifylabs.device_id';

function generateDeviceId(): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return crypto.randomUUID();
  }
  return `device-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function getOrCreateDeviceId(): string {
  try {
    const existing = localStorage.getItem(DEVICE_ID_KEY);
    if (existing) return existing;
    const id = generateDeviceId();
    localStorage.setItem(DEVICE_ID_KEY, id);
    return id;
  } catch {
    // Storage unavailable (e.g. privacy mode) — a fresh id per visit is the
    // acceptable degradation; the backend still handles it (specs/01 §5.3).
    return generateDeviceId();
  }
}
