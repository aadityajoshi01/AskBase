import scenarioDocument from './config/scenarios.json' with { type: 'json' };
import analysisResponseSchema from './schemas/analysis-response.schema.json' with { type: 'json' };
import { assertValid } from './lib/validate.js';

const wait = (milliseconds, signal) => new Promise((resolve, reject) => {
  const timeout = setTimeout(resolve, milliseconds);
  signal?.addEventListener('abort', () => {
    clearTimeout(timeout);
    reject(new DOMException('Analysis cancelled', 'AbortError'));
  }, { once: true });
});

export const analysisScenarios = scenarioDocument.scenarios.map(({ id, label }) => ({ value: id, label }));

export async function runAnalysis(question, databaseConfig, options = {}) {
  if (!question.trim()) throw new Error('Enter a question before running analysis.');
  if (!databaseConfig.database) throw new Error('Select a database before running analysis.');

  await wait(options.delay ?? 450, options.signal);
  const scenario = scenarioDocument.scenarios.find(({ id }) => id === (options.scenario || 'success')) || scenarioDocument.scenarios[0];
  const result = structuredClone(scenario.response);
  const profileContext = options.aiContext || options.dataset?.aiContext;
  if (profileContext) {
    result.meta = {
      ...result.meta,
      mode: 'mock',
      aiContextReceived: true,
      tableCount: profileContext.tables?.length || 0,
      columnCount: profileContext.columns?.length || 0,
    };
    result.warnings = [
      'Mock analysis only: no live AI request, SQL execution, or verified result was performed.',
      ...(result.warnings || []),
    ];
    result.verification = {
      ...result.verification,
      passed: false,
      message: 'Profile context was received by the mock analysis boundary; live analysis is not connected.',
    };
  }
  assertValid(result, analysisResponseSchema, 'Analysis response');
  result.row_count = result.rowCount;
  return result;
}
