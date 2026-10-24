import test from 'node:test';
import assert from 'node:assert/strict';
import { calculateMetrics, evaluationScenarios, filterScenarios } from '../evaluation.js';

test('evaluation metrics use explicit denominators', () => {
  const metrics = calculateMetrics(evaluationScenarios);
  assert.equal(metrics.total, 13);
  assert.equal(metrics.passed, 12);
  assert.deepEqual(metrics.execution, { value: 100, numerator: 9, denominator: 9 });
  assert.deepEqual(metrics.results, { value: 88.9, numerator: 8, denominator: 9 });
  assert.deepEqual(metrics.answers, { value: 92.3, numerator: 12, denominator: 13 });
  assert.equal(metrics.safetyFailures, 0);
});

test('empty evaluation sets do not produce invalid percentages', () => {
  const metrics = calculateMetrics([]);
  assert.equal(metrics.execution.value, 0);
  assert.equal(metrics.results.value, 0);
  assert.equal(metrics.answers.value, 0);
  assert.equal(metrics.averageLatency, 0);
});

test('scenario filters compose status, category, and search', () => {
  const failed = filterScenarios(evaluationScenarios, { status: 'failed' });
  assert.equal(failed.length, 1);
  assert.equal(failed[0].id, 'EVAL-12');

  const quality = filterScenarios(evaluationScenarios, { category: 'Data quality' });
  assert.equal(quality.length, 2);

  const search = filterScenarios(evaluationScenarios, { query: 'offline' });
  assert.deepEqual(search.map((scenario) => scenario.id), ['EVAL-11']);

  assert.deepEqual(filterScenarios(evaluationScenarios, { status: 'passed', category: 'Safety' }).map((scenario) => scenario.id), ['EVAL-10']);
  assert.deepEqual(filterScenarios(evaluationScenarios, { status: 'failed', category: 'Safety' }), []);
});
