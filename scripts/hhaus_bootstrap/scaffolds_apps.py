from __future__ import annotations

import json
from typing import Any

def clients_files(manifest: dict[str, Any]) -> dict[str, str]:
    files: dict[str, str] = {
        "language-targets.json": json.dumps(
            {
                "schema_version": 1,
                "targets": manifest["language_targets"],
                "imports": ["hhaus-org/hhaus-interfaces", "hhaus-org/hhaus-lib-core"],
                "orchestrator": "zed-pkg/zed-pkg",
            },
            indent=2,
        )
        + "\n",
        "clients/typescript/src/client.ts": '''export interface ClientOptions {\n  readonly baseUrl: URL;\n  readonly fetchImpl?: typeof fetch;\n}\n\nexport class HHausClient {\n  readonly #baseUrl: URL;\n  readonly #fetch: typeof fetch;\n\n  constructor(options: ClientOptions) {\n    this.#baseUrl = options.baseUrl;\n    this.#fetch = options.fetchImpl ?? fetch;\n  }\n\n  async health(signal?: AbortSignal): Promise<unknown> {\n    const response = await this.#fetch(new URL("/healthz", this.#baseUrl), { signal });\n    if (!response.ok) throw new Error(`H/HAUS health request failed: ${response.status}`);\n    return response.json();\n  }\n}\n''',
        "clients/rust/src/lib.rs": r'''#![forbid(unsafe_code)]

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ClientConfig {
    pub base_url: String,
    pub user_agent: String,
}

impl ClientConfig {
    /// Verifies the minimum production transport configuration.
    ///
    /// # Errors
    ///
    /// Returns an error for a non-HTTPS base URL or a blank user agent.
    pub fn validate(&self) -> Result<(), &'static str> {
        if !self.base_url.starts_with("https://") {
            return Err("production clients require HTTPS");
        }
        if self.user_agent.trim().is_empty() {
            return Err("user agent is required");
        }
        Ok(())
    }
}
''',
        "clients/rust/Cargo.toml": '''[package]\nname = "hhaus-client"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.85"\nlicense = "MIT"\n\n[lints.rust]\nunsafe_code = "forbid"\n''',
        "clients/dart/lib/hhaus_client.dart": '''final class HHausClientConfig {\n  const HHausClientConfig({required this.baseUri});\n  final Uri baseUri;\n\n  void validate() {\n    if (baseUri.scheme != 'https') {\n      throw ArgumentError.value(baseUri, 'baseUri', 'production clients require HTTPS');\n    }\n  }\n}\n''',
    }
    for language in manifest["language_targets"]:
        readme_path = f"clients/{language}/README.md"
        if readme_path not in files:
            files[readme_path] = (
                f"# H/HAUS {language} client\n\n"
                "This target is generated from `hhaus-interfaces` and imports shared runtime validation from "
                "`hhaus-lib-core` through `zed-pkg`. Hand-written transport adapters must not fork contract validators.\n"
            )
    return files


def flutter_files() -> dict[str, str]:
    pubspec = '''name: hhaus_flutter\ndescription: H/HAUS iOS, Android, and desktop application surfaces.\npublish_to: none\nversion: 0.1.0+1\nenvironment:\n  sdk: '>=3.6.0 <4.0.0'\ndependencies:\n  flutter:\n    sdk: flutter\ndev_dependencies:\n  flutter_test:\n    sdk: flutter\n  flutter_lints: ^5.0.0\nflutter:\n  uses-material-design: true\n'''
    main = '''import 'package:flutter/material.dart';\n\nvoid main() => runApp(const HHausApp());\n\nfinal class HHausApp extends StatelessWidget {\n  const HHausApp({super.key});\n\n  @override\n  Widget build(BuildContext context) => MaterialApp(\n        title: 'H/HAUS',\n        home: Scaffold(\n          appBar: AppBar(title: const Text('H/HAUS')),\n          body: const Center(child: Text('Shared-auth bootstrap pending')),\n        ),\n      );\n}\n'''
    platform = '''enum RateLimitHealth { healthy, degraded, blocked }\n\nfinal class PlatformState {\n  const PlatformState({\n    required this.authenticated,\n    required this.rateLimitHealth,\n    required this.syncPending,\n  });\n\n  final bool authenticated;\n  final RateLimitHealth rateLimitHealth;\n  final int syncPending;\n}\n'''
    test = '''import 'package:flutter_test/flutter_test.dart';\nimport 'package:hhaus_flutter/platform_state.dart';\n\nvoid main() {\n  test('platform state retains explicit rate-limit health', () {\n    const state = PlatformState(\n      authenticated: false,\n      rateLimitHealth: RateLimitHealth.degraded,\n      syncPending: 2,\n    );\n    expect(state.rateLimitHealth, RateLimitHealth.degraded);\n  });\n}\n'''
    return {
        "pubspec.yaml": pubspec,
        "lib/main.dart": main,
        "lib/platform_state.dart": platform,
        "test/platform_state_test.dart": test,
    }


def desktop_files() -> dict[str, str]:
    cargo = '''[package]\nname = "hhaus-desktop-app"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.85"\nlicense = "MIT"\ndescription = "Native Rust desktop application for H/HAUS"\n\n[dependencies]\nslint = { version = "1.13", default-features = false, features = ["compat-1-2", "std", "backend-winit", "renderer-femtovg"] }\n\n[lints.rust]\nunsafe_code = "forbid"\n\n[lints.clippy]\nall = "deny"\npedantic = "deny"\nunwrap_used = "deny"\nexpect_used = "deny"\n'''
    main = r'''#![forbid(unsafe_code)]

slint::slint! {
    export component MainWindow inherits Window {
        title: "H/HAUS";
        width: 720px;
        height: 480px;
        VerticalLayout {
            padding: 32px;
            spacing: 16px;
            Text { text: "H/HAUS"; font-size: 32px; }
            Text { text: "Native Rust desktop foundation"; }
            Text { text: "Auth, sync, telemetry, and rate-limit adapters are explicit platform dependencies."; wrap: word-wrap; }
        }
    }
}

fn main() -> Result<(), slint::PlatformError> {
    MainWindow::new()?.run()
}
'''
    platform = r'''#![forbid(unsafe_code)]

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Connectivity {
    Online,
    Offline,
    Degraded,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RateLimitHealth {
    Healthy,
    LocalOnly,
    Blocked,
}
'''
    return {"Cargo.toml": cargo, "src/main.rs": main, "src/platform.rs": platform}


def lambda_files() -> dict[str, str]:
    cargo = '''[package]\nname = "hhaus-lambdas"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.85"\nlicense = "MIT"\ndescription = "Cross-provider H/HAUS function handler core"\n\n[lints.rust]\nunsafe_code = "forbid"\n\n[lints.clippy]\nall = "deny"\npedantic = "deny"\nunwrap_used = "deny"\nexpect_used = "deny"\n'''
    rust = r'''#![forbid(unsafe_code)]

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Provider {
    AwsLambda,
    GoogleCloudRunJob,
    AzureFunctions,
    CloudflareWorkers,
    Local,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RouteClass {
    PublicRead,
    PublicIntake,
    Auth,
    AuthenticatedWrite,
    Admin,
    Sync,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InvocationContext {
    pub request_id: String,
    pub pseudonymous_subject: String,
    pub route_class: RouteClass,
}

/// Returns whether a route class must reach the durable security or billing quota layer.
#[must_use]
pub const fn requires_durable_quota(route: RouteClass) -> bool {
    matches!(route, RouteClass::PublicIntake | RouteClass::AuthenticatedWrite | RouteClass::Admin)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sensitive_writes_require_durable_quota() {
        assert!(requires_durable_quota(RouteClass::Admin));
        assert!(!requires_durable_quota(RouteClass::PublicRead));
    }
}
'''
    worker = '''export default {\n  async fetch(request, env) {\n    const requestId = request.headers.get("x-request-id") ?? crypto.randomUUID();\n    const response = await env.HHAUS_LAMBDA_CORE.fetch(request);\n    const headers = new Headers(response.headers);\n    headers.set("x-request-id", requestId);\n    return new Response(response.body, { status: response.status, headers });\n  },\n};\n'''
    topology = {
        "schema_version": 1,
        "providers": ["aws-lambda", "google-cloud-run-job", "azure-functions", "cloudflare-workers", "local"],
        "middleware": "ORESoftware/ores-middleware",
        "auth": "shared-auth/shared-auth-lambdas",
        "telemetry": "ores-otel/ores-otel-clients",
        "rate_limit": "ores-rate-limit/ores-rl-lib-core",
    }
    return {
        "Cargo.toml": cargo,
        "src/lib.rs": rust,
        "providers/cloudflare/index.mjs": worker,
        "lambda-topology.json": json.dumps(topology, indent=2, sort_keys=True) + "\n",
    }
