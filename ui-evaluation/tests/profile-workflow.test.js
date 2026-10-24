import test from 'node:test';
import assert from 'node:assert/strict';
import { profileFiles } from '../dataset.js';
import { runAnalysis } from '../analysis-service.js';

test('profiles one messy CSV with real column metrics and warnings', async () => {
  const file = new File([
    'id,email,amount,when,category\n'
      + '1,person@example.com,10.5,2026-01-01,Books\n'
      + '2,,N/A,2026-02-30,books \n'
      + '2,,20,2026-03-01,BOOKS\n',
  ], 'messy.csv', { type: 'text/csv' });

  const result = await profileFiles([file]);
  assert.equal(result.status, 'warning');
  assert.equal(result.summary.fileCount, 1);
  assert.equal(result.summary.totalRows, 3);
  assert.equal(result.summary.totalColumns, 5);
  assert.equal(result.files[0].duplicateRowCount, 0);
  assert.equal(result.files[0].columns.find((column) => column.name === 'email').nullCount, 2);
  assert.equal(result.files[0].columns.find((column) => column.name === 'amount').logicalType, 'decimal');
  assert.equal(result.files[0].columns.find((column) => column.name === 'when').logicalType, 'date');
  assert.equal(result.files[0].columns.find((column) => column.name === 'when').invalidCount, 1);
  assert.ok(result.files[0].columns.find((column) => column.name === 'category').warnings.length);
  assert.ok(result.aiContext.columns.every((column) => !('rawCsv' in column)));
  assert.deepEqual(result.aiContext.columns.find((column) => column.name === 'email').sampleValues, []);
});

test('profiles multiple CSV files and aggregates their totals', async () => {
  const files = [
    new File(['id,name\n1,A\n2,B\n'], 'customers.csv', { type: 'text/csv' }),
    new File(['id,total\n1,10\n'], 'orders.csv', { type: 'text/csv' }),
  ];
  const result = await profileFiles(files);
  assert.equal(result.status, 'warning');
  assert.equal(result.summary.fileCount, 2);
  assert.equal(result.summary.totalRows, 3);
  assert.equal(result.aiContext.tables.length, 2);
});

test('invalid encoding returns an explicit profile error', async () => {
  const file = new File([new Uint8Array([0xff, 0xfe, 0xfd])], 'bad.csv', { type: 'text/csv' });
  const result = await profileFiles([file]);
  assert.equal(result.status, 'error');
  assert.equal(result.summary.errorCount, 1);
  assert.equal(result.files[0].status, 'error');
});

test('detects mixed logical types, duplicate rows, and configured limits', async () => {
  const mixed = new File(['value\n1\nhello\n1\n'], 'mixed.csv', { type: 'text/csv' });
  const result = await profileFiles([mixed], { maxRows: 10, maxColumns: 10 });
  assert.equal(result.files[0].columns[0].logicalType, 'mixed');
  assert.equal(result.files[0].duplicateRowCount, 1);

  const tooManyRows = new File(['value\n1\n2\n3\n'], 'large.csv', { type: 'text/csv' });
  const limited = await profileFiles([tooManyRows], { maxRows: 2, maxColumns: 10 });
  assert.equal(limited.status, 'error');
  assert.match(limited.errors[0].message, /at most 2 rows/);
});

test('profile context crosses the existing analysis boundary as mock-only', async () => {
  const result = await runAnalysis('Summarize the dataset', { database: 'dataset-1' }, {
    delay: 0,
    aiContext: {
      tables: [{ name: 'orders' }],
      columns: [{ name: 'total', logicalType: 'decimal' }],
      warnings: [],
    },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.meta.mode, 'mock');
  assert.equal(result.meta.aiContextReceived, true);
  assert.equal(result.verification.passed, false);
  assert.match(result.warnings[0], /Mock analysis only/);
});
