import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { activeUser, identityHeaders, setActiveUser } from '../lib/identity.js';
import { fetchUserDirectory, runAnalysis, uploadDatasetCsv } from '../analysis-service.js';

const scenarios = JSON.parse(
  readFileSync(new URL('../config/scenarios.json', import.meta.url), 'utf8')
).scenarios;
const database = { database: 'sample_retail' };

function withStubServer(handler, run) {
  return new Promise((resolve, reject) => {
    const stub = createServer((request, response) => {
      let raw = '';
      request.on('data', (chunk) => { raw += chunk; });
      request.on('end', () => handler(JSON.parse(raw || '{}'), request, response));
    });
    stub.listen(0, '127.0.0.1', async () => {
      const previous = process.env.ASKBASE_API_BASE;
      process.env.ASKBASE_API_BASE = `http://127.0.0.1:${stub.address().port}`;
      try {
        resolve(await run());
      } catch (error) {
        reject(error);
      } finally {
        if (previous === undefined) delete process.env.ASKBASE_API_BASE;
        else process.env.ASKBASE_API_BASE = previous;
        stub.close();
      }
    });
  });
}

function json(response, body, statusCode = 200) {
  response.writeHead(statusCode, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(body));
}

test('an anonymous caller sends no identity header', () => {
  setActiveUser('');
  assert.equal(activeUser(), '');
  assert.deepEqual(identityHeaders({ 'Content-Type': 'application/json' }),
    { 'Content-Type': 'application/json' });
});

test('the signed-in name travels as a header, and never as a role claim', () => {
  setActiveUser('amit');
  assert.equal(activeUser(), 'amit');
  assert.equal(identityHeaders()['X-AskBase-User'], 'amit');
  // choosing "admin" in the switcher is only a name: the server maps it to a role
  setActiveUser('admin');
  assert.equal(identityHeaders()['X-AskBase-User'], 'admin');
  setActiveUser('');
  assert.equal(activeUser(), '');
});

test('every gateway call carries the identity', async () => {
  setActiveUser('rita');
  const seen = [];
  try {
    await withStubServer((body, request, response) => {
      seen.push([request.url, request.headers['x-askbase-user']]);
      if (request.url.startsWith('/api/datasets/upload')) {
        return json(response, { status: 'ok', database: { id: 'x_1', label: 'x.csv (upload)',
          table: 'x', row_count: 1, columns: [], source: 'upload' }, schema: null, profile: null }, 201);
      }
      const body2 = structuredClone(scenarios.find(({ id }) => id === 'success').response);
      return json(response, body2);
    }, async () => {
      await runAnalysis('Revenue by category', database, {});
      await uploadDatasetCsv('x.csv', 'id\n1');
    });
  } finally {
    setActiveUser('');
  }
  assert.deepEqual(seen.map(([path]) => path.split('?')[0]).sort(),
    ['/api/analyze', '/api/datasets/upload']);
  assert.deepEqual(seen.map(([, user]) => user), ['rita', 'rita']);
});

test('the role switcher is built from the server directory, not a hard-coded list', async () => {
  let seenUser = null;
  const directory = await withStubServer((body, request, response) => {
    seenUser = request.headers['x-askbase-user'] || null;
    json(response, { users: [{ user: 'amit', role: 'viewer', label: 'East only' }],
      you: { user: 'amit', role: 'viewer' }, default_role: 'viewer', role_limits: {} });
  }, () => {
    setActiveUser('amit');
    return fetchUserDirectory();
  });
  assert.equal(seenUser, 'amit');
  assert.equal(directory.users[0].role, 'viewer');
});

test('a refused role surfaces the gateway reason instead of a silent success', async () => {
  await assert.rejects(
    withStubServer((body, request, response) => {
      json(response, { status: 'error', message: 'role viewer may not register datasets',
        role: 'viewer' }, 403);
    }, () => uploadDatasetCsv('x.csv', 'id\n1')),
    /may not register datasets/);
});
