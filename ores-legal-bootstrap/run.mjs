import { readFile, rename, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { assertApplyGate, Failure, GitHubClient, provision, validateManifest } from './bootstrap.mjs';

export const MANIFEST_SHA256 = 'e1b7a228afa5df1ead8972c4afc24e5e510bdfd12ab4239818461cac998f865c';

export async function run(env = process.env) {
  const raw = await readFile(new URL('./manifest.json', import.meta.url), 'utf8');
  const manifest = validateManifest(raw, MANIFEST_SHA256);
  const mode = env.ORES_LEGAL_MODE ?? 'plan';
  if (mode === 'plan') {
    return { status: 'plan-only', manifest_sha256: MANIFEST_SHA256, ...manifest };
  }
  if (mode !== 'apply') { throw new Failure('invalid-mode'); }
  assertApplyGate(env, MANIFEST_SHA256);
  if (!env.ORES_LEGAL_RECEIPT_PATH) { throw new Failure('missing-receipt-path'); }
  const checkpoint = async (report) => {
    const value = { ...report, manifest_sha256: MANIFEST_SHA256,
      source_repository: env.GITHUB_REPOSITORY, source_commit: env.GITHUB_SHA };
    const temporary = `${env.ORES_LEGAL_RECEIPT_PATH}.tmp`;
    await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    await rename(temporary, env.ORES_LEGAL_RECEIPT_PATH);
  };
  await checkpoint({ organization: 'ores-legal', status: 'not-started', repositories: [] });
  try {
    return await provision(manifest, new GitHubClient(env.ORES_LEGAL_ADMIN_TOKEN), checkpoint);
  } catch (error) {
    const report = { organization: 'ores-legal', status: 'blocked',
      error: error instanceof Failure ? error.code : 'unexpected-failure' };
    await checkpoint(report);
    return report;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    // No CLI options or independent option parser. Unknown arguments fail closed.
    if (process.argv.length !== 2) { throw new Failure('arguments-not-supported'); }
    const report = await run();
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
    process.exitCode = report.status === 'blocked' ? 1 : 0;
  } catch (error) {
    process.stderr.write(`${JSON.stringify({ status: 'blocked',
      error: error instanceof Failure ? error.code : 'unexpected-failure' })}\n`);
    process.exitCode = 1;
  }
}
