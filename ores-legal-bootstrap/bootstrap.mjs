import { createHash } from 'node:crypto';

export const OWNER = 'ores-legal';
export const NAMES = Object.freeze([
  'ores-legal-interfaces', 'ores-legal-clients', 'ores-legal-monorepo',
  'ores-legal-infra', 'ores-legal-desktop-app.rs', 'ores-legal-flutter',
  'ores-legal-lambdas', 'ores-legal-api-server.rs', 'ores-legal-admin-api-server.rs',
  'ores-legal-web-server.rs', 'ores-legal-admin-web-server.rs', 'ores-legal-docs',
  'ores-legal-mcp-server.rs', 'ores-legal-lib-core', 'ores-legal-orm-core',
  'ores-legal-pub-lib-core',
]);
const MARKER = '<!-- ores-legal-bootstrap:v1 -->';
const SHA = /^[a-f0-9]{40}$/;

export class Failure extends Error {
  constructor(code, status = null) {
    super(code);
    this.code = code;
    this.status = status;
  }
}
export function digest(raw) {
  return createHash('sha256').update(raw).digest('hex');
}
export function validateManifest(raw, expectedDigest) {
  if (!/^[a-f0-9]{64}$/.test(expectedDigest ?? '') || digest(raw) !== expectedDigest) {
    throw new Failure('manifest-digest-mismatch');
  }
  let manifest;
  try { manifest = JSON.parse(raw); } catch { throw new Failure('invalid-manifest'); }
  if (manifest?.organization !== OWNER || manifest.visibility !== 'private'
      || Object.keys(manifest).sort().join(',') !== 'organization,repositories,visibility'
      || !Array.isArray(manifest.repositories) || manifest.repositories.length !== NAMES.length) {
    throw new Failure('invalid-manifest');
  }
  for (const [i, item] of manifest.repositories.entries()) {
    if (!item || Object.keys(item).sort().join(',') !== 'name,purpose'
        || item.name !== NAMES[i] || typeof item.purpose !== 'string'
        || !/^[\x20-\x7e]{1,200}$/.test(item.purpose)) {
      throw new Failure('invalid-manifest');
    }
  }
  return manifest;
}
export function assertApplyGate(env, expectedDigest) {
  if (env.GITHUB_REPOSITORY !== 'ORESoftware/ai-agent-coordinator.rs'
      || env.GITHUB_EVENT_NAME !== 'push' || env.GITHUB_REF !== 'refs/heads/main'
      || !SHA.test(env.GITHUB_SHA ?? '')
      || env.ORES_LEGAL_CREATE_ENABLED !== 'true'
      || env.ORES_LEGAL_CONFIRM !== `${OWNER}@${expectedDigest}`
      || !env.ORES_LEGAL_ADMIN_TOKEN) {
    throw new Failure('apply-gate-closed');
  }
}
function allowed(method, route) {
  if (method === 'GET' && route === `/orgs/${OWNER}`) { return true; }
  if (method === 'POST' && route === `/orgs/${OWNER}/repos`) { return true; }
  return NAMES.some((name) => {
    const root = `/repos/${OWNER}/${name}`;
    return (method === 'GET' && (route === root || route === `${root}/commits/main`
      || new RegExp(`^${root.replaceAll('.', '\\.')}/issues\\?state=all&per_page=100&page=([1-9]|10)$`).test(route)))
      || (method === 'POST' && route === `${root}/issues`);
  });
}
export class GitHubClient {
  #token;
  #fetch;
  constructor(token, fetchImpl = globalThis.fetch) {
    if (!token) { throw new Failure('missing-credential'); }
    this.#token = token;
    this.#fetch = fetchImpl;
  }
  async request(method, route, body) {
    if (!allowed(method, route)) { throw new Failure('route-not-allowed'); }
    if (method === 'POST' && route === `/orgs/${OWNER}/repos`
        && (!NAMES.includes(body?.name) || body.private !== true || body.auto_init !== true)) {
      throw new Failure('creation-payload-not-allowed');
    }
    let response;
    try {
      response = await this.#fetch(`https://api.github.com${route}`, {
        method, redirect: 'error', signal: AbortSignal.timeout(20000),
        headers: { Authorization: `Bearer ${this.#token}`, Accept: 'application/vnd.github+json',
          'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch { throw new Failure('transport-failure'); }
    if (!response.ok) { throw new Failure('github-request-failed', response.status); }
    try { return await response.json(); } catch { throw new Failure('invalid-github-response'); }
  }
}
function verifyRepository(repo, name, orgId) {
  if (repo?.full_name !== `${OWNER}/${name}` || repo.name !== name
      || repo.owner?.login !== OWNER || repo.owner?.id !== orgId
      || !Number.isSafeInteger(repo.id) || repo.id <= 0
      || repo.private !== true || repo.visibility !== 'private'
      || repo.archived !== false || repo.default_branch !== 'main'
      || repo.html_url !== `https://github.com/${OWNER}/${name}`) {
    throw new Failure('repository-identity-or-policy-drift');
  }
  return repo;
}
function isStatus(error, values) { return error instanceof Failure && values.includes(error.status); }
async function lookup(api, route) {
  try { return await api.request('GET', route); }
  catch (error) { if (isStatus(error, [404])) { return null; } throw error; }
}
async function visibleRepository(api, route, wait) {
  for (let i = 0; i < 5; i += 1) {
    const repo = await lookup(api, route);
    if (repo) { return repo; }
    if (i < 4) { await wait(1000 * (2 ** i)); }
  }
  throw new Failure('repository-not-yet-readable');
}
export function issueBody(item) {
  return `${MARKER}\n# ${item.name}: implementation acceptance\n\n${item.purpose}.\n\n`
    + 'Tracking: https://linear.app/denman/project/ores-legal-6ea9d5d4d0b7\n'
    + 'Execution: https://github.com/ORESoftware/ai-agent-coordinator.rs/issues/230\n'
    + 'Original request: https://github.com/dancing-dragons/dd-next-1/issues/483\n\n'
    + '- [ ] Implement the repository responsibility above with reviewable feature branches and PRs.\n'
    + '- [ ] Keep TypeSpec and JSON Schema independent; require differential conformance before generated interfaces are accepted.\n'
    + '- [ ] Integrate Shared Auth, tenant isolation, rate limits, ORES middleware/telemetry, flags-2-env and encrypted runtime configuration where applicable.\n'
    + '- [ ] Test signatures, initials, template versions, consent, authorization, replay rejection, evidence hashes and completion transitions at relevant boundaries.\n'
    + '- [ ] Record exact-head CI, independent ores-legal-test acceptance, actual integrations and residual blockers; merge only reviewed/tested changes.\n\n'
    + 'Initialization is NOT signing-platform parity. No signing production deployment, real customer signatures, paid database resources or legal compliance certification is included in this bootstrap.\n';
}
async function findIssue(api, root) {
  let found = null;
  for (let page = 1; page <= 10; page += 1) {
    const issues = await api.request('GET', `${root}/issues?state=all&per_page=100&page=${page}`);
    if (!Array.isArray(issues)) { throw new Failure('invalid-issue-list'); }
    for (const issue of issues) {
      if (!issue.pull_request && typeof issue.body === 'string' && issue.body.includes(MARKER)) {
        if (found) { throw new Failure('duplicate-roadmap-issues'); }
        found = issue;
      }
    }
    if (issues.length < 100) { return found; }
  }
  throw new Failure('issue-scan-limit');
}
async function trackIssue(api, root, item) {
  let issue = await findIssue(api, root);
  if (!issue) {
    try {
      issue = await api.request('POST', `${root}/issues`, {
        title: `DEN-319: ${item.name} signing-platform foundation`, body: issueBody(item),
      });
    } catch (error) {
      // Reconcile an ambiguous mutation once; never blindly repeat a POST.
      if (!(error instanceof Failure) || (error.status !== null && error.status < 500)) { throw error; }
      issue = await findIssue(api, root);
      if (!issue) { throw error; }
    }
  }
  if (!Number.isSafeInteger(issue.number) || issue.number <= 0
      || issue.html_url !== `https://github.com/${OWNER}/${item.name}/issues/${issue.number}`
      || !issue.body?.includes(MARKER)) {
    throw new Failure('invalid-roadmap-receipt');
  }
  return issue.html_url;
}
export async function provision(manifest, api, checkpoint = async () => {}, wait = async (ms) => {
  await new Promise((resolve) => setTimeout(resolve, ms));
}) {
  const report = { organization: OWNER, status: 'in-progress', repositories: [] };
  const org = await api.request('GET', `/orgs/${OWNER}`);
  if (org?.login !== OWNER || org.type !== 'Organization' || !Number.isSafeInteger(org.id) || org.id <= 0) {
    throw new Failure('organization-identity-drift');
  }
  for (const item of manifest.repositories) {
    const root = `/repos/${OWNER}/${item.name}`;
    const receipt = { repository: `${OWNER}/${item.name}`, repository_action: 'none', status: 'blocked' };
    report.repositories.push(receipt);
    try {
      let repo = await lookup(api, root);
      if (!repo) {
        try {
          repo = await api.request('POST', `/orgs/${OWNER}/repos`, {
            name: item.name, description: item.purpose, private: true, auto_init: true,
            has_issues: true, has_wiki: false, allow_rebase_merge: false,
          });
          receipt.repository_action = 'created';
          verifyRepository(repo, item.name, org.id);
          receipt.id = repo.id;
          await checkpoint(report);
        } catch (error) {
          if (receipt.repository_action === 'created') { throw error; }
          if (!(error instanceof Failure) || (error.status !== null && error.status !== 422 && error.status < 500)) { throw error; }
          repo = await visibleRepository(api, root, wait);
          receipt.repository_action = 'reconciled-after-ambiguous-create';
        }
        const visible = verifyRepository(await visibleRepository(api, root, wait), item.name, org.id);
        if (visible.id !== repo.id) { throw new Failure('repository-id-changed'); }
        repo = visible;
      } else { receipt.repository_action = 'existing-unchanged'; }
      verifyRepository(repo, item.name, org.id);
      receipt.id = repo.id;
      receipt.url = repo.html_url;
      let commit;
      for (let attempt = 0; attempt < 5; attempt += 1) {
        try { commit = await api.request('GET', `${root}/commits/main`); break; }
        catch (error) {
          if (!isStatus(error, [404, 409]) || attempt === 4) { throw error; }
          await wait(1000 * (2 ** attempt));
        }
      }
      if (!SHA.test(commit?.sha ?? '')) { throw new Failure('invalid-commit-receipt'); }
      receipt.default_branch = 'main';
      receipt.commit = commit.sha;
      receipt.repository_verified = true;
      receipt.roadmap_issue = await trackIssue(api, root, item);
      receipt.status = 'verified';
    } catch (error) {
      receipt.error = error instanceof Failure ? error.code : 'unexpected-failure';
      receipt.http_status = error instanceof Failure ? error.status : null;
    }
    await checkpoint(report);
  }
  report.status = report.repositories.every((r) => r.status === 'verified') ? 'complete' : 'blocked';
  await checkpoint(report);
  return report;
}
