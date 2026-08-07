import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const testDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(testDirectory, "../../..");
const registryRef = process.env.ORG_CONTEXT_REGISTRY_REF ?? "4".repeat(40);
const forbiddenCredentialMarkers =
  /(?:gh[pousr]_|github_pat_|lin_api_|xox[bpars]-|bearer\s+|-----begin private key-----)/i;

let generatedRoot;
let relationshipRoot;
let index;
let owners;
let previewServer;
let previewBaseUrl;

function runPython(arguments_) {
  const result = spawnSync("python3", arguments_, {
    cwd: repositoryRoot,
    encoding: "utf8",
  });
  expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function sendJson(response, path) {
  response.writeHead(200, {
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
    "x-content-type-options": "nosniff",
  });
  response.end(await readFile(path, "utf8"));
}

async function requestHandler(request, response) {
  try {
    const url = new URL(request.url, "http://127.0.0.1");
    if (url.pathname === "/repository-relationships-index.json") {
      await sendJson(
        response,
        join(relationshipRoot, "repository-relationships-index.json"),
      );
      return;
    }
    if (url.pathname === "/rollout-audit.json") {
      await sendJson(response, join(generatedRoot, "rollout-audit.json"));
      return;
    }
    const match = url.pathname.match(
      /^\/owners\/([A-Za-z0-9-]+)\/repository-relationships\.json$/,
    );
    if (match && owners.has(match[1])) {
      await sendJson(
        response,
        join(relationshipRoot, match[1], "repository-relationships.json"),
      );
      return;
    }
    if (url.pathname !== "/") {
      response.writeHead(404).end("not found");
      return;
    }
    const items = [...owners]
      .sort((left, right) => left.localeCompare(right))
      .map(
        (owner) =>
          `<li><a href="/owners/${encodeURIComponent(owner)}/repository-relationships.json">${escapeHtml(owner)}</a></li>`,
      )
      .join("");
    response.writeHead(200, {
      "cache-control": "no-store",
      "content-security-policy":
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
      "content-type": "text/html; charset=utf-8",
      "x-content-type-options": "nosniff",
    });
    response.end(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Organization repository relationships</title>
    <style>body{font:16px/1.5 system-ui;max-width:900px;margin:2rem auto;padding:0 1rem}</style>
  </head>
  <body>
    <main aria-label="Repository relationship rollout">
      <h1>Organization repository relationships</h1>
      <p>Mapped owners: ${index.owner_count}</p>
      <ul>${items}</ul>
    </main>
  </body>
</html>`);
  } catch (error) {
    response.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
    response.end(String(error));
  }
}

test.beforeAll(async () => {
  expect(registryRef).toMatch(/^[0-9a-f]{40}$/);
  generatedRoot = await mkdtemp(join(tmpdir(), "den629-org-relationships-"));
  relationshipRoot = join(generatedRoot, "relationships");
  runPython([
    "scripts/render_org_repository_relationships.py",
    "--all",
    "--registry-ref",
    registryRef,
    "--output-dir",
    relationshipRoot,
  ]);
  runPython([
    "scripts/audit_org_context_rollout.py",
    "--registry-ref",
    registryRef,
    "--output",
    join(generatedRoot, "rollout-audit.json"),
  ]);
  index = JSON.parse(
    await readFile(
      join(relationshipRoot, "repository-relationships-index.json"),
      "utf8",
    ),
  );
  owners = new Set(
    Object.keys(index.files).map((path) => path.split("/", 1)[0]),
  );
  previewServer = createServer((request, response) => {
    void requestHandler(request, response);
  });
  await new Promise((resolveListening, rejectListening) => {
    previewServer.once("error", rejectListening);
    previewServer.listen(0, "127.0.0.1", resolveListening);
  });
  const address = previewServer.address();
  if (!address || typeof address === "string") throw new Error("missing preview port");
  previewBaseUrl = `http://127.0.0.1:${address.port}`;
});

test.afterAll(async () => {
  if (previewServer) {
    await new Promise((resolveClosed, rejectClosed) =>
      previewServer.close((error) =>
        error ? rejectClosed(error) : resolveClosed(),
      ),
    );
  }
  if (generatedRoot) await rm(generatedRoot, { recursive: true, force: true });
});

test("repository relationship rollout overview exposes every mapped owner safely", async ({
  page,
}) => {
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "127.0.0.1") await route.continue();
    else await route.abort("blockedbyclient");
  });
  await page.goto(`${previewBaseUrl}/`);
  await expect(page).toHaveTitle("Organization repository relationships");
  await expect(
    page.getByRole("main", { name: "Repository relationship rollout" }),
  ).toBeVisible();
  await expect(page.getByText("Mapped owners: 31")).toBeVisible();
  await expect(page.getByRole("listitem")).toHaveCount(31);
  expect(await page.locator("script").count()).toBe(0);
  expect(consoleErrors).toEqual([]);
});

test("repository relationship rollout audit remains dry-run and fail-closed", async ({
  request,
}) => {
  const response = await request.get(`${previewBaseUrl}/rollout-audit.json`);
  expect(response.ok()).toBe(true);
  const audit = await response.json();
  expect(audit.summary).toEqual({
    complete: false,
    eligible_organizations: 30,
    excluded_unmapped: 7,
    existing_public: 3,
    missing: 27,
    unsupported_account_type: 1,
    visibility_mismatch: 0,
  });
  expect(audit.bootstrap_contract.live_creation_authorized_by_this_artifact).toBe(
    false,
  );
  const requests = audit.owners
    .map((owner) => owner.bootstrap_dry_run?.body)
    .filter(Boolean);
  expect(requests).toHaveLength(27);
  for (const body of requests) {
    expect(body.dry_run).toBe(true);
    expect(body.name).toBe(".github");
    expect(body.visibility).toBe("public");
    expect(body).not.toHaveProperty("confirm_repository");
  }
  const userOwner = audit.owners.find(
    (owner) => owner.github.login === "ORESoftware",
  );
  expect(userOwner.status).toBe("unsupported_account_type");
  expect(userOwner.bootstrap_dry_run).toBeNull();
});

test("repository relationship manifests preserve identity and semantic conflict policy", async ({
  request,
}) => {
  const indexResponse = await request.get(
    `${previewBaseUrl}/repository-relationships-index.json`,
  );
  expect(indexResponse.ok()).toBe(true);
  const remoteIndex = await indexResponse.json();
  expect(remoteIndex.owner_count).toBe(31);
  expect(Object.keys(remoteIndex.files)).toHaveLength(31);
  for (const path of Object.keys(remoteIndex.files)) {
    const owner = path.split("/", 1)[0];
    const response = await request.get(
      `${previewBaseUrl}/owners/${encodeURIComponent(owner)}/repository-relationships.json`,
    );
    expect(response.ok(), owner).toBe(true);
    const text = await response.text();
    expect(text).not.toMatch(forbiddenCredentialMarkers);
    const manifest = JSON.parse(text);
    expect(manifest.github.login).toBe(owner);
    expect(manifest.generated_from.ref).toBe(registryRef);
    expect(manifest.generated_from.immutable).toBe(true);
    expect(manifest.governance.repository).toBe(`${owner}/.github`);
    expect(manifest.governance.automatic_agent_instruction_inheritance).toBe(false);
    expect(manifest.repository_selection.unregistered_dependencies).toBe(
      "unknown_not_assumed",
    );
    expect(manifest.git_conflict_resolution.history_lookback_commits).toMatchObject({
      minimum: 3,
      maximum: 10,
      inspect_both_sides: true,
      inspect_merge_base: true,
      path_scoped_history: true,
    });
    expect(manifest.git_conflict_resolution.context_scope).toContain(
      "relevant_external_github_organization_repositories",
    );
  }
});
