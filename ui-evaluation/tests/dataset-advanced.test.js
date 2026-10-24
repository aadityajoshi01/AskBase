import test from 'node:test';
import assert from 'node:assert/strict';
import profileSchema from '../schemas/dataset-profile.schema.json' with { type: 'json' };
import { canAnalyzeDataset, createDatasetRequest, detectDelimiter, inspectDataset, parseCsv, recommendQuestions } from '../dataset.js';
import { validateSchema } from '../lib/validate.js';

test('BOM and semicolon-delimited CSVs are supported', async () => {
  const file = new File(['\uFEFFid;category;note\n1;Books;"quoted;value"'], 'inventory.csv');
  const dataset = await inspectDataset(file);
  assert.equal(dataset.delimiter, ';');
  assert.deepEqual(dataset.rows[0], ['1', 'Books', 'quoted;value']);
  assert.deepEqual(validateSchema(dataset, profileSchema), []);
});

test('tab-delimited CSVs and delimiters inside quotes are detected correctly', () => {
  const text = 'id\tname\tnote\n1\t"Jane, Doe"\t"a;b"';
  assert.equal(detectDelimiter(text), '\t');
  assert.deepEqual(parseCsv(text)[1], ['1', 'Jane, Doe', 'a;b']);
});

test('null counts and configurable preview limits are included', async () => {
  const file = new File(['id,value\n1,\n2,text\n3,\n4,last'], 'nulls.csv');
  const dataset = await inspectDataset(file, { previewRows: 2 });
  assert.equal(dataset.columns[1].nullCount, 2);
  assert.equal(dataset.rows.length, 2);
  assert.equal(dataset.rowCount, 4);
  assert.equal(dataset.truncated, true);
});

test('only server-side data is analyzable — a browser CSV must be ingested first', () => {
  assert.equal(canAnalyzeDataset({ kind: 'sample' }), true);
  assert.equal(canAnalyzeDataset({ kind: 'gateway', databaseId: 'sales_1a2b' }), true);
  assert.equal(canAnalyzeDataset({ kind: 'csv' }), false);
  assert.equal(canAnalyzeDataset({ kind: 'sqlite' }), false);
  assert.equal(canAnalyzeDataset({ kind: 'csv' }, true), false, 'a live gateway does not move browser rows onto the server');
});

test('gateway datasets are analyzable and address themselves by registered id', () => {
  const dataset = { kind: 'gateway', fileName: 'Kaggle Superstore', databaseId: 'superstore' };
  assert.equal(canAnalyzeDataset(dataset), true);
  const request = createDatasetRequest(dataset);
  assert.deepEqual(request.databaseConfig, { type: 'gateway', database: 'superstore' });
  assert.equal(request.datasetOptions, null);
});

test('CSV schema and file metadata are prepared for the integration boundary', () => {
  const dataset = { kind: 'csv', fileName: 'orders.csv', size: 120, delimiter: ',', rowCount: 3, columns: [{ name: 'id', type: 'NUMBER', nullCount: 0 }] };
  const request = createDatasetRequest(dataset);
  assert.deepEqual(request.databaseConfig, { type: 'csv', database: 'orders.csv' });
  assert.equal(request.datasetOptions.schema.columns[0].name, 'id');
  assert.equal(request.datasetOptions.file.fileName, 'orders.csv');
});

test('recommended questions use only columns the dataset really has', () => {
  const questions = recommendQuestions([{
    name: 'orders',
    row_count: 1024,
    columns: [
      { name: 'order_id', type: 'INTEGER', pk: true },
      { name: 'customer_id', type: 'INTEGER', fk_to: 'customers.id' },
      { name: 'status', type: 'TEXT' },
      { name: 'total_amount', type: 'REAL' },
      { name: 'ordered_at', type: 'TEXT' },
      { name: 'email', type: 'TEXT' },
    ],
  }]);
  assert.equal(questions[0], 'Which status has the highest total amount?');
  assert.ok(questions.includes('Top customers by number of orders.'));
  assert.ok(questions.includes('How did total amount change over ordered at?'));
  assert.ok(!questions.some((question) => /email|order id/i.test(question)));
});

test('a dataset with nothing to group or measure suggests nothing', () => {
  assert.deepEqual(recommendQuestions([{ name: 'events', columns: [{ name: 'payload', type: 'BLOB' }] }]), []);
  assert.deepEqual(recommendQuestions([]), []);
});
