import test from 'node:test';
import assert from 'node:assert/strict';
import scenarios from '../config/scenarios.json' with { type: 'json' };
import analysisSchema from '../schemas/analysis-response.schema.json' with { type: 'json' };
import { assertValid, validateSchema } from '../lib/validate.js';

const validResponse = scenarios.scenarios[0].response;

test('validator accepts every configured analysis response', () => {
  for (const scenario of scenarios.scenarios) assert.deepEqual(validateSchema(scenario.response, analysisSchema), []);
});

test('validator reports missing fields and bad enums', () => {
  const missing = { ...validResponse };
  delete missing.answer;
  assert.ok(validateSchema(missing, analysisSchema).some((error) => error.includes('$.answer: required')));
  assert.ok(validateSchema({ ...validResponse, status: 'unknown' }, analysisSchema).some((error) => error.includes('expected one of')));
  assert.ok(validateSchema({ ...validResponse, rowCount: '4' }, analysisSchema).some((error) => error.includes('expected integer')));
});

test('assertValid throws a structured validation error', () => {
  assert.throws(() => assertValid({}, analysisSchema), (error) => error.name === 'ValidationError' && error.details.length > 0);
  assert.equal(assertValid(validResponse, analysisSchema), validResponse);
});

test('validator handles nested arrays and nullable objects', () => {
  assert.deepEqual(validateSchema(validResponse.rows, { type: 'array', items: { type: 'array' } }), []);
  assert.deepEqual(validateSchema(null, { type: ['object', 'null'] }), []);
});
