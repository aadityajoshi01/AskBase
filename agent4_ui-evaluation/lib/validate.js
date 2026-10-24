function matchesType(value, type) {
  if (type === 'array') return Array.isArray(value);
  if (type === 'object') return value !== null && typeof value === 'object' && !Array.isArray(value);
  if (type === 'null') return value === null;
  if (type === 'integer') return Number.isInteger(value);
  if (type === 'number') return typeof value === 'number' && Number.isFinite(value);
  return typeof value === type;
}

export function validateSchema(value, schema, path = '$') {
  const errors = [];
  if (schema.type) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((type) => matchesType(value, type))) {
      errors.push(`${path}: expected ${types.join(' or ')}`);
      return errors;
    }
  }
  if (schema.enum && !schema.enum.some((option) => Object.is(option, value))) {
    errors.push(`${path}: expected one of ${schema.enum.join(', ')}`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) errors.push(`${path}: needs at least ${schema.minItems} items`);
    if (schema.maxItems !== undefined && value.length > schema.maxItems) errors.push(`${path}: allows at most ${schema.maxItems} items`);
    if (schema.items) value.forEach((item, index) => errors.push(...validateSchema(item, schema.items, `${path}[${index}]`)));
  }
  if (matchesType(value, 'object')) {
    for (const field of schema.required || []) {
      if (!(field in value)) errors.push(`${path}.${field}: required`);
    }
    for (const [field, childSchema] of Object.entries(schema.properties || {})) {
      if (value[field] !== undefined) errors.push(...validateSchema(value[field], childSchema, `${path}.${field}`));
    }
  }
  return errors;
}

export function assertValid(value, schema, label = 'Response') {
  const errors = validateSchema(value, schema);
  if (errors.length) {
    const error = new Error(`${label} is malformed: ${errors.join('; ')}`);
    error.name = 'ValidationError';
    error.details = errors;
    throw error;
  }
  return value;
}
