import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { analysisScenarios, runAnalysis, uploadDatasetCsv } from '../analysis-service.js';

const scenarios = JSON.parse(
  readFileSync(fileURLToPath(new URL('../config/scenarios.json', import.meta.url)), 'utf8')
).scenarios;

const database = { database: 'sample_retail' };
const mock = { mock: true, delay: 0 };

function withStubServer(handler, run) {
  return new Promise((resolve, reject) => {
    const stub = createServer((request, response) => {
      let raw = '';
      request.on('data', (chunk) => { raw += chunk; });
      request.on('end', () => handler(JSON.parse(raw || '{}'), request, response));
    });
    stub.listen(0, '127.0.0.1', async () => {
      const previous = process.env.ASKBASE_API_BASE;
      process.env.ASKBASE_API_BASE = `http://127.0.0.1:${stub.address().port}`;
      try {
        resolve(await run());
      } catch (error) {
        reject(error);
      } finally {
        if (previous === undefined) delete process.env.ASKBASE_API_BASE;
        else process.env.ASKBASE_API_BASE = previous;
        stub.close();
      }
    });
  });
}

function json(response, body, statusCode = 200) {
  response.writeHead(statusCode, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(body));
}

test('verified analysis returns evidence and a chart', async () => {
  const result = await runAnalysis('Revenue by category', database, mock);
  assert.equal(result.status, 'success');
  assert.equal(result.verification.passed, true);
  assert.equal(result.rows.length, result.row_count);
  assert.equal(result.chart.type, 'bar');
  assert.match(result.sql, /GROUP BY/);
});

test('mock responses cover every required UI state', async () => {
  for (const scenario of analysisScenarios) {
    const result = await runAnalysis('Demo question', database, { ...mock, scenario: scenario.value });
    assert.equal(result.row_count, result.rows.length);
    assert.ok(['success', 'clarification_needed', 'error'].includes(result.status));
    if (result.status !== 'success') assert.equal(result.rows.length, 0);
  }
});

test('clarification and model failures never claim execution', async () => {
  for (const scenario of ['clarification', 'offline', 'unsafe']) {
    const result = await runAnalysis('Demo question', database, { ...mock, scenario });
    assert.equal(result.verification.passed, false);
    assert.equal(result.execution, undefined);
  }
});

test('empty results remain successful and have no chart', async () => {
  const result = await runAnalysis('No matching orders', database, { ...mock, scenario: 'empty' });
  assert.equal(result.status, 'success');
  assert.equal(result.row_count, 0);
  assert.equal(result.chart, null);
  assert.equal(result.verification.passed, true);
});

test('question and database boundaries are validated', async () => {
  await assert.rejects(runAnalysis(' ', database, mock), /Enter a question/);
  await assert.rejects(runAnalysis('Revenue', {}, mock), /Select a database/);
});

test('mutating a returned mock does not change later responses', async () => {
  const first = await runAnalysis('Revenue', database, mock);
  first.rows[0][1] = 0;
  const second = await runAnalysis('Revenue', database, mock);
  assert.equal(second.rows[0][1], 48250);
});

test('live mode posts to the gateway and normalizes the envelope', async () => {
  let seen = null;
  const stub = createServer((request, response) => {
    let raw = '';
    request.on('data', (chunk) => { raw += chunk; });
    request.on('end', () => {
      seen = JSON.parse(raw);
      const body = structuredClone(scenarios.find(({ id }) => id === 'success').response);
      delete body.rowCount;
      body.row_count = 4;
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify(body));
    });
  });
  await new Promise((resolve) => stub.listen(0, '127.0.0.1', resolve));
  const previous = process.env.ASKBASE_API_BASE;
  process.env.ASKBASE_API_BASE = `http://127.0.0.1:${stub.address().port}`;
  try {
    const result = await runAnalysis('Revenue by category', database, {});
    assert.equal(seen.question, 'Revenue by category');
    assert.equal(result.status, 'success');
    assert.equal(result.rowCount, 4, 'row_count alias is normalized for the UI schema');
  } finally {
    if (previous === undefined) delete process.env.ASKBASE_API_BASE;
    else process.env.ASKBASE_API_BASE = previous;
    stub.close();
  }
});

test('ingest posts the CSV text and returns the registered dataset', async () => {
  let path = null;
  let seen = null;
  const result = await withStubServer((body, request, response) => {
    path = request.url;
    seen = body;
    json(response, { status: 'ok', database: { id: 'sales_1a2b', label: 'sales.csv (upload)',
      table: 'sales', row_count: 3, columns: [], source: 'upload' }, schema: null, profile: null }, 201);
  }, async () => {
    const body = await uploadDatasetCsv('sales.csv', 'id,region\n1,West\n2,East\n3,West');
    assert.equal(path, '/api/datasets/upload');
    assert.equal(seen.fileName, 'sales.csv');
    assert.equal(seen.csv, 'id,region\n1,West\n2,East\n3,West');
    return body;
  });
  assert.equal(result.database.id, 'sales_1a2b');
  assert.equal(result.database.row_count, 3);
});

test('an accepted-but-rejected CSV surfaces the gateway reason', async () => {
  await assert.rejects(
    withStubServer((body, request, response) => {
      json(response, { status: 'error', message: 'the CSV needs a header row and at least one data row' }, 400);
    }, () => uploadDatasetCsv('empty.csv', 'a,b,c\n')),
    /header row and at least one data row/);
});

test('an unreachable gateway says how to start it', async () => {
  const previous = process.env.ASKBASE_API_BASE;
  process.env.ASKBASE_API_BASE = 'http://127.0.0.1:1';
  try {
    await assert.rejects(uploadDatasetCsv('sales.csv', 'id\n1'), /unreachable at http:\/\/127\.0\.0\.1:1/);
  } finally {
    if (previous === undefined) delete process.env.ASKBASE_API_BASE;
    else process.env.ASKBASE_API_BASE = previous;
  }
});

test('ingest refuses an empty payload before any request', async () => {
  await assert.rejects(uploadDatasetCsv('', 'id\n1'), /Choose a CSV file/);
  await assert.rejects(uploadDatasetCsv('sales.csv', '   '), /CSV is empty/);
});
