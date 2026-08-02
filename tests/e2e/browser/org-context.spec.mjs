import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, normalize, resolve, sep } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";
import { marked } from "marked";

const testDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(testDirectory, "../../..");
const registryRef = process.env.ORG_CONTEXT_REGISTRY_REF ?? "2".repeat(40);

const pilots = [
  {
    owner: "fiducia-cloud",
    accountId: 297262292,
    projectId: "d9e89bd3-19da-47f3-9bf7-6dc8cc910b70",
    projectName: "github.com/fiducia-cloud",
    projectUrl:
      "https://linear.app/denman/project/githubcomfiducia-cloud-8fd5e1bec9d3",
    defaultRepository: null,
  },
  {
    owner: "sonus-auris",
    accountId: 292916213,
    projectId: "40905103-ae88-4186-9cff-858b7b9384d2",
    projectName: "github.com/sonus-auris",
    projectUrl:
      "https://linear.app/denman/project/githubcomsonus-auris-a557165528ef",
    defaultRepository: null,
  },
  {
    owner: "shared-auth",
    accountId: 307325286,
    projectId: "4bbe0ba4-d7f1-49ce-8f41-afd2cff6c2a2",
    projectName: "github.com/shared-auth",
    projectUrl:
      "https://linear.app/denman/project/githubcomshared-auth-acbca07bb390",
    defaultRepository: "shared-auth/shared-auth-mcp-server.rs",
  },
];

const forbiddenCredentialMarkers =
  /(?:gh[pousr]_|github_pat_|lin_api_|xox[bpars]-|bearer\s+|-----begin private key-----)/i;

let generatedRoot;
let previewServer;
let previewBaseUrl;

function bundlePath(owner, relativePath) {
  const ownerRoot = resolve(generatedRoot, owner);
  const candidate = resolve(ownerRoot, normalize(relativePath));
  if (!candidate.startsWith(`${ownerRoot}${sep}`)) {
    throw new Error(`path escaped generated bundle: ${relativePath}`);
  }
  return candidate;
}

async function sendFile(response, owner, relativePath, contentType) {
  const candidate = bundlePath(owner, relativePath);
  const metadata = await stat(candidate);
  if (!metadata.isFile()) throw new Error(`not a file: ${relativePath}`);
  response.writeHead(200, {
    "cache-control": "no-store",
    "content-type": `${contentType}; charset=utf-8`,
    "x-content-type-options": "nosniff",
  });
  response.end(await readFile(candidate, "utf8"));
}

async function requestHandler(request, response) {
  try {
    const url = new URL(request.url, "http://127.0.0.1");
    const segments = url.pathname.split("/").filter(Boolean);
    const owner = segments.shift();
    if (!pilots.some((pilot) => pilot.owner === owner)) {
      response.writeHead(404).end("unknown owner");
      return;
    }

    if (segments[0] === "raw") {
      const relativePath = decodeURIComponent(segments.slice(1).join("/"));
      await sendFile(response, owner, relativePath, "text/plain");
      return;
    }
    if (segments.length === 1 && segments[0] === "project-context.yaml") {
      await sendFile(response, owner, "project-context.yaml", "application/json");
      return;
    }
    if (segments.length === 1 && segments[0] === "org-context-manifest.json") {
      await sendFile(response, owner, "org-context-manifest.json", "application/json");
      return;
    }
    if (
      segments.length === 2 &&
      segments[0] === "agents" &&
      segments[1] === "org-context.agent.md"
    ) {
      await sendFile(
        response,
        owner,
        "agents/org-context.agent.md",
        "text/markdown",
      );
      return;
    }

    const profile = segments.length === 1 && segments[0] === "profile";
    const repository = segments.length === 0;
    if (!profile && !repository) {
      response.writeHead(404).end("unknown path");
      return;
    }
    const markdownPath = profile ? "profile/README.md" : "README.md";
    const markdown = await readFile(bundlePath(owner, markdownPath), "utf8");
    const body = await marked.parse(markdown, { gfm: true });
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
    <title>${owner} organization context</title>
    <style>body{font:16px/1.5 system-ui;max-width:900px;margin:2rem auto;padding:0 1rem}code{background:#eee;padding:.1rem .25rem}</style>
  </head>
  <body><main aria-label="Organization context">${body}</main></body>
</html>`);
  } catch (error) {
    response.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
    response.end(String(error));
  }
}

test.beforeAll(async () => {
  expect(registryRef).toMatch(/^[0-9a-f]{40}$/);
  generatedRoot = await mkdtemp(join(tmpdir(), "den629-org-context-"));
  for (const pilot of pilots) {
    const result = spawnSync(
      "python3",
      [
        "scripts/render_org_project_context.py",
        "--owner",
        pilot.owner,
        "--registry-ref",
        registryRef,
        "--output-dir",
        join(generatedRoot, pilot.owner),
      ],
      { cwd: repositoryRoot, encoding: "utf8" },
    );
    expect(result.status, result.stderr).toBe(0);
  }
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

for (const pilot of pilots) {
  test(`${pilot.owner} profile renders canonical identity and safe links`, async ({
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
    await page.goto(`${previewBaseUrl}/${pilot.owner}/profile/`);

    await expect(page).toHaveTitle(`${pilot.owner} organization context`);
    await expect(page.getByRole("main", { name: "Organization context" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: pilot.owner })).toBeVisible();
    const projectLink = page.getByRole("link", { name: pilot.projectName });
    await expect(projectLink).toHaveAttribute("href", pilot.projectUrl);
    await expect(page.getByText(`GitHub owner ID: ${pilot.accountId}`)).toBeVisible();
    await expect(page.getByText(`Linear project ID: ${pilot.projectId}`)).toBeVisible();

    const contextLink = page.getByRole("link", { name: "project-context.yaml" });
    await expect(contextLink).toHaveAttribute(
      "href",
      `https://github.com/${pilot.owner}/.github/blob/main/project-context.yaml`,
    );
    const hrefs = await page.locator("a").evaluateAll((anchors) =>
      anchors.map((anchor) => anchor.getAttribute("href")),
    );
    expect(hrefs.every((href) => href?.startsWith("https://"))).toBe(true);
    expect(await page.locator("script").count()).toBe(0);
    expect(consoleErrors).toEqual([]);
  });

  test(`${pilot.owner} machine context, agent, and manifest agree`, async ({
    request,
  }) => {
    const contextResponse = await request.get(
      `${previewBaseUrl}/${pilot.owner}/project-context.yaml`,
    );
    expect(contextResponse.ok()).toBe(true);
    const context = await contextResponse.json();
    expect(context.github.login).toBe(pilot.owner);
    expect(context.github.account_id).toBe(pilot.accountId);
    expect(context.linear.project_id).toBe(pilot.projectId);
    expect(context.generated_from.ref).toBe(registryRef);
    expect(context.generated_from.ref_type).toBe("commit");
    expect(context.generated_from.immutable).toBe(true);
    expect(context.generated_from.raw_url).toContain(`/${registryRef}/`);
    expect(context.runtime_route?.default_repository ?? null).toBe(
      pilot.defaultRepository,
    );

    const agentResponse = await request.get(
      `${previewBaseUrl}/${pilot.owner}/agents/org-context.agent.md`,
    );
    expect(agentResponse.ok()).toBe(true);
    const agent = await agentResponse.text();
    expect(agent).toContain("target: github-copilot");
    expect(agent).toContain('tools: ["read", "search"]');
    expect(agent).toContain("Fail closed");
    expect(agent).toContain(pilot.projectId);

    const manifestResponse = await request.get(
      `${previewBaseUrl}/${pilot.owner}/org-context-manifest.json`,
    );
    expect(manifestResponse.ok()).toBe(true);
    const manifest = await manifestResponse.json();
    expect(manifest.github_owner).toBe(pilot.owner);
    expect(manifest.registry_ref).toBe(registryRef);
    for (const [relativePath, expectedDigest] of Object.entries(manifest.files)) {
      const rawResponse = await request.get(
        `${previewBaseUrl}/${pilot.owner}/raw/${relativePath}`,
      );
      expect(rawResponse.ok()).toBe(true);
      const content = await rawResponse.text();
      expect(createHash("sha256").update(content).digest("hex")).toBe(
        expectedDigest,
      );
      expect(content).not.toMatch(forbiddenCredentialMarkers);
    }
  });
}
