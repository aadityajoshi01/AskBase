import scenarioDocument from './config/scenarios.json' with { type: 'json' };
import analysisResponseSchema from './schemas/analysis-response.schema.json' with { type: 'json' };
import { assertValid } from './lib/validate.js';
import { identityHeaders } from './lib/identity.js';

const DEFAULT_API_BASE = 'http://127.0.0.1:8010';
// optional chaining: this module also runs in the browser, where `process` is absent
const gatewayBase = () => globalThis.process?.env?.ASKBASE_API_BASE || DEFAULT_API_BASE;
const mockFallback = () => globalThis.process?.env?.ASKBASE_MOCK_FALLBACK === 'true';

const wait = (milliseconds, signal) => new Promise((resolve, reject) => {
  const timeout = setTimeout(resolve, milliseconds);
  signal?.addEventListener('abort', () => {
    clearTimeout(timeout);
    reject(new DOMException('Analysis cancelled', 'AbortError'));
  }, { once: true });
});

export const analysisScenarios = scenarioDocument.scenarios.map(({ id, label }) => ({ value: id, label }));

function mockResponse(scenario, delay, signal, profileContext = null) {
  const found = scenarioDocument.scenarios.find(({ id }) => id === (scenario || 'success')) || scenarioDocument.scenarios[0];
  const result = structuredClone(found.response);
  if (profileContext) {
    result.meta = {
      ...result.meta,
      mode: 'mock',
      aiContextReceived: true,
      tableCount: profileContext.tables?.length || 0,
      columnCount: profileContext.columns?.length || 0,
    };
    result.warnings = [
      'Mock analysis only: no live AI request, SQL execution, or verified result was performed.',
      ...(result.warnings || []),
    ];
    result.verification = {
      ...result.verification,
      passed: false,
      message: 'Profile context was received by the mock analysis boundary; live analysis is not connected.',
    };
  }
  assertValid(result, analysisResponseSchema, 'Analysis response');
  result.row_count = result.rowCount;
  return wait(delay, signal).then(() => result);
}

export async function uploadDatasetCsv(fileName, csv, options = {}) {
  if (!fileName) throw new Error('Choose a CSV file to ingest.');
  if (!csv || !csv.trim()) throw new Error('The selected CSV is empty.');

  const controller = new AbortController();
  const relay = () => controller.abort();
  options.signal?.addEventListener('abort', relay, { once: true });
  const apiBase = gatewayBase();
  try {
    const response = await fetch(`${apiBase}/api/datasets/upload`, {
      method: 'POST',
      headers: identityHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ fileName, csv }),
      signal: controller.signal,
    });
    const result = await response.json();
    // a rejected CSV is a 400 carrying the gateway's own reason, never a silent success
    if (!response.ok || result.status !== 'ok') {
      throw new Error(result.message || `The gateway rejected the CSV (HTTP ${response.status}).`);
    }
    return result;
  } catch (error) {
    if (error.name === 'AbortError') throw new DOMException('Ingest cancelled', 'AbortError');
    if (error.name === 'TypeError') {
      throw new Error(`AskBase gateway is unreachable at ${apiBase}. Start it with: python -m gateway.server (from the repository root).`);
    }
    throw error;
  } finally {
    options.signal?.removeEventListener?.('abort', relay);
  }
}

export async function fetchDatasetInsights(databaseId, options = {}) {
  if (!databaseId) throw new Error('Select a dataset first.');
  const controller = new AbortController();
  const relay = () => controller.abort();
  options.signal?.addEventListener?.('abort', relay, { once: true });
  try {
    const response = await fetch(`${gatewayBase()}/api/insights?database=${encodeURIComponent(databaseId)}`,
      { cache: 'no-store', headers: identityHeaders(), signal: controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || `HTTP ${response.status}`);
    return result;
  } finally {
    options.signal?.removeEventListener?.('abort', relay);
  }
}

export async function fetchDatasetSchema(databaseId, options = {}) {
  if (!databaseId) throw new Error('Select a dataset before loading its schema.');
  const controller = new AbortController();
  const relay = () => controller.abort();
  options.signal?.addEventListener?.('abort', relay, { once: true });
  try {
    const response = await fetch(`${gatewayBase()}/api/schema?database=${encodeURIComponent(databaseId)}`,
      { cache: 'no-store', headers: identityHeaders(), signal: controller.signal });
    const result = await response.json();
    if (!response.ok || result.status === 'error') throw new Error(result.message || `HTTP ${response.status}`);
    return result;
  } finally {
    options.signal?.removeEventListener?.('abort', relay);
  }
}

/** The server's own list of signable identities and role limits, for the role switcher. */
export async function fetchUserDirectory(options = {}) {
  const controller = new AbortController();
  const relay = () => controller.abort();
  options.signal?.addEventListener?.('abort', relay, { once: true });
  try {
    const response = await fetch(`${gatewayBase()}/api/users`,
      { cache: 'no-store', headers: identityHeaders(), signal: controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || `HTTP ${response.status}`);
    return result;
  } finally {
    options.signal?.removeEventListener?.('abort', relay);
  }
}

export async function runAnalysis(question, databaseConfig, options = {}) {
  if (!question.trim()) throw new Error('Enter a question before running analysis.');
  if (!databaseConfig.database) throw new Error('Select a database before running analysis.');

  // a browser-profiled CSV has no rows on the server; only the mock boundary can take it
  const profileContext = options.aiContext || options.dataset?.aiContext;
  if (options.mock || profileContext) return mockResponse(options.scenario, options.delay ?? 450, options.signal, profileContext);

  const controller = new AbortController();
  const relay = () => controller.abort();
  options.signal?.addEventListener('abort', relay, { once: true });
  const apiBase = gatewayBase();
  try {
    const response = await fetch(`${apiBase}/api/analyze`, {
      method: 'POST',
      headers: identityHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ question, databaseConfig }),
      signal: controller.signal,
    });
    const result = await response.json();
    // the gateway speaks the contract's snake_case; the UI schema wants rowCount
    if (typeof result.row_count === 'number' && result.rowCount === undefined) result.rowCount = result.row_count;
    assertValid(result, analysisResponseSchema, 'Analysis response');
    return result;
  } catch (error) {
    if (error.name === 'AbortError') throw new DOMException('Analysis cancelled', 'AbortError');
    if (error.name === 'ValidationError') throw error;
    if (mockFallback()) {
      const result = await mockResponse(options.scenario, options.delay ?? 0, options.signal);
      result.warnings = [...result.warnings, `Gateway at ${apiBase} unreachable — served the mock scenario instead.`];
      return result;
    }
    throw new Error(`AskBase gateway is unreachable at ${apiBase}. Start it with: python -m gateway.server (from the repository root).`);
  } finally {
    options.signal?.removeEventListener?.('abort', relay);
  }
}

/** One-click sales report: every section is validated, executed and verified server-side. */
export async function generateReport(databaseConfig, options = {}) {
  if (!databaseConfig?.database) throw new Error('Select a database before generating a report.');
  const controller = new AbortController();
  const relay = () => controller.abort();
  options.signal?.addEventListener?.('abort', relay, { once: true });
  const apiBase = gatewayBase();
  try {
    const response = await fetch(`${apiBase}/api/report`, {
      method: 'POST',
      headers: identityHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ databaseConfig }),
      signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok || !result.html) {
      throw new Error(result.message || `The gateway could not build a report (HTTP ${response.status}).`);
    }
    return result;
  } catch (error) {
    if (error.name === 'TypeError') {
      throw new Error(`AskBase gateway is unreachable at ${apiBase}. Start it with: python -m gateway.server (from the repository root).`);
    }
    throw error;
  } finally {
    options.signal?.removeEventListener?.('abort', relay);
  }
}
