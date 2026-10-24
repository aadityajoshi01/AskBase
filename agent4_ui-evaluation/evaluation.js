import evaluationFixture from './config/evaluation.fixtures.json' with { type: 'json' };

export const evaluationScenarios = evaluationFixture.scenarios;
export const evaluationMode = evaluationFixture;

export function calculateMetrics(scenarios) {
  const executable = scenarios.filter((scenario) => scenario.executable);
  const withResults = scenarios.filter((scenario) => scenario.resultCorrect !== null);
  const ratio = (count, total) => total ? Math.round((count / total) * 1000) / 10 : 0;

  return {
    execution: {
      value: ratio(executable.filter((scenario) => scenario.executionSucceeded).length, executable.length),
      numerator: executable.filter((scenario) => scenario.executionSucceeded).length,
      denominator: executable.length,
    },
    results: {
      value: ratio(withResults.filter((scenario) => scenario.resultCorrect).length, withResults.length),
      numerator: withResults.filter((scenario) => scenario.resultCorrect).length,
      denominator: withResults.length,
    },
    answers: {
      value: ratio(scenarios.filter((scenario) => scenario.answerCorrect).length, scenarios.length),
      numerator: scenarios.filter((scenario) => scenario.answerCorrect).length,
      denominator: scenarios.length,
    },
    safetyFailures: scenarios.filter((scenario) => scenario.safetyFailure).length,
    averageLatency: scenarios.length ? Math.round(scenarios.reduce((total, scenario) => total + scenario.latency, 0) / scenarios.length) : 0,
    passed: scenarios.filter((scenario) => scenario.passed).length,
    total: scenarios.length,
  };
}

export function filterScenarios(scenarios, { status = 'all', category = 'all', query = '' } = {}) {
  const normalizedQuery = query.trim().toLowerCase();
  return scenarios.filter((scenario) => {
    const matchesStatus = status === 'all' || (status === 'passed' ? scenario.passed : !scenario.passed);
    const matchesCategory = category === 'all' || scenario.category === category;
    const matchesQuery = !normalizedQuery || `${scenario.name} ${scenario.id}`.toLowerCase().includes(normalizedQuery);
    return matchesStatus && matchesCategory && matchesQuery;
  });
}
