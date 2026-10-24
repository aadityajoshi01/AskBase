import test from 'node:test';
import assert from 'node:assert/strict';
import { analysisScenarios, runAnalysis } from '../analysis-service.js';

const database = { database: 'sample_retail' };

test('verified analysis returns evidence and a chart', async () => {
  const result = await runAnalysis('Revenue by category', database, { delay: 0 });
  assert.equal(result.status, 'success');
  assert.equal(result.verification.passed, true);
  assert.equal(result.rows.length, result.row_count);
  assert.equal(result.chart.type, 'bar');
  assert.match(result.sql, /GROUP BY/);
});

test('mock responses cover every required UI state', async () => {
  for (const scenario of analysisScenarios) {
    const result = await runAnalysis('Demo question', database, { scenario: scenario.value, delay: 0 });
    assert.equal(result.row_count, result.rows.length);
    assert.ok(['success', 'clarification_needed', 'error'].includes(result.status));
    if (result.status !== 'success') assert.equal(result.rows.length, 0);
  }
});

test('clarification and model failures never claim execution', async () => {
  for (const scenario of ['clarification', 'offline', 'unsafe']) {
    const result = await runAnalysis('Demo question', database, { scenario, delay: 0 });
    assert.equal(result.verification.passed, false);
    assert.equal(result.execution, undefined);
  }
});

test('empty results remain successful and have no chart', async () => {
  const result = await runAnalysis('No matching orders', database, { scenario: 'empty', delay: 0 });
  assert.equal(result.status, 'success');
  assert.equal(result.row_count, 0);
  assert.equal(result.chart, null);
  assert.equal(result.verification.passed, true);
});

test('question and database boundaries are validated', async () => {
  await assert.rejects(runAnalysis(' ', database, { delay: 0 }), /Enter a question/);
  await assert.rejects(runAnalysis('Revenue', {}, { delay: 0 }), /Select a database/);
});

test('mutating a returned mock does not change later responses', async () => {
  const first = await runAnalysis('Revenue', database, { delay: 0 });
  first.rows[0][1] = 0;
  const second = await runAnalysis('Revenue', database, { delay: 0 });
  assert.equal(second.rows[0][1], 48250);
});
