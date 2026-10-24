export function createLogger(limit = 200) {
  let logs = [];
  return {
    log({ level = 'info', stage = 'app', requestId = null, durationMs = null, message = '' }) {
      const entry = { timestamp: new Date().toISOString(), level, stage, requestId, durationMs, message };
      logs = [...logs.slice(-(limit - 1)), entry];
      return entry;
    },
    getLogs: () => logs.map((log) => ({ ...log })),
    clear: () => { logs = []; },
  };
}
