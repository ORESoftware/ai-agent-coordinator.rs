import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { assertApplyGate, digest, Failure, GitHubClient, NAMES, provision, validateManifest } from './bootstrap.mjs';
import { MANIFEST_SHA256, run } from './run.mjs';

const raw = await readFile(new URL('./manifest.json', import.meta.url), 'utf8');
const manifest = validateManifest(raw, MANIFEST_SHA256);
const sha = 'a'.repeat(40);
const noWait = async () => {};
const env = { GITHUB_REPOSITORY: 'ORESoftware/ai-agent-coordinator.rs', GITHUB_EVENT_NAME: 'push',
  GITHUB_REF: 'refs/heads/main', GITHUB_SHA: sha, ORES_LEGAL_CREATE_ENABLED: 'true',
  ORES_LEGAL_CONFIRM: `ores-legal@${MANIFEST_SHA256}`, ORES_LEGAL_ADMIN_TOKEN: 'synthetic-test-value' };
function repo(name, id) {
  return { id, name, full_name: `ores-legal/${name}`, owner: { login: 'ores-legal', id: 42 },
    private: true, visibility: 'private', archived: false, default_branch: 'main',
    html_url: `https://github.com/ores-legal/${name}` };
}
class FakeApi {
  calls = [];
  repos = new Map();
  issues = new Map();
  hook;
  constructor(existing = false) {
    if (existing) { NAMES.forEach((name, i) => this.repos.set(name, repo(name, 100 + i))); }
  }
  async request(method, route, body) {
    this.calls.push({ method, route, body });
    if (this.hook) {
      const overridden = await this.hook(method, route, body);
      if (overridden !== undefined) { return overridden; }
    }
    if (method === 'GET' && route === '/orgs/ores-legal') { return { login: 'ores-legal', id: 42, type: 'Organization' }; }
    if (method === 'POST' && route === '/orgs/ores-legal/repos') {
      const value = repo(body.name, 100 + NAMES.indexOf(body.name));
      this.repos.set(body.name, value);
      return value;
    }
    const match = route.match(/^\/repos\/ores-legal\/([^/]+)(.*)$/);
    assert.ok(match, route);
    const [, name, suffix] = match;
    if (method === 'GET' && suffix === '') {
      if (!this.repos.has(name)) { throw new Failure('github-request-failed', 404); }
      return this.repos.get(name);
    }
    if (method === 'GET' && suffix === '/commits/main') { return { sha }; }
    if (method === 'GET' && suffix.startsWith('/issues?')) { return this.issues.get(name) ?? []; }
    if (method === 'POST' && suffix === '/issues') {
      const issue = { number: 1, html_url: `https://github.com/ores-legal/${name}/issues/1`, ...body };
      this.issues.set(name, [issue]);
      return issue;
    }
    throw new Error(`Unexpected test request ${method} ${route}`);
  }
  writes() { return this.calls.filter((call) => call.method === 'POST'); }
}
function mutateManifest(change) {
  const altered = structuredClone(manifest);
  change(altered);
  const text = JSON.stringify(altered);
  return () => validateManifest(text, digest(text));
}

test('sealed manifest contains precisely the ordered sixteen private repositories', () => {
  assert.equal(manifest.repositories.length, 16);
  assert.deepEqual(manifest.repositories.map((r) => r.name), NAMES);
  assert.equal(digest(raw), MANIFEST_SHA256);
});
for (const [name, change] of [
  ['other owner', (m) => { m.organization = 'other'; }],
  ['public visibility', (m) => { m.visibility = 'public'; }],
  ['extra repository', (m) => { m.repositories.push(m.repositories[0]); }],
  ['missing repository', (m) => { m.repositories.pop(); }],
  ['duplicate name', (m) => { m.repositories[1].name = m.repositories[0].name; }],
  ['path traversal', (m) => { m.repositories[0].name = '../other'; }],
  ['unknown property', (m) => { m.token = 'not-allowed'; }],
  ['item unknown property', (m) => { m.repositories[0].url = 'https://invalid.example'; }],
  ['multiline description', (m) => { m.repositories[0].purpose = 'x\ny'; }],
]) {
  test(`manifest rejects ${name}`, () => { assert.throws(mutateManifest(change), Failure); });
}
test('manifest rejects altered bytes, missing seal and malformed JSON', () => {
  assert.throws(() => validateManifest(`${raw}\n`, MANIFEST_SHA256), Failure);
  assert.throws(() => validateManifest(raw, ''), Failure);
  assert.throws(() => validateManifest('{', digest('{')), Failure);
});
test('plan is the default and does not need or expose credentials', async () => {
  const result = await run({ ORES_LEGAL_ADMIN_TOKEN: 'synthetic-private-sentinel' });
  assert.equal(result.status, 'plan-only');
  assert.ok(!JSON.stringify(result).includes('synthetic-private-sentinel'));
});
test('invalid mode fails before network access', async () => {
  await assert.rejects(run({ ORES_LEGAL_MODE: 'execute' }), Failure);
});
test('apply gate requires the reviewed main push and every confirmation', () => {
  assert.doesNotThrow(() => assertApplyGate(env, MANIFEST_SHA256));
  for (const key of Object.keys(env)) {
    assert.throws(() => assertApplyGate({ ...env, [key]: '' }, MANIFEST_SHA256), Failure);
  }
  for (const patch of [{ GITHUB_EVENT_NAME: 'pull_request' }, { GITHUB_EVENT_NAME: 'pull_request_target' },
    { GITHUB_REF: 'refs/heads/feature' }, { GITHUB_REPOSITORY: 'other/repository' }, { GITHUB_SHA: 'main' }]) {
    assert.throws(() => assertApplyGate({ ...env, ...patch }, MANIFEST_SHA256), Failure);
  }
});
test('HTTP client rejects foreign routes and destructive methods before fetch', async () => {
  let calls = 0;
  const api = new GitHubClient('synthetic', async () => { calls += 1; });
  for (const [method, route] of [['DELETE', '/repos/ores-legal/ores-legal-docs'],
    ['PATCH', '/repos/ores-legal/ores-legal-docs'], ['GET', '/repos/other/repo'],
    ['POST', '/orgs/other/repos'], ['GET', 'https://invalid.example']]) {
    await assert.rejects(api.request(method, route), Failure);
  }
  await assert.rejects(api.request('POST', '/orgs/ores-legal/repos', { name: NAMES[0], private: false }), Failure);
  assert.equal(calls, 0);
});
test('HTTP client uses timeout, no redirects, private token field and sanitized errors', async () => {
  const api = new GitHubClient('synthetic-private-sentinel', async (url, options) => {
    assert.equal(url, 'https://api.github.com/orgs/ores-legal');
    assert.equal(options.redirect, 'error');
    assert.ok(options.signal);
    throw new Error('synthetic-private-sentinel');
  });
  assert.ok(!JSON.stringify(api).includes('synthetic-private-sentinel'));
  await assert.rejects(api.request('GET', '/orgs/ores-legal'), { message: 'transport-failure' });
});
test('HTTP error response bodies are never read or echoed', async () => {
  const api = new GitHubClient('synthetic', async () => ({ ok: false, status: 403,
    json() { throw new Error('must not read response body'); } }));
  await assert.rejects(api.request('GET', '/orgs/ores-legal'), { code: 'github-request-failed', status: 403 });
});
test('malformed successful HTTP response fails closed', async () => {
  const api = new GitHubClient('synthetic', async () => ({ ok: true, async json() { throw new Error('bad'); } }));
  await assert.rejects(api.request('GET', '/orgs/ores-legal'), { code: 'invalid-github-response' });
});
test('creates sixteen repositories, verifies commits and records sixteen roadmap issues', async () => {
  const api = new FakeApi();
  const snapshots = [];
  const report = await provision(manifest, api, async (r) => snapshots.push(structuredClone(r)), noWait);
  assert.equal(report.status, 'complete');
  assert.equal(report.repositories.length, 16);
  assert.equal(api.writes().length, 32);
  assert.ok(report.repositories.every((r) => r.status === 'verified' && r.repository_verified && r.commit === sha));
  assert.equal(snapshots.length, 33);
  for (const call of api.writes().filter((c) => c.route === '/orgs/ores-legal/repos')) {
    assert.equal(call.body.private, true);
    assert.equal(call.body.auto_init, true);
    assert.equal(call.body.allow_rebase_merge, false);
  }
});
test('replay preserves existing repositories and never duplicates roadmap issues', async () => {
  const api = new FakeApi();
  await provision(manifest, api, undefined, noWait);
  api.calls = [];
  const report = await provision(manifest, api, undefined, noWait);
  assert.equal(report.status, 'complete');
  assert.equal(api.writes().length, 0);
  assert.ok(report.repositories.every((r) => r.repository_action === 'existing-unchanged'));
});
test('organization mismatch blocks every mutation', async () => {
  const api = new FakeApi();
  api.hook = async () => ({ login: 'other', type: 'Organization', id: 42 });
  await assert.rejects(provision(manifest, api, undefined, noWait), { code: 'organization-identity-drift' });
  assert.equal(api.writes().length, 0);
});
for (const status of [401, 403, 429, 500]) {
  test(`repository read ${status} is never classified as missing`, async () => {
    const api = new FakeApi(true);
    api.hook = async (method, route) => {
      if (route === `/repos/ores-legal/${NAMES[0]}`) { throw new Failure('github-request-failed', status); }
    };
    const report = await provision(manifest, api, undefined, noWait);
    assert.equal(report.status, 'blocked');
    assert.equal(report.repositories[0].repository_action, 'none');
    assert.equal(report.repositories[0].http_status, status);
    assert.equal(api.writes().filter((c) => c.route === '/orgs/ores-legal/repos').length, 0);
  });
}
for (const [name, change] of [
  ['public repository', (r) => { r.private = false; r.visibility = 'public'; }],
  ['foreign owner', (r) => { r.owner = { login: 'other', id: 7 }; }],
  ['renamed repository', (r) => { r.full_name = 'ores-legal/other'; }],
  ['wrong default branch', (r) => { r.default_branch = 'master'; }],
  ['archived repository', (r) => { r.archived = true; }],
]) {
  test(`preserves and blocks ${name}`, async () => {
    const api = new FakeApi(true);
    change(api.repos.get(NAMES[0]));
    const report = await provision(manifest, api, undefined, noWait);
    assert.equal(report.repositories[0].status, 'blocked');
    assert.equal(report.repositories[0].error, 'repository-identity-or-policy-drift');
    assert.ok(!api.writes().some((c) => c.route === `/repos/ores-legal/${NAMES[0]}/issues`));
  });
}
test('ambiguous create is reconciled without repeated POST', async () => {
  for (const status of [null, 422, 503]) {
    const api = new FakeApi();
    api.hook = async (method, route, body) => {
      if (method === 'POST' && route === '/orgs/ores-legal/repos' && body.name === NAMES[0]) {
        api.repos.set(NAMES[0], repo(NAMES[0], 100));
        throw new Failure('ambiguous', status);
      }
    };
    const report = await provision(manifest, api, undefined, noWait);
    assert.equal(report.status, 'complete');
    assert.equal(report.repositories[0].repository_action, 'reconciled-after-ambiguous-create');
    assert.equal(api.writes().filter((c) => c.body?.name === NAMES[0]).length, 1);
  }
});
test('unresolved ambiguous create remains blocked after bounded reads', async () => {
  const api = new FakeApi();
  let waits = 0;
  api.hook = async (method, route, body) => {
    if (method === 'POST' && route === '/orgs/ores-legal/repos' && body.name === NAMES[0]) { throw new Failure('transport-failure'); }
  };
  const report = await provision(manifest, api, undefined, async () => { waits += 1; });
  assert.equal(report.repositories[0].error, 'repository-not-yet-readable');
  assert.equal(api.writes().filter((c) => c.body?.name === NAMES[0]).length, 1);
  assert.equal(waits, 4);
});
test('post-create identity change is blocked before issue creation', async () => {
  const api = new FakeApi();
  api.hook = async (method, route) => {
    if (method === 'GET' && route === `/repos/ores-legal/${NAMES[0]}` && api.repos.has(NAMES[0])) {
      return repo(NAMES[0], 999);
    }
  };
  const report = await provision(manifest, api, undefined, noWait);
  assert.equal(report.repositories[0].error, 'repository-id-changed');
  assert.equal(report.repositories[0].repository_action, 'created');
});
test('commit initialization retries are bounded and eventually verify', async () => {
  const api = new FakeApi(true);
  let count = 0;
  api.hook = async (method, route) => {
    if (route === `/repos/ores-legal/${NAMES[0]}/commits/main` && count++ < 2) { throw new Failure('initializing', 409); }
  };
  const report = await provision(manifest, api, undefined, noWait);
  assert.equal(report.status, 'complete');
  assert.equal(count, 3);
});
test('invalid commit does not become a success receipt', async () => {
  const api = new FakeApi(true);
  api.hook = async (method, route) => {
    if (route === `/repos/ores-legal/${NAMES[0]}/commits/main`) { return { sha: 'main' }; }
  };
  const report = await provision(manifest, api, undefined, noWait);
  assert.equal(report.repositories[0].error, 'invalid-commit-receipt');
  assert.equal(report.repositories[0].repository_verified, undefined);
});
test('duplicate roadmap markers fail closed without adding another issue', async () => {
  const api = new FakeApi(true);
  const issues = [{ body: '<!-- ores-legal-bootstrap:v1 -->' }, { body: '<!-- ores-legal-bootstrap:v1 -->' }];
  api.issues.set(NAMES[0], issues);
  const report = await provision(manifest, api, undefined, noWait);
  assert.equal(report.repositories[0].error, 'duplicate-roadmap-issues');
  assert.equal(api.issues.get(NAMES[0]).length, 2);
});
test('issue scan limits fail closed rather than creating a potential duplicate', async () => {
  const api = new FakeApi(true);
  api.issues.set(NAMES[0], Array.from({ length: 100 }, () => ({ body: 'unrelated' })));
  const report = await provision(manifest, api, undefined, noWait);
  assert.equal(report.repositories[0].error, 'issue-scan-limit');
  assert.equal(api.calls.filter((c) => c.route.startsWith(`/repos/ores-legal/${NAMES[0]}/issues?`)).length, 10);
});
test('ambiguous issue creation reconciles its marker without another mutation', async () => {
  const api = new FakeApi(true);
  api.hook = async (method, route, body) => {
    if (method === 'POST' && route === `/repos/ores-legal/${NAMES[0]}/issues`) {
      api.issues.set(NAMES[0], [{ number: 1, html_url: `https://github.com/ores-legal/${NAMES[0]}/issues/1`, ...body }]);
      throw new Failure('transport-failure');
    }
  };
  const report = await provision(manifest, api, undefined, noWait);
  assert.equal(report.status, 'complete');
  assert.equal(api.writes().filter((c) => c.route === `/repos/ores-legal/${NAMES[0]}/issues`).length, 1);
});
test('issue permission failure retains the independently verified repository receipt', async () => {
  const api = new FakeApi(true);
  api.hook = async (method, route) => {
    if (method === 'POST' && route === `/repos/ores-legal/${NAMES[0]}/issues`) { throw new Failure('github-request-failed', 403); }
  };
  const report = await provision(manifest, api, undefined, noWait);
  assert.equal(report.repositories[0].repository_verified, true);
  assert.equal(report.repositories[0].status, 'blocked');
  assert.equal(report.repositories[0].http_status, 403);
});
test('unexpected failure diagnostics never serialize raw exception messages', async () => {
  const api = new FakeApi(true);
  api.hook = async (method, route) => {
    if (route === `/repos/ores-legal/${NAMES[0]}`) { throw new Error('synthetic-private-sentinel'); }
  };
  const report = await provision(manifest, api, undefined, noWait);
  assert.equal(report.repositories[0].error, 'unexpected-failure');
  assert.ok(!JSON.stringify(report).includes('synthetic-private-sentinel'));
});
