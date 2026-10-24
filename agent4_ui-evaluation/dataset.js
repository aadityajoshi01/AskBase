const DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024;
const DEFAULT_MAX_ROWS = 100_000;
const DEFAULT_MAX_COLUMNS = 100;
const DEFAULT_PREVIEW_ROWS = 6;
const DEFAULT_SAMPLE_VALUES = 5;
const NULL_LIKE_VALUES = new Set(['', 'null', 'na', 'n/a', 'none', 'nil']);
const ALLOWED_MIME_TYPES = new Set(['', 'text/csv', 'application/csv', 'text/plain', 'application/vnd.ms-excel']);
const SENSITIVE_HEADER_PATTERN = /(email|e-mail|phone|mobile|address|street|ssn|social|password|secret|token|api.?key|credit.?card|bank|account)/i;
const IDENTIFIER_HEADER_PATTERN = /(^id$|_id$|uuid|identifier|key$)/i;

export const PROFILE_LIMITS = Object.freeze({
  maxUploadBytes: DEFAULT_MAX_FILE_SIZE,
  maxRows: DEFAULT_MAX_ROWS,
  maxColumns: DEFAULT_MAX_COLUMNS,
  previewRows: DEFAULT_PREVIEW_ROWS,
});

export function detectDelimiter(text) {
  const candidates = [',', ';', '\t'];
  const counts = { ',': 0, ';': 0, '\t': 0 };
  let quoted = false;
  const source = String(text).replace(/^\uFEFF/, '');
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
  const source = String(text).replace(/^\uFEFF/, '');
  const separator = delimiter || detectDelimiter(source);
  const records = [];
  let record = [];
  let field = '';
  let quoted = false;
  let justClosedQuote = false;

  const finishRecord = () => {
    record.push(field);
    if (record.some((value) => value !== '')) records.push(record);
    record = [];
    field = '';
    justClosedQuote = false;
  };

  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (quoted) {
      if (character === '"' && source[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (character === '"') {
        quoted = false;
        justClosedQuote = true;
      } else field += character;
    } else if (character === '"' && field === '') {
      quoted = true;
      justClosedQuote = false;
    } else if (justClosedQuote && character !== separator && character !== '\n' && character !== '\r') {
      throw new Error('The CSV contains characters after a closing quote.');
    } else if (character === separator) {
      record.push(field);
      field = '';
      justClosedQuote = false;
    } else if (character === '\n' || character === '\r') {
      if (character === '\r' && source[index + 1] === '\n') index += 1;
      finishRecord();
    } else field += character;
  }

  if (quoted) throw new Error('The CSV contains an unterminated quoted value.');
  if (record.length || field || justClosedQuote) finishRecord();
  return records;
}

function normalizedNull(value) {
  const text = String(value);
  return NULL_LIKE_VALUES.has(text.trim().toLowerCase());
}

function nonNullValues(values) {
  return values.filter((value) => !normalizedNull(value));
}

function isInteger(value) {
  return /^[+-]?\d+$/.test(String(value).trim());
}

function isDecimal(value) {
  const text = String(value).trim();
  return text !== '' && Number.isFinite(Number(text));
}

function isBoolean(value) {
  return /^(true|false|yes|no)$/i.test(String(value).trim());
}

function parseDateValue(value) {
  const text = String(value).trim();
  const dateMatch = /^(\d{4})[-/]([01]\d)[-/]([0-3]\d)$/.exec(text);
  const slashMatch = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(text);
  const dateTimeMatch = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?Z?$/.exec(text);
  let year;
  let month;
  let day;
  let hasTime = false;
  if (dateTimeMatch) {
    [, year, month, day] = dateTimeMatch;
    hasTime = true;
  } else if (dateMatch) {
    [, year, month, day] = dateMatch;
  } else if (slashMatch) {
    [, day, month, year] = slashMatch;
  } else return null;

  const parsed = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (parsed.getUTCFullYear() !== Number(year) || parsed.getUTCMonth() !== Number(month) - 1 || parsed.getUTCDate() !== Number(day)) return null;
  return { iso: parsed.toISOString(), hasTime };
}

function looksLikeDate(value) {
  const text = String(value).trim();
  return /^(?:\d{4}[-/]\d{2}[-/]\d{2}|\d{1,2}\/\d{1,2}\/\d{4})(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?)?$/.test(text);
}

function inferLogicalType(values) {
  const populated = nonNullValues(values);
  if (!populated.length) return 'unknown';
  if (populated.every(isBoolean)) return 'boolean';
  if (populated.every(isInteger)) return 'integer';
  if (populated.every(isDecimal)) return 'decimal';
  const parsedDates = populated.map(parseDateValue);
  const dateShapes = populated.filter(looksLikeDate);
  if (dateShapes.length === populated.length) return parsedDates.some((value) => value?.hasTime) ? 'datetime' : 'date';
  const numericCount = populated.filter(isDecimal).length;
  const dateCount = dateShapes.length;
  if (numericCount || dateCount) return 'mixed';
  return 'string';
}

export function inferColumnType(values) {
  return legacyType(inferLogicalType(values));
}

function legacyType(logicalType) {
  if (['integer', 'decimal'].includes(logicalType)) return 'NUMBER';
  if (['date', 'datetime'].includes(logicalType)) return 'DATE';
  return 'TEXT';
}

function distinctValues(values) {
  return new Set(nonNullValues(values).map((value) => String(value))).size;
}

function mean(values) {
  const numbers = values.filter(isDecimal).map((value) => Number(String(value).trim()));
  return numbers.length ? numbers.reduce((total, value) => total + value, 0) / numbers.length : null;
}

function safeStatistics(values, logicalType) {
  if (['integer', 'decimal'].includes(logicalType)) {
    const numbers = values.filter(isDecimal).map((value) => Number(String(value).trim()));
    return numbers.length ? {
      minimum: Math.min(...numbers),
      maximum: Math.max(...numbers),
      mean: mean(values),
    } : { minimum: null, maximum: null, mean: null };
  }
  if (['date', 'datetime'].includes(logicalType)) {
    const dates = values.map(parseDateValue).filter(Boolean).map((value) => value.iso).sort();
    return { minimum: dates[0] || null, maximum: dates.at(-1) || null };
  }
  return {};
}

function warning(code, message, details = {}) {
  return { code, severity: 'warning', message, details };
}

function isSensitiveColumn(name, values) {
  if (SENSITIVE_HEADER_PATTERN.test(name)) return true;
  return values.some((value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value).trim()));
}

function columnProfile(name, values) {
  const populated = nonNullValues(values);
  const logicalType = inferLogicalType(values);
  const sensitive = isSensitiveColumn(name, populated);
  const warnings = [];
  const whitespaceCount = populated.filter((value) => String(value) !== String(value).trim()).length;
  const normalizedGroups = new Map();
  for (const value of populated) {
    const normalized = String(value).trim().toLowerCase();
    const originals = normalizedGroups.get(normalized) || new Set();
    originals.add(String(value));
    normalizedGroups.set(normalized, originals);
  }
  const caseCollisionCount = [...normalizedGroups.values()].filter((originals) => originals.size > 1).length;
  if (whitespaceCount) warnings.push(warning('whitespace_inconsistency', `${whitespaceCount} values contain leading or trailing whitespace.`, { count: whitespaceCount }));
  if (caseCollisionCount) warnings.push(warning('case_inconsistency', `${caseCollisionCount} normalized value group(s) differ by case or whitespace.`, { groupCount: caseCollisionCount }));

  let invalidCount = 0;
  if (['integer', 'decimal'].includes(logicalType)) invalidCount = populated.filter((value) => !isDecimal(value)).length;
  else if (logicalType === 'boolean') invalidCount = populated.filter((value) => !isBoolean(value)).length;
  else if (['date', 'datetime'].includes(logicalType)) invalidCount = populated.filter((value) => !parseDateValue(value)).length;
  else if (logicalType === 'mixed') {
    const numericLike = populated.filter(isDecimal).length;
    const dateLike = populated.filter((value) => parseDateValue(value)).length;
    invalidCount = Math.max(0, Math.min(populated.length - numericLike, populated.length - dateLike));
  }
  if (invalidCount) warnings.push(warning('invalid_values', `${invalidCount} non-null value(s) do not match the inferred type.`, { count: invalidCount }));
  if (sensitive) warnings.push(warning('potential_sensitive_data', 'Potential sensitive values were detected; samples are omitted from AI context.'));

  const uniqueRatio = populated.length ? distinctValues(values) / populated.length : 0;
  const potentialIdentifier = IDENTIFIER_HEADER_PATTERN.test(name) || (uniqueRatio === 1 && populated.length > 1);
  if (potentialIdentifier) warnings.push(warning('potential_identifier', 'This column appears to identify rows uniquely.', { uniqueRatio }));

  const sampleValues = sensitive ? [] : populated.slice(0, DEFAULT_SAMPLE_VALUES);
  return {
    name,
    type: legacyType(logicalType),
    logicalType,
    nullCount: values.length - populated.length,
    nullPercentage: values.length ? ((values.length - populated.length) / values.length) * 100 : 0,
    distinctCount: distinctValues(values),
    duplicateCount: Math.max(0, populated.length - distinctValues(values)),
    invalidCount,
    sampleValues,
    statistics: safeStatistics(populated, logicalType),
    potentialIdentifier,
    potentialSensitiveData: sensitive,
    warnings,
  };
}

function duplicateRowCount(rows) {
  const groups = new Map();
  for (const row of rows) {
    const key = JSON.stringify(row);
    groups.set(key, (groups.get(key) || 0) + 1);
  }
  return [...groups.values()].reduce((total, count) => total + Math.max(0, count - 1), 0);
}

function decodeUtf8(buffer) {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer);
  } catch (error) {
    throw new Error('The CSV is not valid UTF-8 text.');
  }
}

function validateFile(file, options) {
  const extension = file.name.toLowerCase().split('.').pop();
  const maxUploadBytes = options.maxUploadBytes ?? DEFAULT_MAX_FILE_SIZE;
  if (extension !== 'csv') throw new Error('Choose a CSV file.');
  if (file.type && !ALLOWED_MIME_TYPES.has(file.type.toLowerCase())) throw new Error('The selected file is not a supported CSV MIME type.');
  if (file.size > maxUploadBytes) throw new Error(`Files must be ${Math.round(maxUploadBytes / 1024 / 1024)} MB or smaller.`);
  if (file.size === 0) throw new Error('The selected file is empty.');
}

async function profileFile(file, options = {}) {
  validateFile(file, options);
  const text = decodeUtf8(await file.arrayBuffer());
  const delimiter = detectDelimiter(text);
  const records = parseCsv(text, delimiter);
  const maxColumns = options.maxColumns ?? DEFAULT_MAX_COLUMNS;
  const maxRows = options.maxRows ?? DEFAULT_MAX_ROWS;
  if (records.length < 2) throw new Error('Add a header row and at least one data row.');
  const headers = records[0].map((header) => header.trim());
  if (headers.length > maxColumns) throw new Error(`Files may contain at most ${maxColumns} columns.`);
  if (headers.some((header) => !header)) throw new Error('Every CSV column needs a name.');
  if (new Set(headers.map((header) => header.toLowerCase())).size !== headers.length) throw new Error('CSV column names must be unique.');
  const rows = records.slice(1);
  if (rows.length > maxRows) throw new Error(`Files may contain at most ${maxRows.toLocaleString()} rows.`);
  const normalizedRows = rows.map((record) => headers.map((_, index) => record[index] ?? ''));
  const columns = headers.map((name, index) => columnProfile(name, normalizedRows.map((row) => row[index])));
  const warnings = columns.flatMap((column) => column.warnings);
  const duplicateRows = duplicateRowCount(normalizedRows);
  if (duplicateRows) warnings.push(warning('duplicate_rows', `${duplicateRows} duplicate row(s) were found.`, { duplicateRowCount: duplicateRows }));
  if (!normalizedRows.length) warnings.push(warning('empty_table', 'The CSV contains headers but no data rows.'));
  return {
    name: file.name,
    sourceFile: file.name,
    sizeBytes: file.size,
    size: file.size,
    rowCount: normalizedRows.length,
    columnCount: headers.length,
    delimiter,
    delimiterName: delimiter === '\t' ? 'Tab' : delimiter === ';' ? 'Semicolon' : 'Comma',
    status: warnings.length ? 'warning' : 'success',
    columns,
    duplicateRowCount: duplicateRows,
    previewRows: normalizedRows.slice(0, options.previewRows ?? DEFAULT_PREVIEW_ROWS),
    rows: normalizedRows.slice(0, options.previewRows ?? DEFAULT_PREVIEW_ROWS),
    truncated: normalizedRows.length > (options.previewRows ?? DEFAULT_PREVIEW_ROWS),
    warnings,
    errors: [],
  };
}

function buildAiContext(files, relationshipCandidates = []) {
  return {
    tables: files.map((file) => ({
      name: file.name,
      rowCount: file.rowCount,
      columnCount: file.columnCount,
      sourceFile: file.name,
    })),
    columns: files.flatMap((file) => file.columns.map((column) => ({
      table: file.name,
      name: column.name,
      logicalType: column.logicalType,
      nullCount: column.nullCount,
      nullPercentage: column.nullPercentage,
      distinctCount: column.distinctCount,
      invalidCount: column.invalidCount,
      statistics: column.statistics,
      sampleValues: column.potentialSensitiveData ? [] : column.sampleValues.slice(0, 3),
    }))),
    statistics: files.map((file) => ({ name: file.name, rowCount: file.rowCount, duplicateRowCount: file.duplicateRowCount })),
    warnings: files.flatMap((file) => file.warnings.map((item) => ({ table: file.name, ...item }))),
    relationshipCandidates,
  };
}

export async function profileFiles(files, options = {}) {
  const list = [...files].filter(Boolean);
  const datasetId = `dataset-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
  const profiledFiles = [];
  const errors = [];
  for (const file of list) {
    try {
      profiledFiles.push(await profileFile(file, options));
    } catch (error) {
      errors.push({ fileName: file.name, code: 'profile_failed', message: error.message });
      profiledFiles.push({
        name: file.name,
        sourceFile: file.name,
        sizeBytes: file.size,
        size: file.size,
        rowCount: 0,
        columnCount: 0,
        delimiter: null,
        status: 'error',
        columns: [],
        duplicateRowCount: 0,
        previewRows: [],
        rows: [],
        truncated: false,
        warnings: [],
        errors: [{ code: 'profile_failed', message: error.message }],
      });
    }
  }
  const warnings = profiledFiles.flatMap((file) => file.warnings || []);
  const hasErrors = errors.length > 0;
  const result = {
    status: hasErrors ? 'error' : warnings.length ? 'warning' : 'success',
    datasetId,
    files: profiledFiles,
    summary: {
      fileCount: profiledFiles.length,
      totalRows: profiledFiles.reduce((total, file) => total + file.rowCount, 0),
      totalColumns: profiledFiles.reduce((total, file) => total + file.columnCount, 0),
      warningCount: warnings.length,
      errorCount: errors.length,
    },
    relationshipCandidates: [],
    aiContext: buildAiContext(profiledFiles),
    errors,
  };
  return result;
}

export async function inspectDataset(file, options = {}) {
  const extension = file.name.toLowerCase().split('.').pop();
  if (['db', 'sqlite'].includes(extension)) {
    const maxUploadBytes = options.maxUploadBytes ?? DEFAULT_MAX_FILE_SIZE;
    if (file.size > maxUploadBytes) throw new Error(`Files must be ${Math.round(maxUploadBytes / 1024 / 1024)} MB or smaller.`);
    if (file.size === 0) throw new Error('The selected file is empty.');
    return { kind: 'sqlite', name: file.name, fileName: file.name, size: file.size, columns: [], rows: [], rowCount: null, truncated: false, delimiter: null };
  }
  const profile = await profileFile(file, options);
  return {
    kind: 'csv',
    name: profile.name,
    fileName: profile.name,
    size: profile.size,
    columns: profile.columns.map(({ name, type, nullCount, logicalType, nullPercentage, distinctCount, invalidCount, statistics, warnings }) => ({ name, type, logicalType, nullCount, nullPercentage, distinctCount, invalidCount, statistics, warnings })),
    rows: profile.rows,
    rowCount: profile.rowCount,
    truncated: profile.truncated,
    delimiter: profile.delimiter,
    profile,
  };
}

export function canAnalyzeDataset(dataset, liveBackend = false, profile = null) {
  if (dataset?.kind === 'sample') return true;
  return Boolean(liveBackend || ['success', 'warning'].includes(profile?.status));
}

export function createDatasetRequest(dataset) {
  if (dataset.kind === 'sample') return { databaseConfig: { type: 'sample', database: 'sample_retail' }, datasetOptions: null };
  const files = dataset.files || (dataset.fileName ? [dataset] : []);
  const datasetOptions = {
    schema: { columns: (dataset.columns || []).map(({ name, type, nullCount }) => ({ name, type, nullCount })) },
    file: { fileName: dataset.fileName, size: dataset.size, delimiter: dataset.delimiter, rowCount: dataset.rowCount },
  };
  if (files.length > 1 || dataset.profile) {
    datasetOptions.files = files.map((file) => ({ fileName: file.name || file.fileName, size: file.sizeBytes ?? file.size, delimiter: file.delimiter, rowCount: file.rowCount }));
  }
  if (dataset.profile?.aiContext) datasetOptions.aiContext = dataset.profile.aiContext;
  return {
    databaseConfig: { type: dataset.kind, database: dataset.datasetId || dataset.fileName },
    datasetOptions,
  };
}
