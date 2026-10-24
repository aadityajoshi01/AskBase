import test from 'node:test';
import assert from 'node:assert/strict';
import { inferColumnType, inspectDataset, parseCsv } from '../dataset.js';

test('CSV parser handles quotes, escaped quotes, commas, and newlines', () => {
  const records = parseCsv('name,note\r\n"Doe, Jane","said ""hello"""\r\nBob,"two\nlines"\r\n');
  assert.deepEqual(records, [
    ['name', 'note'],
    ['Doe, Jane', 'said "hello"'],
    ['Bob', 'two\nlines'],
  ]);
});

test('column types are inferred without changing source values', () => {
  assert.equal(inferColumnType(['1', '2.5', '']), 'NUMBER');
  assert.equal(inferColumnType(['2026-01-01', '2026-02-01']), 'DATE');
  assert.equal(inferColumnType(['Technology', 'TECHNOLOGY']), 'TEXT');
  assert.equal(inferColumnType(['', '']), 'TEXT');
});

test('CSV upload returns schema and bounded preview rows', async () => {
  const file = new File(['id,date,category\n1,2026-01-01,Technology\n2,2026-01-02,Books\n3,2026-01-03,Books\n4,2026-01-04,Sports\n5,2026-01-05,Sports\n6,2026-01-06,Home\n7,2026-01-07,Home'], 'products.csv');
  const dataset = await inspectDataset(file);
  assert.equal(dataset.kind, 'csv');
  assert.equal(dataset.rowCount, 7);
  assert.equal(dataset.rows.length, 6);
  assert.equal(dataset.truncated, true);
  assert.deepEqual(dataset.columns.map((column) => column.type), ['NUMBER', 'DATE', 'TEXT']);
});

test('SQLite selection does not pretend to discover the schema', async () => {
  const file = new File(['SQLite format 3\0'], 'retail.sqlite');
  const dataset = await inspectDataset(file);
  assert.equal(dataset.kind, 'sqlite');
  assert.equal(dataset.rowCount, null);
  assert.deepEqual(dataset.columns, []);
});

test('invalid and empty files are rejected at the upload boundary', async () => {
  await assert.rejects(inspectDataset(new File(['id\n1'], 'data.txt')), /Choose a CSV/);
  await assert.rejects(inspectDataset(new File([], 'data.csv')), /file is empty/);
  await assert.rejects(inspectDataset(new File(['id,name\n'], 'data.csv')), /at least one data row/);
  await assert.rejects(inspectDataset(new File(['id,id\n1,2'], 'data.csv')), /must be unique/);
  assert.throws(() => parseCsv('id,note\n1,"unfinished'), /unterminated/);
});
