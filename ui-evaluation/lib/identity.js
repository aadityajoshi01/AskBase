// The gateway decides what a caller may read; the browser only ever names itself.
// Nothing here grants a role — picking "admin" in the switcher changes the header the
// server looks up in its own policy file, and an unknown name falls back to the
// most restricted role.
const STORAGE_KEY = 'askbase.user';

const storage = () => {
  try {
    return globalThis.localStorage;
  } catch {
    return null;                    // Node test run: no storage, in-memory only
  }
};

let current = storage()?.getItem?.(STORAGE_KEY) || '';

export function activeUser() {
  return current;
}

export function setActiveUser(name) {
  const next = String(name || '').trim();
  if (next === current) return current;
  current = next;
  const store = storage();
  if (store) {
    if (next) store.setItem(STORAGE_KEY, next);
    else store.removeItem(STORAGE_KEY);
  }
  return current;
}

/** Headers every gateway call carries. Empty when the caller is anonymous. */
export function identityHeaders(extra = {}) {
  const headers = { ...extra };
  if (current) headers['X-AskBase-User'] = current;
  return headers;
}
