export function createEventBus() {
  const listeners = new Map();
  return {
    on(eventName, listener) {
      const eventListeners = listeners.get(eventName) || new Set();
      eventListeners.add(listener);
      listeners.set(eventName, eventListeners);
      return () => eventListeners.delete(listener);
    },
    emit(eventName, payload) {
      for (const listener of listeners.get(eventName) || []) listener(payload);
    },
    clear() {
      listeners.clear();
    },
  };
}
