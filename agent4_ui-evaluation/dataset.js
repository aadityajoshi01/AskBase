const DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024;
const DEFAULT_PREVIEW_ROWS = 6;

export function detectDelimiter(text) {
  const candidates = [',', ';', '\t'];
  const counts = { ',': 0, ';': 0, '\t': 0 };
  let quoted = false;
  const source = text.replace(/^\uFEFF/, '');
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === '"' && quoted && source[index + 1] === '"') index += 1;
    else if (character === '"') quoted = !quoted;
    else if (!quoted && (character === '\n' || character === '\r')) break;
    else if (!quoted && character in counts) counts[character] += 1;
  }
  return candidates.reduce((best, candidate) => counts[candidate] > counts[best] ? candidate : best, ',');
}

export function parseCsv(text, delimiter) {
  const source = text.replace(/^\uFEFF/, '');
  const separator = delimiter || detectDelimiter(source);
  const records = [];
  let record = [];
  let field = '';
  let quoted = false;

  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (quoted) {
      if (character === '"' && source[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (character === '"') quoted = false;
      else field += character;
    } else if (character === '"' && field === '') quoted = true;
    else if (character === separator) {
      record.push(field);
      field = '';
    } else if (character === '\n' || character === '\r') {
      if (character === '\r' && source[index + 1] === '\n') index += 1;
      record.push(field);
      if (record.some((value) => value !== '')) records.push(record);
      record = [];
      field = '';
    } else field += character;
  }

  if (quoted) throw new Error('The CSV contains an unterminated quoted value.');
  record.push(field);
  if (record.some((value) => value !== '')) records.push(record);
  return records;
}

export function inferColumnType(values) {
  const populated = values.filter((value) => value.trim() !== '');
  if (!populated.length) return 'TEXT';
  if (populated.every((value) => Number.isFinite(Number(value.trim())))) return 'NUMBER';
  if (populated.every((value) => !Number.isNaN(Date.parse(value)) && /^\d{4}-\d{2}-\d{2}/.test(value.trim()))) return 'DATE';
  return 'TEXT';
}

function inspectCsv(text, previewRows) {
  const delimiter = detectDelimiter(text);
  const records = parseCsv(text, delimiter);
  if (records.length < 2) throw new Error('Add a header row and at least one data row.');
  const headers = records[0].map((header) => header.trim());
  if (headers.some((header) => !header)) throw new Error('Every CSV column needs a name.');
  if (new Set(headers).size !== headers.length) throw new Error('CSV column names must be unique.');

  const rows = records.slice(1).map((record) => headers.map((_, index) => record[index] ?? ''));
  return {
    columns: headers.map((name, index) => {
      const values = rows.map((row) => row[index]);
      return { name, type: inferColumnType(values), nullCount: values.filter((value) => value.trim() === '').length };
    }),
    rows: rows.slice(0, previewRows),
    rowCount: rows.length,
    truncated: rows.length > previewRows,
    delimiter,
  };
}

export async function inspectDataset(file, options = {}) {
  const extension = file.name.toLowerCase().split('.').pop();
  const maxUploadBytes = options.maxUploadBytes ?? DEFAULT_MAX_FILE_SIZE;
  const previewRows = options.previewRows ?? DEFAULT_PREVIEW_ROWS;
  if (!['csv', 'db', 'sqlite'].includes(extension)) throw new Error('Choose a CSV, DB, or SQLite file.');
  if (file.size > maxUploadBytes) throw new Error(`Files must be ${Math.round(maxUploadBytes / 1024 / 1024)} MB or smaller.`);
  if (file.size === 0) throw new Error('The selected file is empty.');

  const dataset = { name: file.name, fileName: file.name, size: file.size, kind: extension === 'csv' ? 'csv' : 'sqlite' };
  if (dataset.kind === 'sqlite') return { ...dataset, columns: [], rows: [], rowCount: null, truncated: false, delimiter: null };
  return { ...dataset, ...inspectCsv(await file.text(), previewRows) };
}

export function canAnalyzeDataset(dataset, liveBackend = false) {
  return dataset.kind === 'sample' || liveBackend;
}

export function createDatasetRequest(dataset) {
  if (dataset.kind === 'sample') return { databaseConfig: { type: 'sample', database: 'sample_retail' }, datasetOptions: null };
  return {
    databaseConfig: { type: dataset.kind, database: dataset.fileName },
    datasetOptions: {
      schema: { columns: dataset.columns.map(({ name, type, nullCount }) => ({ name, type, nullCount })) },
      file: { fileName: dataset.fileName, size: dataset.size, delimiter: dataset.delimiter, rowCount: dataset.rowCount },
    },
  };
}
