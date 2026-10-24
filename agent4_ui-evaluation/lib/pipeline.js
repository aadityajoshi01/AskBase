export async function runPipeline({ stages, context = {}, execute, eventBus, logger, requestId }) {
  if (!stages.length) throw new Error('Pipeline needs at least one stage.');
  const timeline = [];
  let currentContext = context;
  eventBus?.emit('pipeline:start', { requestId, stages });

  for (const [index, stage] of stages.entries()) {
    const started = performance.now();
    eventBus?.emit('stage:start', { requestId, stage, index });
    try {
      const result = await execute(stage, currentContext, index);
      if (result !== undefined) currentContext = result;
      const durationMs = Math.round((performance.now() - started) * 10) / 10;
      const entry = { ...stage, status: 'complete', durationMs };
      timeline.push(entry);
      eventBus?.emit('stage:complete', { requestId, stage, index, durationMs });
      logger?.log({ stage: stage.id, requestId, durationMs, message: `${stage.label} complete` });
    } catch (error) {
      const durationMs = Math.round((performance.now() - started) * 10) / 10;
      timeline.push({ ...stage, status: 'error', durationMs });
      eventBus?.emit('stage:error', { requestId, stage, index, durationMs, error });
      logger?.log({ level: 'error', stage: stage.id, requestId, durationMs, message: error.message });
      throw error;
    }
  }

  eventBus?.emit('pipeline:complete', { requestId, timeline });
  return { context: currentContext, timeline };
}
