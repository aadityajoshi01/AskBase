import appConfig from './config/app.config.json' with { type: 'json' };
import themeTokens from './config/theme.tokens.json' with { type: 'json' };
import exampleConfig from './config/examples.json' with { type: 'json' };
import sampleDatabase from './config/sample-db.schema.json' with { type: 'json' };
import evaluationFixture from './config/evaluation.fixtures.json' with { type: 'json' };
import apiContract from './docs/api-contract.json' with { type: 'json' };
import datasetProfileSchema from './schemas/dataset-profile.schema.json' with { type: 'json' };
import evaluationReportSchema from './schemas/evaluation-report.schema.json' with { type: 'json' };
import { analysisScenarios, runAnalysis } from './analysis-service.js';
import { canAnalyzeDataset, createDatasetRequest, inspectDataset } from './dataset.js';
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

function renderExamples() {
  const examples = exampleConfig.groups.flatMap((group) => group.questions).slice(0, 5);
  $('#example-chips').innerHTML = examples.map((example) => `<button type="button" data-scenario="${example.scenario}" data-question="${escapeHtml(example.question)}">${escapeHtml(example.question)}</button>`).join('');
  $$('#example-chips button').forEach((button) => button.addEventListener('click', () => {
    $('#question').value = button.dataset.question;
    $('#scenario').value = button.dataset.scenario;
    $('#question').focus();
  }));
}

analysisScenarios.forEach((scenario) => {
  const option = document.createElement('option');
  option.value = scenario.value;
  option.textContent = scenario.label;
  $('#scenario').append(option);
});
renderSampleContext();
renderExamples();
$('#app-version').textContent = `v${appConfig.version} · ${appConfig.buildMode}`;
$('#developer-version').textContent = `${appConfig.name} v${appConfig.version} · ${appConfig.buildMode}`;

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
  currentDataset = { kind: 'sample', fileName: 'Sample retail' };
  $('#dataset-upload').value = '';
  $('#sample-source').classList.add('active');
  $('#upload').classList.remove('active');
  $('#sample-explorer').hidden = false;
  $('#dataset-preview').hidden = true;
  $('#active-source').innerHTML = `${icon('database')}Sample retail`;
  $('#upload-backend-note').hidden = true;
  $('#scenario').disabled = false;
  resetAnalysisView();
  setAnalyzing(false);
}

function renderDatasetPreview(dataset) {
  $('#sample-source').classList.remove('active');
  $('#upload').classList.add('active');
  $('#sample-explorer').hidden = true;
  $('#active-source').innerHTML = `${icon('file')}${escapeHtml(dataset.fileName)}`;
  $('#upload-backend-note').hidden = false;
  $('#scenario').disabled = true;
  const chips = [dataset.fileName, formatBytes(dataset.size), dataset.kind === 'csv' ? `${dataset.rowCount.toLocaleString()} rows` : 'Schema pending', dataset.kind === 'csv' ? `${dataset.columns.length} columns` : 'SQLite', dataset.delimiter ? delimiterName(dataset.delimiter) : null].filter(Boolean);
  const schema = dataset.kind === 'csv' ? `<div class="table-scroll"><table class="dataset-schema-table"><thead><tr><th>Column</th><th>Inferred type</th><th>Null count</th></tr></thead><tbody>${dataset.columns.map((column) => `<tr><td>${escapeHtml(column.name)}</td><td><span class="type-chip">${column.type}</span></td><td>${column.nullCount}</td></tr>`).join('')}</tbody></table></div>` : '';
  const preview = dataset.kind === 'csv' ? `<h3 class="preview-title">Data preview · first ${dataset.rows.length} rows</h3><div class="table-scroll"><table class="result-table"><thead><tr>${dataset.columns.map((column) => `<th>${escapeHtml(column.name)}</th>`).join('')}</tr></thead><tbody>${dataset.rows.map((row) => `<tr>${row.map((value) => `<td>${escapeHtml(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '';
  $('#dataset-preview').innerHTML = `<div class="dataset-header"><div class="dataset-file"><span>${icon('file')}</span><div><strong>${escapeHtml(dataset.fileName)}</strong><small>${dataset.kind === 'csv' ? 'CSV parsed locally · nothing uploaded' : 'SQLite metadata only · nothing uploaded'}</small></div></div><button class="light-pill" data-remove-dataset>Remove dataset</button></div><div class="dataset-chips">${chips.map((chip) => `<span class="dataset-chip">${escapeHtml(chip)}</span>`).join('')}</div>${schema}${preview}<p class="upload-backend-note">${icon('warning')}Analysis of uploaded CSVs is available once the backend is connected.</p>`;
  $('#dataset-preview').hidden = false;
  resetAnalysisView();
  setAnalyzing(false);
}

async function handleDatasetFile(file) {
  if (!file) return;
  $('#upload-error').hidden = true;
  try {
    const dataset = await inspectDataset(file, appConfig.limits);
    if (dataset.kind === 'csv') assertValid(dataset, datasetProfileSchema, 'Dataset profile');
    currentDataset = dataset;
    renderDatasetPreview(dataset);
    showToast('Dataset preview ready. Analysis requires the backend.');
  } catch (error) {
    $('#dataset-upload').value = '';
    $('#upload-error').textContent = error.message;
    $('#upload-error').hidden = false;
  }
}

$('#sample-source').addEventListener('click', restoreSample);
$('#dataset-preview').addEventListener('click', (event) => {
  if (event.target.closest('[data-remove-dataset]')) {
    restoreSample();
    showToast('Sample retail restored.');
  }
});
$('#dropzone').addEventListener('click', () => $('#dataset-upload').click());
$('#dropzone').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    $('#dataset-upload').click();
  }
});
$('#dataset-upload').addEventListener('change', (event) => handleDatasetFile(event.target.files[0]));
for (const eventName of ['dragenter', 'dragover']) $('#dropzone').addEventListener(eventName, (event) => {
  event.preventDefault();
  $('#upload').classList.add('drag-over');
});
for (const eventName of ['dragleave', 'drop']) $('#dropzone').addEventListener(eventName, (event) => {
  event.preventDefault();
  $('#upload').classList.remove('drag-over');
});
$('#dropzone').addEventListener('drop', (event) => handleDatasetFile(event.dataTransfer.files[0]));
$('#sqlite-toggle').addEventListener('click', () => {
  uploadKind = uploadKind === 'csv' ? 'sqlite' : 'csv';
  $('#dataset-upload').accept = uploadKind === 'csv' ? '.csv,text/csv' : '.db,.sqlite';
  $('#dataset-upload').value = '';
  $('#dropzone-title').innerHTML = uploadKind === 'csv' ? 'Drag & drop your .csv file here <span>or browse</span>' : 'Choose your .db or .sqlite file <span>or browse</span>';
  $('#sqlite-toggle').textContent = uploadKind === 'csv' ? 'Use a SQLite file instead' : 'Use a CSV file instead';
  $('#dropzone').setAttribute('aria-label', uploadKind === 'csv' ? 'Upload a CSV dataset' : 'Select a SQLite database');
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
  return `<section class="result-card"><div class="result-card-header"><div class="result-card-title"><span>${icon('table')}</span><div><h3>Result table</h3><small>Actual rows returned by the mock executor</small></div></div><div class="result-meta"><span>${result.rowCount} ${result.rowCount === 1 ? 'row' : 'rows'}</span><span>${result.meta.durationMs ?? '—'} ms</span>${result.truncated ? '<span>Truncated</span>' : ''}</div></div><div class="table-scroll"><table class="result-table"><thead><tr>${result.columns.map((column, index) => `<th class="${numericColumns[index] ? 'numeric' : ''}">${escapeHtml(column)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((value, index) => `<td class="${numericColumns[index] ? 'numeric' : ''}">${formatValue(value, result.columns[index])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`;
}

function renderChart(result) {
  if (!result.chart || result.chart.type !== 'bar' || !result.rows.length) return '';
  const values = result.rows.map((row) => Number(row[1]));
  const maximum = Math.max(...values, 1);
  return `<section class="result-card"><div class="result-card-header"><div class="result-card-title"><span>${icon('chart')}</span><div><h3>${escapeHtml(result.chart.y)} by ${escapeHtml(result.chart.x)}</h3><small>Generated from returned rows only</small></div></div></div><div class="result-card-body"><div class="chart">${result.rows.map((row, index) => `<div class="chart-row"><span class="chart-label" title="${escapeHtml(row[0])}">${escapeHtml(row[0])}</span><div class="bar-track"><div class="bar-fill" style="--bar:${Math.max(2, values[index] / maximum * 100)}%"></div></div><span class="chart-value">${formatValue(row[1], result.columns[1])}</span></div>`).join('')}</div><p class="chart-caption">No additional values are introduced for visualization.</p></div></section>`;
}

function renderResult(result) {
  const isError = result.status === 'error';
  const isClarification = result.status === 'clarification_needed';
  const code = result.meta.code;
  const title = isClarification ? 'Clarification needed' : code === 'unsafe_sql' ? 'Unsafe SQL blocked' : code === 'model_offline' ? 'Model unavailable' : isError ? 'Analysis failed' : result.rowCount === 0 ? 'Query completed · zero rows' : 'Verified answer';
  const subtitle = isClarification ? 'No SQL generated or executed' : isError ? result.verification.message : 'Evidence-backed response';
  const state = isError ? 'error' : isClarification ? 'clarification' : 'success';
  const badge = isClarification ? 'Question required' : isError ? 'Not verified' : `${icon('shield')}Verified`;
  const checks = result.verification.checks?.length ? `<div class="verification-checks">${result.verification.checks.map((check) => `<span class="verification-check">${icon('check')}${escapeHtml(check)}</span>`).join('')}</div>` : '';
  const warnings = result.warnings.length ? `<div class="warning-list">${result.warnings.map((warning) => `<span class="warning-item">${icon('warning')}${escapeHtml(warning)}</span>`).join('')}</div>` : '';
  $('#analysis-result').innerHTML = `<div class="result-stack"><section class="result-card ${state}"><div class="result-card-header"><div class="result-card-title"><span>${icon(isClarification ? 'help' : isError ? 'warning' : 'check')}</span><div><h3>${title}</h3><small>${escapeHtml(subtitle)}</small></div></div><span class="verified-pill">${badge}</span></div><div class="result-card-body"><p class="answer-text">${escapeHtml(result.answer)}</p></div></section><section class="result-card ${state}"><div class="result-card-header"><div class="result-card-title"><span>${icon(isError || isClarification ? 'warning' : 'shield')}</span><div><h3>Verification & limitations</h3><small>Status is separate from visual styling</small></div></div><span class="mock-result-label">Mock backend</span></div><div class="result-card-body"><p class="verification-message">${escapeHtml(result.verification.message)}</p>${checks}${warnings}</div></section>${renderSql(result)}${result.status === 'success' ? renderTable(result) : ''}${result.status === 'success' ? renderChart(result) : ''}</div>`;
}

function renderResponseError(error) {
  lastError = { name: error.name, message: error.message, details: error.details || [] };
  const malformed = error.name === 'ValidationError';
  $('#analysis-result').innerHTML = `<section class="result-card ${malformed ? 'malformed' : 'error'}"><div class="result-card-header"><div class="result-card-title"><span>${icon('warning')}</span><div><h3>${malformed ? 'Malformed response' : 'Analysis failed'}</h3><small>Never presented as verified success</small></div></div><span class="verified-pill">Error</span></div><div class="result-card-body"><p class="answer-text">${malformed ? 'The backend response did not match the shared contract, so it was not rendered as an answer.' : 'The analysis could not be completed. No result values are shown.'}</p><pre class="error-details">${escapeHtml(error.message)}</pre></div></section>`;
  if (!$('#developer-drawer').hidden) renderDeveloper();
}

function setAnalyzing(analyzing) {
  const button = $('#analyze-button');
  button.disabled = analyzing || !canAnalyzeDataset(currentDataset, appConfig.features.liveBackend);
  button.innerHTML = analyzing ? `${icon('clock')}<span>Analyzing…</span>` : `${icon('spark')}<span>Analyze question</span>${icon('arrow')}`;
}

$('#question-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const { databaseConfig, datasetOptions } = createDatasetRequest(currentDataset);
  const requestOptions = { scenario: $('#scenario').value, dataset: datasetOptions };
  if (!canAnalyzeDataset(currentDataset, appConfig.features.liveBackend)) {
    showToast('Analysis of uploaded CSVs is available once the backend is connected.');
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
          const response = await runAnalysis(question, databaseConfig, { ...requestOptions, signal: activeController.signal });
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
  } else content.innerHTML = `<pre>${syntaxHighlight({ app: appConfig, theme: themeTokens, activeDataset: currentDataset.kind === 'sample' ? currentDataset : createDatasetRequest(currentDataset) })}</pre>`;
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
  const { databaseConfig, datasetOptions } = createDatasetRequest(currentDataset);
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
