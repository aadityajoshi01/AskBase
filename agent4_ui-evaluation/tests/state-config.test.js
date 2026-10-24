import test from 'node:test';
import assert from 'node:assert/strict';
import appConfig from '../config/app.config.json' with { type: 'json' };
import theme from '../config/theme.tokens.json' with { type: 'json' };
import { applyThemeTokens, loadJsonDocument } from '../lib/config.js';
import { createStateStore } from '../lib/state-store.js';

test('state store ignores stale request responses', () => {
  const store = createStateStore();
  store.dispatch({ type: 'analysis/requested', requestId: 1 });
  store.dispatch({ type: 'analysis/requested', requestId: 2 });
  const before = store.getState();
  store.dispatch({ type: 'analysis/resolved', requestId: 1, result: { stale: true } });
  assert.equal(store.getState(), before);
  store.dispatch({ type: 'analysis/resolved', requestId: 2, result: { current: true } });
  assert.equal(store.getState().result.current, true);
  assert.equal(store.getState().status, 'ready');
});

test('state updates are immutable and notify subscribers', () => {
  const store = createStateStore();
  const before = store.getState();
  let notifications = 0;
  store.subscribe(() => { notifications += 1; });
  store.dispatch({ type: 'analysis/requested', requestId: 1 });
  assert.notEqual(store.getState(), before);
  assert.equal(before.status, 'idle');
  assert.equal(notifications, 1);
});

test('theme tokens map to CSS variables', () => {
  const values = new Map();
  const root = { style: { setProperty: (name, value) => values.set(name, value) } };
  applyThemeTokens(theme, root);
  assert.equal(values.get('--bg'), '#F4F1EC');
  assert.equal(values.get('--accent'), '#F26A1B');
  assert.ok(values.get('--font-sans').includes('Inter Tight'));
  assert.equal(values.get('--radius-pill'), '999px');
});

test('config documents load and HTTP failures are surfaced', async () => {
  const document = await loadJsonDocument('/config/app.config.json', async () => ({ ok: true, json: async () => appConfig }));
  assert.equal(document.features.liveBackend, false);
  assert.equal(document.limits.previewRows, 6);
  assert.equal(document.pipelineStages.length, 5);
  await assert.rejects(loadJsonDocument('/missing.json', async () => ({ ok: false, status: 404 })), /HTTP 404/);
});
