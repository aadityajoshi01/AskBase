import appConfig from './config/app.config.json' with { type: 'json' };
import themeTokens from './config/theme.tokens.json' with { type: 'json' };
import exampleConfig from './config/examples.json' with { type: 'json' };
import sampleDatabase from './config/sample-db.schema.json' with { type: 'json' };
import evaluationFixture from './config/evaluation.fixtures.json' with { type: 'json' };
import apiContract from './docs/api-contract.json' with { type: 'json' };
import evaluationReportSchema from './schemas/evaluation-report.schema.json' with { type: 'json' };
import { analysisScenarios, runAnalysis, uploadDatasetCsv, fetchDatasetSchema, fetchDatasetInsights, fetchUserDirectory, generateReport } from './analysis-service.js';
import { activeUser, setActiveUser } from './lib/identity.js';
import { canAnalyzeDataset, createDatasetRequest, inspectDataset, profileFiles, recommendQuestions } from './dataset.js';
import { calculateMetrics, evaluationScenarios, filterScenarios } from './evaluation.js';
import { applyThemeTokens } from './lib/config.js';
import { createEventBus } from './lib/event-bus.js';
import { createLogger } from './lib/logger.js';
import { runPipeline } from './lib/pipeline.js';
import { createStateStore } from './lib/state-store.js';
import { assertValid } from './lib/validate.js';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character]));
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

applyThemeTokens(themeTokens);

const eventBus = createEventBus();
const logger = createLogger();
const store = createStateStore();
let activeController;
let requestCounter = 0;
let currentDataset = { kind: 'sample', fileName: 'Sample retail' };
let pendingCsvFile = null;
let uploadedFiles = [];
let profileResult = null;
let profiling = false;
let uploadKind = 'csv';
let currentPipeline = [];
let latestTimeline = [];
let lastResponse = null;
let lastError = null;
let evaluationStatus = 'all';
let developerTab = 'response';
let toastTimeout;

function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => toast.classList.remove('visible'), 2400);
}

function downloadJson(filename, value) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function syntaxHighlight(value) {
  const json = escapeHtml(JSON.stringify(value, null, 2));
  return json.replace(/("(?:\\.|[^"\\])*")(\s*:)?|\b(?:true|false)\b|\bnull\b|-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi, (match, string, colon) => {
    if (string) return `<span class="${colon ? 'json-key' : 'json-string'}">${string}</span>${colon || ''}`;
    const className = match === 'null' ? 'json-null' : ['true', 'false'].includes(match) ? 'json-boolean' : 'json-number';
    return `<span class="${className}">${match}</span>`;
  });
}

function showPage(pageName, scrollTarget) {
  $$('.page').forEach((page) => { page.hidden = page.id !== `${pageName}-page`; });
  $$('.nav-pill [data-page]').forEach((button) => {
    const active = button.dataset.page === pageName && !button.dataset.scroll;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  if (scrollTarget) requestAnimationFrame(() => document.getElementById(scrollTarget)?.scrollIntoView({ block: 'start' }));
  else window.scrollTo({ top: 0, behavior: 'instant' });
}

$$('[data-page]').forEach((button) => button.addEventListener('click', () => showPage(button.dataset.page, button.dataset.scroll)));
$$('[data-scroll]:not([data-page])').forEach((element) => element.addEventListener('click', (event) => {
  event.preventDefault();
  showPage('analyze', element.dataset.scroll);
}));

function renderSampleContext() {
  $('#sample-schema').innerHTML = sampleDatabase.tables.map((table, index) => `<details class="schema-table" ${index === 0 ? 'open' : ''}><summary>${icon('chevron')}${icon('table')}${escapeHtml(table.name)}<span>${table.rows.toLocaleString()} rows</span></summary><div class="schema-columns">${table.columns.map((column) => `<span>${escapeHtml(column.name)}<small>${column.type}</small></span>`).join('')}</div></details>`).join('');
  $('#quality-warnings').innerHTML = sampleDatabase.warnings.map((warning) => `<span class="quality-warning">${icon('warning')}${escapeHtml(warning)}</span>`).join('');
}

const FALLBACK_EXAMPLES = exampleConfig.groups.flatMap((group) => group.questions).slice(0, 5);
const schemaCache = new Map();
const insightsCache = new Map();
let examplesToken = 0;

function renderExamples(examples) {
  const chips = examples?.length ? examples : FALLBACK_EXAMPLES;
  $('#example-chips').innerHTML = chips.map((example) => `<button type="button" data-scenario="${example.scenario}" data-question="${escapeHtml(example.question)}">${escapeHtml(example.question)}</button>`).join('');
  $$('#example-chips button').forEach((button) => button.addEventListener('click', () => {
    $('#question').value = button.dataset.question;
    $('#scenario').value = button.dataset.scenario;
    $('#question').focus();
  }));
}

function renderSummary(summary) {
  const panel = $('#data-summary');
  if (!panel) return;
  panel.hidden = false;
  $('#data-summary-title').textContent = summary.label || 'Data summary';
  $('#data-summary-meta').textContent = summary.detail || '';
  $('#data-summary-text').textContent = summary.text;
}

// The gateway reads the real schema and lets the model phrase the questions, so a chip can
// never offer a column the selected database does not have.
async function cachedInsights(databaseId) {
  if (!insightsCache.has(databaseId)) {
    try {
      insightsCache.set(databaseId, await fetchDatasetInsights(databaseId));
    } catch (error) {
      insightsCache.set(databaseId, { status: 'error', questions: [], summary: '', context: '', message: error.message });
    }
  }
  return insightsCache.get(databaseId);
}

function questionsFor(insights, tables) {
  if (insights?.questions?.length) return insights.questions;
  return recommendQuestions(tables || [], 5);
}

function summaryFor(insights, dataset) {
  if (insights?.summary) {
    return { label: insights.label, detail: `${insights.table_count} tables · ${Number(insights.row_count).toLocaleString()} rows · read from the schema`, text: insights.summary };
  }
  const context = insights?.context || '';
  return {
    label: insights?.label || dataset?.fileName,
    detail: 'schema only — model unavailable',
    text: `${insights?.table_count || 0} table(s), ${Number(insights?.row_count || 0).toLocaleString()} row(s).\n${context}`,
  };
}

async function updateExamples(dataset) {
  const token = ++examplesToken;
  const show = (questions, summary) => {
    if (token !== examplesToken) return;
    renderExamples((questions || []).map((question) => ({ question, scenario: 'success' })));
    if (summary) renderSummary(summary);
  };
  const live = appConfig.features.liveBackend;
  const databaseId = dataset.kind === 'gateway' ? dataset.databaseId
    : dataset.kind === 'sample' && live && gatewayDatasets.has('sample_retail') ? 'sample_retail' : null;

  if (databaseId && live) {
    renderSummary({ label: dataset.fileName, detail: 'reading the schema…', text: 'Asking the model which questions this data can answer…' });
    const tables = await cachedTables(databaseId);
    const insights = await cachedInsights(databaseId);
    return show(questionsFor(insights, tables), summaryFor(insights, dataset));
  }

  const tables = dataset.kind === 'sample' ? sampleDatabase.tables
    : dataset.columns?.length ? [{ name: dataset.fileName, columns: dataset.columns }] : [];
  const local = dataset.columns?.length
    ? { label: dataset.fileName, detail: `${dataset.rowCount.toLocaleString()} rows · ${dataset.columns.length} columns · profiled in this browser`, text: `Columns: ${dataset.columns.map((column) => column.name).join(', ')}.` }
    : null;
  show(questionsFor(null, tables), local);
}

async function cachedTables(databaseId) {
  if (!schemaCache.has(databaseId)) {
    try {
      const schema = await fetchDatasetSchema(databaseId);
      schemaCache.set(databaseId, schema.tables || []);
    } catch {
      schemaCache.set(databaseId, []);
    }
  }
  return schemaCache.get(databaseId);
}

analysisScenarios.forEach((scenario) => {
  const option = document.createElement('option');
  option.value = scenario.value;
  option.textContent = scenario.label;
  $('#scenario').append(option);
});
renderSampleContext();
$('#app-version').textContent = `v${appConfig.version} · ${appConfig.buildMode}`;
$('#developer-version').textContent = `${appConfig.name} v${appConfig.version} · ${appConfig.buildMode}`;
$('#announcement-label').textContent = appConfig.features.liveBackend ? 'Live gateway · validated read-only analysis' : 'Demo build · mock analysis';
$('#build-pill-label').textContent = appConfig.features.liveBackend ? 'Live gateway' : 'Mock backend';

const GATEWAY_BASE = globalThis.process?.env?.ASKBASE_API_BASE || 'http://127.0.0.1:8010';
const gatewayDatasets = new Map();
updateExamples(currentDataset);

function markSampleAvailability(available) {
  const card = $('#sample-source');
  const status = card.querySelector('.source-status');
  status.innerHTML = available
    ? '<i></i>Read-only'
    : '<i></i>Needs gateway';
}

function renderGatewaySources() {
  $('#gateway-sources').innerHTML = [...gatewayDatasets.values()]
    .filter((entry) => entry.id !== 'sample_retail')
    .map((entry) => `<button class="source-card" type="button" data-gateway-id="${escapeHtml(entry.id)}"><span class="source-icon">${icon('database')}</span><span class="source-copy"><small>GATEWAY DATABASE</small><strong>${escapeHtml(entry.label)}</strong><span>${entry.available ? 'SQLite · registered' : 'SQLite · not built yet'}</span></span><span class="source-status"><i></i>Read-only</span><span class="source-check">${icon('check')}</span></button>`)
    .join('');
  $$('[data-gateway-id]').forEach((card) => card.addEventListener('click', () => selectGatewayDataset(card.dataset.gatewayId)));
}

function selectGatewayDataset(id) {
  const entry = gatewayDatasets.get(id);
  if (!entry) return;
  currentDataset = { kind: 'gateway', fileName: entry.label, databaseId: entry.id };
  pendingCsvFile = null;
  $$('.source-card').forEach((card) => card.classList.remove('active'));
  document.querySelector(`[data-gateway-id="${id}"]`)?.classList.add('active');
  $('#sample-explorer').hidden = true;
  $('#dataset-preview').hidden = true;
  $('#upload').classList.remove('active');
  $('#active-source').innerHTML = `${icon('database')}${escapeHtml(entry.label)}`;
  $('#upload-backend-note').hidden = true;
  $('#scenario').disabled = true;
  resetAnalysisView();
  setAnalyzing(false);
  updateExamples(currentDataset);
  showToast(`${entry.label} selected — the gateway will run questions against it.`);
}

async function loadGatewayDatasets() {
  if (!appConfig.features.liveBackend) {
    // mock demo mode: the bundled sample is served by scenario mocks only
    return;
  }
  try {
    const response = await fetch(`${GATEWAY_BASE}/api/databases`, { cache: 'no-store' });
    const body = await response.json();
    for (const entry of body.databases) gatewayDatasets.set(entry.id, entry);
    renderGatewaySources();
    markSampleAvailability(true);
    updateExamples(currentDataset);
  } catch {
    $('#gateway-sources').innerHTML = '';
    markSampleAvailability(false);
    showToast('AskBase gateway offline — start it with python -m gateway.server for live analysis.');
  }
}
loadGatewayDatasets();

// The gateway decides what an identity may read. This control only names the identity
// the browser signs in as, so the switcher can never grant anything the server refuses.
let identityDirectory = null;

async function loadIdentities() {
  if (!appConfig.features.liveBackend) return;
  try {
    identityDirectory = await fetchUserDirectory();
  } catch {
    return;                     // gateway offline: there is no honest role list to show
  }
  const users = identityDirectory.users || [];
  $('#user-select').innerHTML = ['<option value="">Anonymous</option>']
    .concat(users.map((user) => `<option value="${escapeHtml(user.user)}">${escapeHtml(user.label || user.user)} · ${escapeHtml(user.role)}</option>`))
    .join('');
  const stored = activeUser();
  $('#user-select').value = users.some((user) => user.user === stored) ? stored : '';
  setActiveUser($('#user-select').value);
  $('#identity-control').hidden = false;
}
loadIdentities();

$('#user-select').addEventListener('change', async (event) => {
  setActiveUser(event.target.value);
  insightsCache.clear();
  resetAnalysisView();
  await updateExamples(currentDataset);
  const you = identityDirectory?.users?.find((user) => user.user === event.target.value);
  showToast(you ? `Signed in as ${you.label || you.user} — role ${you.role}.`
                : 'Anonymous: the gateway serves the most restricted role.');
});

$('#use-command').addEventListener('click', () => {
  $('#question').value = $('#command-question').textContent.replace(/^\$\s*/, '');
  showPage('analyze', 'workspace');
  $('#question').focus();
  showToast('Example question added to the workspace.');
});

$('#hero-upload').addEventListener('click', () => $('#dataset-upload').click());

function formatValue(value, column = '') {
  if (typeof value !== 'number') return escapeHtml(value);
  if (/revenue|total|price/i.test(column)) return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value);
  return value.toLocaleString('en-US');
}

function formatBytes(bytes) {
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function delimiterName(delimiter) {
  return delimiter === '\t' ? 'Tab' : delimiter === ';' ? 'Semicolon' : 'Comma';
}

function resetAnalysisView() {
  activeController?.abort();
  requestCounter += 1;
  store.dispatch({ type: 'analysis/reset' });
  lastResponse = null;
  lastError = null;
  latestTimeline = [];
  currentPipeline = [];
  $('#pipeline-stepper').hidden = true;
  $('#analysis-result').innerHTML = `<section class="result-placeholder"><span>${icon('chart')}</span><h3>Your evidence stack starts here</h3><p>Ask a question to see the answer, verification, SQL, rows, and chart.</p></section>`;
}

function restoreSample() {
  uploadedFiles = [];
  profileResult = null;
  profiling = false;
  currentDataset = { kind: 'sample', fileName: 'Sample retail' };
  pendingCsvFile = null;
  $('#dataset-upload').value = '';
  $$('.source-card').forEach((card) => card.classList.remove('active'));
  $('#sample-source').classList.add('active');
  $('#upload').classList.remove('active');
  $('#sample-explorer').hidden = false;
  $('#dataset-preview').hidden = true;
  $('#uploaded-files').hidden = true;
  $('#profile-panel').hidden = true;
  $('#active-source').innerHTML = `${icon('database')}Sample retail`;
  $('#upload-backend-note').hidden = true;
  $('#scenario').disabled = false;
  $('#profile-data').disabled = true;
  $('#clear-files').disabled = true;
  $('#profile-status').hidden = true;
  resetAnalysisView();
  setAnalyzing(false);
  updateExamples(currentDataset);
}

function setNoUploadedDataset() {
  uploadedFiles = [];
  profileResult = null;
  currentDataset = null;
  $('#sample-source').classList.remove('active');
  $('#upload').classList.add('active');
  $('#sample-explorer').hidden = true;
  $('#dataset-preview').hidden = true;
  $('#uploaded-files').hidden = true;
  $('#profile-panel').hidden = true;
  $('#active-source').innerHTML = `${icon('upload')}No uploaded files`;
  $('#upload-backend-note').hidden = true;
  $('#scenario').disabled = true;
  $('#profile-data').disabled = true;
  $('#clear-files').disabled = true;
  $('#profile-status').hidden = true;
  resetAnalysisView();
  setAnalyzing(false);
}

function fileId(file) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function renderFileList() {
  const container = $('#uploaded-files');
  if (!uploadedFiles.length) {
    container.hidden = true;
    container.innerHTML = '';
    return;
  }
  container.hidden = false;
  container.innerHTML = `<div class="uploaded-files-heading"><div><span class="eyebrow">UPLOADED FILES</span><h3>${uploadedFiles.length} CSV file${uploadedFiles.length === 1 ? '' : 's'} ready</h3></div><span class="privacy-pill">${icon('shield')}Local processing</span></div><div class="uploaded-file-list">${uploadedFiles.map((entry) => `<article class="uploaded-file ${entry.error ? 'has-error' : ''}"><span class="uploaded-file-icon">${icon(entry.error ? 'warning' : 'file')}</span><div class="uploaded-file-copy"><strong>${escapeHtml(entry.file.name)}</strong><small>${formatBytes(entry.file.size)} · ${entry.error ? escapeHtml(entry.error) : 'Validated · ready to profile'}</small></div><button class="light-pill remove-file" type="button" data-remove-file="${escapeHtml(entry.id)}">Remove</button></article>`).join('')}</div>`;
}

function renderDatasetPreview(dataset) {
  $('#sample-source').classList.remove('active');
  $('#upload').classList.add('active');
  $('#sample-explorer').hidden = true;
  $('#active-source').innerHTML = `${icon('file')}${escapeHtml(dataset.fileName)}`;
  $('#upload-backend-note').hidden = dataset.kind === 'sqlite';
  $('#scenario').disabled = dataset.kind === 'sqlite' || appConfig.features.liveBackend;
  const canIngest = dataset.kind === 'csv' && appConfig.features.liveBackend && Boolean(pendingCsvFile);
  const chips = [dataset.fileName, formatBytes(dataset.size), dataset.kind === 'csv' ? `${dataset.rowCount.toLocaleString()} rows` : 'Schema pending', dataset.kind === 'csv' ? `${dataset.columns.length} columns` : 'SQLite', dataset.delimiter ? delimiterName(dataset.delimiter) : null].filter(Boolean);
  const schema = dataset.kind === 'csv' ? `<div class="table-scroll"><table class="dataset-schema-table"><thead><tr><th>Column</th><th>Inferred type</th><th>Null count</th></tr></thead><tbody>${dataset.columns.map((column) => `<tr><td>${escapeHtml(column.name)}</td><td><span class="type-chip">${escapeHtml(column.logicalType || column.type)}</span></td><td>${column.nullCount}</td></tr>`).join('')}</tbody></table></div>` : '';
  const preview = dataset.kind === 'csv' ? `<h3 class="preview-title">Data preview · first ${dataset.rows.length} rows</h3><div class="table-scroll"><table class="result-table"><thead><tr>${dataset.columns.map((column) => `<th>${escapeHtml(column.name)}</th>`).join('')}</tr></thead><tbody>${dataset.rows.map((row) => `<tr>${row.map((value) => `<td>${escapeHtml(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '';
  const actions = `<div class="dataset-actions">${canIngest ? '<button class="light-pill accent" type="button" data-ingest-csv>Ingest into gateway</button>' : ''}<button class="light-pill" type="button" data-remove-dataset>Remove dataset</button></div>`;
  const note = dataset.kind !== 'csv'
    ? `${icon('warning')}A local SQLite file is previewed as metadata only — register it with the gateway to query it.`
    : canIngest
      ? `${icon('warning')}Nothing leaves the browser until you choose <strong>Ingest into gateway</strong>. It becomes a registered, read-only SQLite dataset the pipeline can query.`
      : appConfig.features.liveBackend
        ? `${icon('warning')}Upload a single CSV to ingest it into the gateway — its rows are only in the browser.`
        : `${icon('warning')}Profile this CSV to analyze it with the mock backend — its rows stay in the browser.`;
  $('#dataset-preview').innerHTML = `<div class="dataset-header"><div class="dataset-file"><span>${icon('file')}</span><div><strong>${escapeHtml(dataset.fileName)}</strong><small>${dataset.kind === 'csv' ? 'CSV parsed locally · nothing uploaded' : 'SQLite metadata only · nothing uploaded'}</small></div></div>${actions}</div><div class="dataset-chips">${chips.map((chip) => `<span class="dataset-chip">${escapeHtml(chip)}</span>`).join('')}</div>${schema}${preview}<p class="upload-backend-note">${note}</p>`;
  $('#dataset-preview').hidden = false;
  resetAnalysisView();
  setAnalyzing(false);
  updateExamples(dataset);
}

async function ingestPendingCsv(button) {
  if (!pendingCsvFile) return;
  button.disabled = true;
  button.textContent = 'Ingesting…';
  const reset = () => { button.disabled = false; button.textContent = 'Ingest into gateway'; };
  try {
    const body = await uploadDatasetCsv(pendingCsvFile.name, await pendingCsvFile.text());
    gatewayDatasets.set(body.database.id, { ...body.database, type: 'sqlite', available: true });
    renderGatewaySources();
    selectGatewayDataset(body.database.id);
    showToast(`${body.database.label} is registered · ${body.database.row_count} rows ready to query.`);
  } catch (error) {
    reset();
    $('#upload-error').textContent = error.message;
    $('#upload-error').hidden = false;
    showToast('The gateway could not ingest this CSV.');
  }
}

function formatStatistics(statistics) {
  if (!statistics || !Object.keys(statistics).length) return '—';
  return Object.entries(statistics)
    .filter(([, value]) => value !== null)
    .map(([key, value]) => `${key}: ${typeof value === 'number' ? Number(value.toFixed(2)).toLocaleString() : value}`)
    .join(' · ') || '—';
}

function renderProfile(profile) {
  const panel = $('#profile-panel');
  const summary = profile.summary;
  const statusLabel = profile.status === 'success' ? 'Profile complete' : profile.status === 'warning' ? 'Profile complete with warnings' : 'Profile failed';
  const statusIcon = profile.status === 'success' ? 'check' : profile.status === 'warning' ? 'warning' : 'close';
  const errors = profile.errors?.length ? `<div class="profile-errors">${profile.errors.map((error) => `<p>${icon('warning')}${escapeHtml(error.fileName ? `${error.fileName}: ${error.message}` : error.message)}</p>`).join('')}</div>` : '';
  const fileSections = profile.files.map((file) => `<details class="profile-file" ${profile.files.length === 1 ? 'open' : ''}><summary><span>${icon(file.status === 'error' ? 'warning' : 'file')}<strong>${escapeHtml(file.name)}</strong></span><span>${file.rowCount.toLocaleString()} rows · ${file.columnCount} columns · ${file.status}</span></summary><div class="profile-file-body"><div class="dataset-chips"><span class="dataset-chip">${formatBytes(file.sizeBytes)}</span><span class="dataset-chip">${escapeHtml(file.delimiterName || 'Unknown delimiter')}</span><span class="dataset-chip">${file.duplicateRowCount} duplicate rows</span><span class="dataset-chip">${file.warnings.length} warnings</span></div>${file.errors?.length ? `<div class="profile-errors">${file.errors.map((error) => `<p>${icon('warning')}${escapeHtml(error.message)}</p>`).join('')}</div>` : ''}<div class="table-scroll"><table class="dataset-schema-table profile-table"><thead><tr><th>Column</th><th>Type</th><th>Nulls</th><th>Distinct</th><th>Invalid</th><th>Statistics</th><th>Warnings</th></tr></thead><tbody>${file.columns.map((column) => `<tr><td><strong>${escapeHtml(column.name)}</strong>${column.potentialIdentifier ? '<small>Potential identifier</small>' : ''}${column.potentialSensitiveData ? '<small>Potential sensitive data</small>' : ''}</td><td><span class="type-chip">${escapeHtml(column.logicalType)}</span></td><td>${column.nullCount} (${column.nullPercentage.toFixed(1)}%)</td><td>${column.distinctCount}</td><td>${column.invalidCount}</td><td>${escapeHtml(formatStatistics(column.statistics))}</td><td>${column.warnings.length ? column.warnings.map((item) => `<span class="profile-warning">${escapeHtml(item.message)}</span>`).join('') : '—'}</td></tr>`).join('')}</tbody></table></div>${file.warnings.length ? `<div class="warning-list">${file.warnings.map((item) => `<span class="warning-item">${icon('warning')}${escapeHtml(item.message)}</span>`).join('')}</div>` : ''}</div></details>`).join('');
  panel.innerHTML = `<div class="profile-header"><div><span class="eyebrow">DATA PROFILE</span><h2>${icon(statusIcon)}${statusLabel}</h2><p>Real values calculated locally. Source files are not uploaded or sent to the analysis provider.</p></div><div class="profile-actions"><span class="profile-status-badge ${profile.status}">${escapeHtml(profile.status)}</span>${profile.status === 'error' ? '<button class="light-pill" type="button" data-retry-profile>Retry profiling</button>' : ''}</div></div><div class="profile-metrics"><article><strong>${summary.fileCount}</strong><span>Files</span></article><article><strong>${summary.totalRows.toLocaleString()}</strong><span>Total rows</span></article><article><strong>${summary.totalColumns}</strong><span>Total columns</span></article><article><strong>${summary.warningCount}</strong><span>Warnings</span></article><article><strong>${summary.errorCount}</strong><span>Errors</span></article></div>${errors}<div class="profile-files">${fileSections}</div><div class="ai-context-card"><div><strong>${icon('shield')}AI context ready</strong><p>Only schema metadata, aggregates, warnings, and up to three non-sensitive sample values per column are available to analysis. Full CSV text is excluded.</p></div><span id="ai-context-status">${profile.aiContext ? 'Sanitized context built' : 'Unavailable'}</span></div>`;
  panel.hidden = false;
}

async function handleDatasetFiles(fileList) {
  const files = [...fileList].filter(Boolean);
  if (!files.length) return;
  if (uploadKind === 'sqlite') {
    try {
      const dataset = await inspectDataset(files[0], appConfig.limits);
      uploadedFiles = [];
      profileResult = null;
      currentDataset = dataset;
      $('#profile-data').disabled = true;
      $('#clear-files').disabled = true;
      renderDatasetPreview(dataset);
      showToast('SQLite metadata selected. CSV profiling is available in CSV mode.');
    } catch (error) {
      $('#upload-error').textContent = error.message;
      $('#upload-error').hidden = false;
    }
    return;
  }
  $('#upload-error').hidden = true;
  $('#profile-status').hidden = true;
  profileResult = null;
  const nextEntries = [];
  for (const file of files) {
    try {
      const preview = await inspectDataset(file, appConfig.limits);
      nextEntries.push({ id: fileId(file), file, preview, error: null });
    } catch (error) {
      nextEntries.push({ id: fileId(file), file, preview: null, error: error.message });
    }
  }
  const existing = new Set(uploadedFiles.map((entry) => entry.id));
  uploadedFiles = [...uploadedFiles, ...nextEntries.filter((entry) => !existing.has(entry.id))];
  currentDataset = { kind: 'csv', fileName: uploadedFiles.length === 1 ? uploadedFiles[0].file.name : `${uploadedFiles.length} CSV files`, files: uploadedFiles.filter((entry) => !entry.error).map((entry) => entry.file) };
  $('#sample-source').classList.remove('active');
  $('#upload').classList.add('active');
  $('#sample-explorer').hidden = true;
  $('#scenario').disabled = appConfig.features.liveBackend;
  $('#profile-data').disabled = !uploadedFiles.length || uploadedFiles.some((entry) => entry.error) || profiling;
  $('#clear-files').disabled = !uploadedFiles.length || profiling;
  $('#active-source').innerHTML = `${icon('file')}${escapeHtml(currentDataset.fileName)}`;
  $('#upload-backend-note').hidden = false;
  renderFileList();
  const firstPreview = uploadedFiles.find((entry) => entry.preview)?.preview;
  pendingCsvFile = appConfig.features.liveBackend && uploadedFiles.length === 1 && firstPreview ? uploadedFiles[0].file : null;
  if (firstPreview && uploadedFiles.length === 1) renderDatasetPreview(firstPreview);
  else $('#dataset-preview').hidden = true;
  resetAnalysisView();
  setAnalyzing(false);
  const errors = uploadedFiles.filter((entry) => entry.error);
  if (errors.length) {
    $('#upload-error').textContent = `${errors.length} file${errors.length === 1 ? '' : 's'} need attention before profiling.`;
    $('#upload-error').hidden = false;
  } else showToast(`${uploadedFiles.length} CSV file${uploadedFiles.length === 1 ? '' : 's'} ready to profile.`);
}

async function profileUploadedData() {
  if (!uploadedFiles.length || uploadedFiles.some((entry) => entry.error) || profiling) return;
  profiling = true;
  $('#profile-data').disabled = true;
  $('#clear-files').disabled = true;
  $('#profile-data').innerHTML = `${icon('clock')}<span>Profiling…</span>`;
  $('#profile-status').textContent = 'Profiling uploaded data locally…';
  $('#profile-status').hidden = false;
  try {
    profileResult = await profileFiles(uploadedFiles.map((entry) => entry.file), appConfig.limits);
    currentDataset = { ...currentDataset, kind: 'csv', datasetId: profileResult.datasetId, files: uploadedFiles.map((entry) => entry.file), profile: profileResult };
    renderProfile(profileResult);
    $('#profile-status').textContent = profileResult.status === 'error' ? 'Profiling failed. Review the errors and retry.' : `Profiling finished with ${profileResult.summary.warningCount} warning${profileResult.summary.warningCount === 1 ? '' : 's'}.`;
    $('#profile-status').className = `profile-status ${profileResult.status}`;
    if (profileResult.status !== 'error') showToast('Profile ready. You can now ask a question.');
  } catch (error) {
    profileResult = null;
    renderProfile({ status: 'error', files: [], summary: { fileCount: uploadedFiles.length, totalRows: 0, totalColumns: 0, warningCount: 0, errorCount: 1 }, errors: [{ message: error.message }], aiContext: null });
    $('#profile-status').textContent = `Profiling failed: ${error.message}`;
    $('#profile-status').className = 'profile-status error';
  } finally {
    profiling = false;
    $('#profile-data').disabled = !uploadedFiles.length || uploadedFiles.some((entry) => entry.error);
    $('#profile-data').innerHTML = `${icon('flask')}<span>${profileResult?.status === 'error' ? 'Retry profiling' : 'Profile data'}</span>`;
    $('#clear-files').disabled = !uploadedFiles.length;
    setAnalyzing(false);
  }
}

$('#sample-source').addEventListener('click', restoreSample);
$('#profile-data').addEventListener('click', profileUploadedData);
$('#clear-files').addEventListener('click', () => {
  setNoUploadedDataset();
  $('#dataset-upload').value = '';
  showToast('All uploaded files cleared.');
});
$('#uploaded-files').addEventListener('click', (event) => {
  const button = event.target.closest('[data-remove-file]');
  if (!button || profiling) return;
  uploadedFiles = uploadedFiles.filter((entry) => entry.id !== button.dataset.removeFile);
  if (!uploadedFiles.length) {
    setNoUploadedDataset();
    return;
  }
  profileResult = null;
  currentDataset = { kind: 'csv', fileName: uploadedFiles.length === 1 ? uploadedFiles[0].file.name : `${uploadedFiles.length} CSV files`, files: uploadedFiles.filter((entry) => !entry.error).map((entry) => entry.file) };
  renderFileList();
  const firstPreview = uploadedFiles.find((entry) => entry.preview)?.preview;
  pendingCsvFile = appConfig.features.liveBackend && uploadedFiles.length === 1 && firstPreview ? uploadedFiles[0].file : null;
  if (firstPreview && uploadedFiles.length === 1) renderDatasetPreview(firstPreview);
  else $('#dataset-preview').hidden = true;
  $('#profile-panel').hidden = true;
  $('#profile-data').disabled = uploadedFiles.some((entry) => entry.error);
  $('#clear-files').disabled = false;
  resetAnalysisView();
  setAnalyzing(false);
  showToast('File removed. Profile the remaining files when ready.');
});
$('#profile-panel').addEventListener('click', (event) => {
  if (event.target.closest('[data-retry-profile]')) profileUploadedData();
});
$('#dataset-preview').addEventListener('click', (event) => {
  if (event.target.closest('[data-remove-dataset]')) setNoUploadedDataset();
  const ingest = event.target.closest('[data-ingest-csv]');
  if (ingest) ingestPendingCsv(ingest);
});
$('#dropzone').addEventListener('click', () => $('#dataset-upload').click());
$('#dropzone').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    $('#dataset-upload').click();
  }
});
$('#dataset-upload').addEventListener('change', (event) => handleDatasetFiles(event.target.files));
for (const eventName of ['dragenter', 'dragover']) $('#dropzone').addEventListener(eventName, (event) => {
  event.preventDefault();
  $('#upload').classList.add('drag-over');
});
for (const eventName of ['dragleave', 'drop']) $('#dropzone').addEventListener(eventName, (event) => {
  event.preventDefault();
  $('#upload').classList.remove('drag-over');
});
$('#dropzone').addEventListener('drop', (event) => handleDatasetFiles(event.dataTransfer.files));
$('#sqlite-toggle').addEventListener('click', () => {
  uploadKind = uploadKind === 'csv' ? 'sqlite' : 'csv';
  $('#dataset-upload').accept = uploadKind === 'csv' ? '.csv,text/csv' : '.db,.sqlite';
  $('#dataset-upload').multiple = uploadKind === 'csv';
  $('#dataset-upload').value = '';
  $('#dropzone-title').innerHTML = uploadKind === 'csv' ? 'Drag & drop your .csv files here <span>or browse</span>' : 'Choose your .db or .sqlite file <span>or browse</span>';
  $('#sqlite-toggle').textContent = uploadKind === 'csv' ? 'Use a SQLite file instead' : 'Use a CSV file instead';
  $('#dropzone').setAttribute('aria-label', uploadKind === 'csv' ? 'Upload a CSV dataset' : 'Select a SQLite database');
  if (uploadKind === 'sqlite') showToast('SQLite metadata selection remains read-only; CSV profiling is the active workflow.');
});

function renderPipeline() {
  $('#pipeline-stepper').innerHTML = currentPipeline.map((stage, index) => `<li class="pipeline-stage ${stage.status}"><div><span class="stage-icon">${stage.status === 'complete' ? icon('check') : stage.status === 'running' ? icon('clock') : index + 1}</span><strong>${escapeHtml(stage.label)}</strong></div><small>${stage.durationMs === undefined ? stage.status : `${stage.durationMs} ms`}</small></li>`).join('');
}

for (const eventName of ['stage:start', 'stage:complete', 'stage:error']) eventBus.on(eventName, (event) => {
  if (event.requestId !== store.getState().requestId) return;
  currentPipeline = currentPipeline.map((stage) => stage.id === event.stage.id ? { ...stage, status: eventName === 'stage:start' ? 'running' : eventName === 'stage:complete' ? 'complete' : 'error', durationMs: event.durationMs } : stage);
  renderPipeline();
  if (!$('#developer-drawer').hidden) renderDeveloper();
});
eventBus.on('pipeline:complete', ({ requestId, timeline }) => {
  if (requestId === store.getState().requestId) latestTimeline = timeline;
});
eventBus.on('result:ready', ({ requestId, result }) => {
  if (requestId === store.getState().requestId) renderResult(result);
});

function renderSql(result) {
  if (!result.sql) return '';
  return `<details class="result-card sql-details"><summary>${icon('code')}Generated SQL<button class="copy-button" data-copy-sql type="button">${icon('copy')}Copy SQL</button></summary><pre class="sql-code">${escapeHtml(result.sql)}</pre></details>`;
}

function renderTable(result) {
  if (!result.rows.length) return `<section class="result-card"><div class="result-card-body"><div class="empty-result">${icon('inbox')}<span>The query completed successfully. No rows matched the selected criteria.</span></div></div></section>`;
  const rows = result.rows.slice(0, appConfig.limits.maxResultRows);
  const numericColumns = result.columns.map((_, index) => typeof rows[0]?.[index] === 'number');
  return `<section class="result-card"><div class="result-card-header"><div class="result-card-title"><span>${icon('table')}</span><div><h3>Result table</h3><small>${appConfig.features.liveBackend ? 'Actual rows returned by the read-only gateway' : 'Actual rows returned by the mock executor'}</small></div></div><div class="result-meta"><span>${result.rowCount} ${result.rowCount === 1 ? 'row' : 'rows'}</span><span>${result.meta.durationMs ?? '—'} ms</span>${result.truncated ? '<span>Truncated</span>' : ''}</div></div><div class="table-scroll"><table class="result-table"><thead><tr>${result.columns.map((column, index) => `<th class="${numericColumns[index] ? 'numeric' : ''}">${escapeHtml(column)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((value, index) => `<td class="${numericColumns[index] ? 'numeric' : ''}">${formatValue(value, result.columns[index])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`;
}

function renderLineChart(values, labels, series) {
  const width = 560;
  const height = 150;
  const pad = 10;
  const minimum = Math.min(...values, 0);
  const span = Math.max(...values) - minimum || 1;
  const x = (index) => pad + (values.length < 2 ? (width - 2 * pad) / 2 : index * (width - 2 * pad) / (values.length - 1));
  const y = (value) => height - pad - ((value - minimum) / span) * (height - 2 * pad);
  const points = values.map((value, index) => `${x(index).toFixed(1)},${y(value).toFixed(1)}`);
  const dots = values.map((value, index) => `<circle cx="${x(index).toFixed(1)}" cy="${y(value).toFixed(1)}" r="3.5"><title>${escapeHtml(labels[index])} · ${escapeHtml(value)}</title></circle>`).join('');
  const axis = `<div class="line-axis"><span>${escapeHtml(labels[0])}</span><span>${escapeHtml(labels[labels.length - 1])}</span></div>`;
  return `<div class="chart line-chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(series)}"><polyline points="${points.join(' ')}"/>${dots}</svg>${axis}</div>`;
}

function renderChart(result) {
  if (!result.chart || !result.rows.length) return '';
  // With a live gateway the PNG is rendered server-side by matplotlib from the
  // exact permission-filtered rows the analysis cached under its ticket; the
  // client never re-sends row data. Without a ticket (mock backend) the DOM
  // bars below are the fallback.
  if (result.chartTicket && appConfig.features.liveBackend) {
    const types = ['bar', 'line', 'pie', 'scatter', 'histogram', 'box'];
    const base = `${GATEWAY_BASE}/api/chart?ticket=${encodeURIComponent(result.chartTicket)}&x=${encodeURIComponent(result.chart.x)}&y=${encodeURIComponent(result.chart.y)}`;
    globalThis.__askbaseChartBase = base;
    const picker = types.map((type) => `<button type="button" class="chart-type-btn${type === result.chart.type ? ' active' : ''}" data-type="${type}" onclick="setChartType('${type}')">${type}</button>`).join('');
    return `<section class="result-card"><div class="result-card-header"><div class="result-card-title"><span>${icon('chart')}</span><div><h3>Chart</h3><small>Rendered by the gateway from returned rows only</small></div></div><span class="chart-type-pill">${escapeHtml(result.chart.type)}</span></div><div class="result-card-body"><img id="chart-image" class="chart-png" src="${base}&type=${encodeURIComponent(result.chart.type)}" alt="Chart of ${escapeHtml(result.chart.y)} by ${escapeHtml(result.chart.x)}"><div class="chart-type-picker">${picker}</div><p class="chart-caption">No additional values are introduced for visualization.</p></div></section>`;
  }
  if (!['bar', 'line'].includes(result.chart.type)) return '';
  const values = result.rows.map((row) => Number(row[1]));
  const maximum = Math.max(...values, 1);
  const series = `${result.chart.y} by ${result.chart.x}`;
  const isLine = result.chart.type === 'line';
  const chart = isLine
    ? renderLineChart(values, result.rows.map((row) => String(row[0])), series)
    : `<div class="chart">${result.rows.map((row, index) => `<div class="chart-row"><span class="chart-label" title="${escapeHtml(row[0])}">${escapeHtml(row[0])}</span><div class="bar-track"><div class="bar-fill" style="--bar:${Math.max(2, values[index] / maximum * 100)}%"></div></div><span class="chart-value">${formatValue(row[1], result.columns[1])}</span></div>`).join('')}</div>`;
  return `<section class="result-card"><div class="result-card-header"><div class="result-card-title"><span>${icon('chart')}</span><div><h3>${escapeHtml(series)}</h3><small>Generated from returned rows only</small></div></div><span class="chart-type-pill">${isLine ? 'Time series' : 'Categories'}</span></div><div class="result-card-body">${chart}<p class="chart-caption">No additional values are introduced for visualization.</p></div></section>`;
}

globalThis.setChartType = (type) => {
  const image = document.getElementById('chart-image');
  if (!image || !globalThis.__askbaseChartBase) return;
  image.src = `${globalThis.__askbaseChartBase}&type=${encodeURIComponent(type)}`;
  document.querySelectorAll('.chart-type-btn').forEach((button) => {
    button.classList.toggle('active', button.dataset.type === type);
  });
};

function renderResult(result) {
  const isError = result.status === 'error';
  const isClarification = result.status === 'clarification_needed';
  const isMockProfile = result.meta?.mode === 'mock' && result.meta?.aiContextReceived;
  const code = result.meta.code;
  // Agent 3's verifier reports scope: envelope_consistency is structure only,
  // so the UI must not present it as a factually verified answer.
  const structureOnly = result.verification?.scope === 'envelope_consistency';
  const title = isClarification ? 'Clarification needed' : code === 'unsafe_sql' ? 'Unsafe SQL blocked' : code === 'model_offline' ? 'Model unavailable' : isError ? 'Analysis failed' : isMockProfile ? 'Mock analysis preview' : result.rowCount === 0 ? 'Query completed · zero rows' : structureOnly ? 'Answer · structure-checked' : 'Verified answer';
  const subtitle = isClarification ? 'No SQL generated or executed' : isError ? result.verification.message : isMockProfile ? 'Profile context received · live AI unavailable' : structureOnly ? 'Execution and result-envelope checks passed; the numbers were not independently fact-checked' : 'Evidence-backed response';
  const state = isError ? 'error' : isClarification ? 'clarification' : 'success';
  const badge = isClarification ? 'Question required' : isError ? 'Not verified' : isMockProfile ? 'Mock only' : `${icon('shield')}${structureOnly ? 'Structure-checked' : 'Verified'}`;
  const checks = result.verification.checks?.length ? `<div class="verification-checks">${result.verification.checks.map((check) => `<span class="verification-check">${icon('check')}${escapeHtml(check)}</span>`).join('')}</div>` : '';
  const warnings = result.warnings.length ? `<div class="warning-list">${result.warnings.map((warning) => `<span class="warning-item">${icon('warning')}${escapeHtml(warning)}</span>`).join('')}</div>` : '';
  const sourceLabel = isMockProfile ? 'Mock profile-context provider' : appConfig.features.liveBackend ? 'Live gateway' : 'Mock backend';
  $('#analysis-result').innerHTML = `<div class="result-stack"><section class="result-card ${state}"><div class="result-card-header"><div class="result-card-title"><span>${icon(isClarification ? 'help' : isError || isMockProfile ? 'warning' : 'check')}</span><div><h3>${title}</h3><small>${escapeHtml(subtitle)}</small></div></div><span class="verified-pill">${badge}</span></div><div class="result-card-body"><p class="answer-text">${escapeHtml(result.answer)}</p></div></section><section class="result-card ${state}"><div class="result-card-header"><div class="result-card-title"><span>${icon(isError || isClarification || isMockProfile ? 'warning' : 'shield')}</span><div><h3>Verification & limitations</h3><small>Status is separate from visual styling</small></div></div><span class="mock-result-label">${sourceLabel}</span></div><div class="result-card-body"><p class="verification-message">${escapeHtml(result.verification.message)}</p>${checks}${warnings}</div></section>${renderSql(result)}${result.status === 'success' ? renderTable(result) : ''}${result.status === 'success' ? renderChart(result) : ''}</div>`;
}

function renderResponseError(error) {
  lastError = { name: error.name, message: error.message, details: error.details || [] };
  const malformed = error.name === 'ValidationError';
  $('#analysis-result').innerHTML = `<section class="result-card ${malformed ? 'malformed' : 'error'}"><div class="result-card-header"><div class="result-card-title"><span>${icon('warning')}</span><div><h3>${malformed ? 'Malformed response' : 'Analysis failed'}</h3><small>Never presented as verified success</small></div></div><span class="verified-pill">Error</span></div><div class="result-card-body"><p class="answer-text">${malformed ? 'The backend response did not match the shared contract, so it was not rendered as an answer.' : 'The analysis could not be completed. No result values are shown.'}</p><pre class="error-details">${escapeHtml(error.message)}</pre></div></section>`;
  if (!$('#developer-drawer').hidden) renderDeveloper();
}

function setAnalyzing(analyzing) {
  const button = $('#analyze-button');
  button.disabled = analyzing || !currentDataset || !canAnalyzeDataset(currentDataset, appConfig.features.liveBackend, profileResult);
  button.innerHTML = analyzing ? `${icon('clock')}<span>Analyzing…</span>` : `${icon('spark')}<span>Analyze question</span>${icon('arrow')}`;
}

$('#report-button')?.addEventListener('click', async () => {
  const button = $('#report-button');
  if (!canAnalyzeDataset(currentDataset, appConfig.features.liveBackend, profileResult)) {
    showToast('Ingest the CSV into the gateway first — its rows are only in the browser.');
    return;
  }
  // opened synchronously so the popup blocker treats it as user-initiated
  const view = window.open('', '_blank');
  button.disabled = true;
  try {
    const { databaseConfig } = createDatasetRequest(currentDataset);
    const report = await generateReport(databaseConfig);
    if (!view) throw new Error('Allow pop-ups for this page to open the report.');
    view.document.open();
    view.document.write(report.html);
    view.document.close();
    showToast(`Report ready: ${report.meta.sections_verified}/${report.meta.sections_total} sections verified.`);
  } catch (error) {
    view?.close();
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

$('#question-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!currentDataset) {
    showToast('Select or upload a dataset before analyzing.');
    return;
  }
  const { databaseConfig, datasetOptions } = createDatasetRequest(currentDataset);
  const requestOptions = { scenario: $('#scenario').value, dataset: datasetOptions, aiContext: profileResult?.aiContext };
  if (!canAnalyzeDataset(currentDataset, appConfig.features.liveBackend, profileResult)) {
    showToast(appConfig.features.liveBackend ? 'Ingest the CSV into the gateway first — its rows are only in the browser.' : profileResult?.status === 'error' ? 'Fix profiling errors before analyzing.' : 'Profile the uploaded data before analyzing.');
    return;
  }
  const question = $('#question').value;
  if (!question.trim()) {
    $('#question').focus();
    showToast('Enter a question to analyze.');
    return;
  }

  activeController?.abort();
  activeController = new AbortController();
  const requestId = ++requestCounter;
  store.dispatch({ type: 'analysis/requested', requestId });
  logger.log({ stage: 'request', requestId, message: 'Analysis requested' });
  lastResponse = null;
  lastError = null;
  latestTimeline = [];
  currentPipeline = appConfig.pipelineStages.map((stage) => ({ ...stage, status: 'pending' }));
  $('#pipeline-stepper').hidden = false;
  renderPipeline();
  $('#analysis-result').innerHTML = `<section class="result-placeholder"><span>${icon('spark')}</span><h3>Building your evidence stack</h3><p>The pipeline will report each stage as it completes.</p></section>`;
  setAnalyzing(true);

  try {
    const { context } = await runPipeline({
      stages: appConfig.pipelineStages,
      context: {},
      requestId,
      eventBus,
      logger,
      execute: async (stage, context) => {
        if (stage.id === 'generateSql') {
          const response = await runAnalysis(question, databaseConfig, {
            ...requestOptions,
            mock: !appConfig.features.liveBackend,
            signal: activeController.signal,
          });
          return { ...context, response };
        }
        if (!appConfig.features.liveBackend) await sleep(55);
        return context;
      },
    });
    const result = context.response;
    lastResponse = result;
    store.dispatch({ type: 'analysis/resolved', requestId, result });
    if (store.getState().requestId === requestId && store.getState().result === result) eventBus.emit('result:ready', { requestId, result });
  } catch (error) {
    if (error.name !== 'AbortError' && store.getState().requestId === requestId) {
      store.dispatch({ type: 'analysis/rejected', requestId, error: error.message });
      logger.log({ level: 'error', stage: 'response', requestId, message: error.message });
      renderResponseError(error);
    }
  } finally {
    if (store.getState().requestId === requestId) setAnalyzing(false);
  }
});

$('#analysis-result').addEventListener('click', async (event) => {
  const copyButton = event.target.closest('[data-copy-sql]');
  if (!copyButton) return;
  event.preventDefault();
  try {
    await navigator.clipboard.writeText(copyButton.closest('details').querySelector('.sql-code').textContent);
    showToast('SQL copied to clipboard.');
  } catch {
    showToast('Clipboard access is unavailable in this browser.');
  }
});

function metricCard(label, value, detail, iconName, percentage, good = true) {
  return `<article class="metric-card"><div class="metric-top"><span>${label}</span>${icon(iconName)}</div><div class="metric-value ${good ? 'good' : 'bad'}">${value}</div><div class="metric-detail">${detail}</div>${percentage !== undefined ? `<div class="metric-track"><span style="--value:${percentage}%"></span></div>` : ''}</article>`;
}

function renderEvaluation() {
  const metrics = calculateMetrics(evaluationScenarios);
  $('#evaluation-metrics').innerHTML = [
    metricCard('Execution success', `${metrics.execution.value}%`, `${metrics.execution.numerator} of ${metrics.execution.denominator} executable`, 'play', metrics.execution.value),
    metricCard('Result correctness', `${metrics.results.value}%`, `${metrics.results.numerator} of ${metrics.results.denominator} with results`, 'table', metrics.results.value),
    metricCard('Answer correctness', `${metrics.answers.value}%`, `${metrics.answers.numerator} of ${metrics.answers.denominator} scenarios`, 'check', metrics.answers.value),
    metricCard('Safety failures', metrics.safetyFailures, `${evaluationScenarios.filter((scenario) => scenario.category === 'Safety').length} adversarial scenario`, 'shield', undefined, metrics.safetyFailures === 0),
    metricCard('Average latency', `${metrics.averageLatency} ms`, `Across ${metrics.total} scenarios`, 'clock'),
  ].join('');
  const scenarios = filterScenarios(evaluationScenarios, { status: evaluationStatus, category: $('#evaluation-category').value, query: $('#evaluation-search').value });
  $('#evaluation-rows').innerHTML = scenarios.length ? scenarios.map((scenario) => `<tr><td>${escapeHtml(scenario.name)}<small>${scenario.id}</small></td><td><span class="category-tag">${escapeHtml(scenario.category)}</span></td><td>${escapeHtml(scenario.expected)}</td><td>${escapeHtml(scenario.actual)}</td><td>${scenario.latency} ms</td><td><span class="${scenario.passed ? 'pass-badge' : 'fail-badge'}">${icon(scenario.passed ? 'check' : 'close')}${scenario.passed ? 'Passed' : 'Failed'}</span></td></tr>`).join('') : '<tr><td colspan="6" class="no-filter-results">No scenarios match these filters.</td></tr>';
  $('#evaluation-count').textContent = `Showing ${scenarios.length} of ${evaluationScenarios.length} scenarios · ${metrics.passed} passed · ${metrics.total - metrics.passed} failed`;
}

[...new Set(evaluationScenarios.map((scenario) => scenario.category))].sort().forEach((category) => {
  const option = document.createElement('option');
  option.value = category;
  option.textContent = category;
  $('#evaluation-category').append(option);
});
$('#evaluation-search').addEventListener('input', renderEvaluation);
$('#evaluation-category').addEventListener('change', renderEvaluation);
$$('.filter-tabs button').forEach((button) => button.addEventListener('click', () => {
  evaluationStatus = button.dataset.status;
  $$('.filter-tabs button').forEach((item) => item.classList.toggle('active', item === button));
  renderEvaluation();
}));
$('#run-evaluation').addEventListener('click', async () => {
  const button = $('#run-evaluation');
  button.disabled = true;
  button.innerHTML = `${icon('clock')}<span>Running…</span>`;
  $('#last-run').textContent = 'Running baseline fixtures…';
  await sleep(750);
  renderEvaluation();
  $('#last-run').textContent = `Fixture run ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · not live`;
  button.disabled = false;
  button.innerHTML = `${icon('play')}<span>Run evaluation</span>`;
  showToast('Baseline fixtures evaluated: 12 passed, 1 failed.');
});
$('#export-results').addEventListener('click', () => {
  const report = { timestamp: new Date().toISOString(), mode: evaluationFixture.mode, metrics: calculateMetrics(evaluationScenarios), scenarios: evaluationScenarios };
  assertValid(report, evaluationReportSchema, 'Evaluation report');
  downloadJson('askbase-evaluation.json', report);
  showToast('Baseline fixture report exported.');
});

function renderDocs() {
  $('#endpoint-list').innerHTML = Object.entries(apiContract.paths).map(([path, operations]) => Object.entries(operations).map(([method, operation]) => `<article class="endpoint-card"><div class="endpoint-top"><span class="method-badge ${method}">${method.toUpperCase()}</span><code class="endpoint-path">${escapeHtml(path)}</code></div><h3>${escapeHtml(operation.summary)}</h3><p>${escapeHtml(operation.description || `Returns ${operation.responses?.['200']?.description || 'the contracted resource'}.`)}</p><div class="endpoint-contract">200 · ${escapeHtml(operation.responses?.['200']?.description || 'Success')} · application/json</div></article>`).join('')).join('');
}

function renderDeveloper() {
  $$('.developer-tabs button').forEach((button) => button.classList.toggle('active', button.dataset.devTab === developerTab));
  const content = $('#developer-content');
  if (developerTab === 'response') {
    const value = lastResponse || lastError;
    content.innerHTML = value ? `<pre>${syntaxHighlight(value)}</pre>` : '<div class="developer-empty">Run an analysis to inspect its validated raw JSON response.</div>';
  } else if (developerTab === 'pipeline') {
    content.innerHTML = latestTimeline.length ? `<div class="developer-timeline">${latestTimeline.map((stage) => `<div class="developer-stage"><span>${icon(stage.status === 'complete' ? 'check' : 'warning')}</span><strong>${escapeHtml(stage.label)}</strong><small>${stage.durationMs} ms · ${stage.status}</small></div>`).join('')}</div>` : '<div class="developer-empty">Pipeline stages and durations will appear here.</div>';
  } else if (developerTab === 'logs') {
    const logs = logger.getLogs().reverse();
    content.innerHTML = logs.length ? `<div class="log-list">${logs.map((log) => `<article class="log-entry"><div><span class="log-level">${log.level}</span><span>${escapeHtml(log.stage)}</span><span>request ${log.requestId ?? '—'}</span><span>${log.durationMs ?? '—'} ms</span></div><p>${escapeHtml(log.message)}</p><small>${log.timestamp}</small></article>`).join('')}</div>` : '<div class="developer-empty">Structured JSON logs will appear here.</div>';
  } else content.innerHTML = `<pre>${syntaxHighlight({ app: appConfig, theme: themeTokens, activeDataset: currentDataset ? (currentDataset.kind === 'sample' ? currentDataset : createDatasetRequest(currentDataset)) : null })}</pre>`;
}

function openDeveloper() {
  $('#developer-drawer').hidden = false;
  $('#drawer-overlay').hidden = false;
  document.body.classList.add('drawer-open');
  renderDeveloper();
  $('#developer-close').focus();
}
function closeDeveloper() {
  $('#developer-drawer').hidden = true;
  $('#drawer-overlay').hidden = true;
  document.body.classList.remove('drawer-open');
  $('#developer-open').focus();
}
$('#developer-open').addEventListener('click', openDeveloper);
$('#developer-close').addEventListener('click', closeDeveloper);
$('#drawer-overlay').addEventListener('click', closeDeveloper);
document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !$('#developer-drawer').hidden) closeDeveloper(); });
$$('.developer-tabs button').forEach((button) => button.addEventListener('click', () => {
  developerTab = button.dataset.devTab;
  renderDeveloper();
}));
$('#download-debug').addEventListener('click', () => {
  const { databaseConfig, datasetOptions } = currentDataset ? createDatasetRequest(currentDataset) : { databaseConfig: null, datasetOptions: null };
  downloadJson('askbase-debug-bundle.json', {
    generatedAt: new Date().toISOString(),
    app: appConfig,
    mode: appConfig.buildMode,
    databaseConfig,
    dataset: datasetOptions,
    response: lastResponse,
    error: lastError,
    pipeline: latestTimeline,
    logs: logger.getLogs(),
  });
  showToast('Debug bundle downloaded.');
});

renderEvaluation();
renderDocs();
restoreSample();
