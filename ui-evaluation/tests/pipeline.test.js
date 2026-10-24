import test from 'node:test';
import assert from 'node:assert/strict';
import { createEventBus } from '../lib/event-bus.js';
import { createLogger } from '../lib/logger.js';
import { runPipeline } from '../lib/pipeline.js';

const stages = [{ id: 'one', label: 'One' }, { id: 'two', label: 'Two' }, { id: 'three', label: 'Three' }];

test('pipeline executes stages in order and reports timeline', async () => {
  const calls = [];
  const events = [];
  const eventBus = createEventBus();
  const logger = createLogger();
  eventBus.on('stage:complete', (event) => events.push(event.stage.id));
  const result = await runPipeline({
    stages,
    context: { value: 1 },
    requestId: 7,
    eventBus,
    logger,
    async execute(stage, context) {
      calls.push(stage.id);
      return { value: context.value + 1 };
    },
  });
  assert.deepEqual(calls, ['one', 'two', 'three']);
  assert.deepEqual(events, calls);
  assert.equal(result.context.value, 4);
  assert.deepEqual(result.timeline.map((stage) => stage.status), ['complete', 'complete', 'complete']);
  assert.equal(logger.getLogs().length, 3);
  assert.ok(logger.getLogs().every((log) => log.requestId === 7 && typeof log.durationMs === 'number'));
});

test('pipeline emits an error and stops subsequent stages', async () => {
  const eventBus = createEventBus();
  let failure;
  eventBus.on('stage:error', (event) => { failure = event; });
  await assert.rejects(runPipeline({ stages, requestId: 2, eventBus, execute: async (stage) => {
    if (stage.id === 'two') throw new Error('Stage failed');
  } }), /Stage failed/);
  assert.equal(failure.stage.id, 'two');
});

test('event bus unsubscribes listeners', () => {
  const bus = createEventBus();
  let calls = 0;
  const off = bus.on('event', () => { calls += 1; });
  bus.emit('event', {});
  off();
  bus.emit('event', {});
  assert.equal(calls, 1);
});

test('logger returns defensive copies and respects its limit', () => {
  const logger = createLogger(2);
  logger.log({ message: 'one' });
  logger.log({ message: 'two' });
  logger.log({ level: 'warn', stage: 'test', message: 'three' });
  const logs = logger.getLogs();
  assert.equal(logs.length, 2);
  assert.equal(logs[1].level, 'warn');
  assert.ok(logs[1].timestamp);
  logs[0].message = 'changed';
  assert.equal(logger.getLogs()[0].message, 'two');
});
