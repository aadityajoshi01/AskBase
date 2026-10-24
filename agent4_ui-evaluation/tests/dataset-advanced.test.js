import test from 'node:test';
import assert from 'node:assert/strict';
import profileSchema from '../schemas/dataset-profile.schema.json' with { type: 'json' };
import { canAnalyzeDataset, createDatasetRequest, detectDelimiter, inspectDataset, parseCsv } from '../dataset.js';
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

test('uploaded datasets need a live backend for analysis', () => {
  assert.equal(canAnalyzeDataset({ kind: 'sample' }), true);
  assert.equal(canAnalyzeDataset({ kind: 'csv' }), false);
  assert.equal(canAnalyzeDataset({ kind: 'csv' }, true), true);
});

test('CSV schema and file metadata are prepared for the integration boundary', () => {
  const dataset = { kind: 'csv', fileName: 'orders.csv', size: 120, delimiter: ',', rowCount: 3, columns: [{ name: 'id', type: 'NUMBER', nullCount: 0 }] };
  const request = createDatasetRequest(dataset);
  assert.deepEqual(request.databaseConfig, { type: 'csv', database: 'orders.csv' });
  assert.equal(request.datasetOptions.schema.columns[0].name, 'id');
  assert.equal(request.datasetOptions.file.fileName, 'orders.csv');
});
