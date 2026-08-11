#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

API = "https://api.github.com"
API_VERSION = "2022-11-28"
ASTRO_VERSION = "7.1.6"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"  # actions/checkout v7.0.1
ASTRO_ACTION_SHA = "b7d53628f8b666036b0238aadb0b984a2a489f26"  # withastro/action v6.1.1
DEPLOY_PAGES_SHA = "d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e"  # actions/deploy-pages v4
USER_AGENT = "portfolio-astro-pages-bootstrap/2026-08-10"
GENERATED_MARKER = "portfolio-astro-pages-bootstrap:2026-08-10"


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, message: str):
        clean = re.sub(r"gh[pousr]_[A-Za-z0-9_]+", "[REDACTED]", message)
        super().__init__(f"GitHub {method} {path} returned {status}: {clean[:700]}")
        self.method = method
        self.path = path
        self.status = status
        self.message = clean[:700]


class GitHub:
    def __init__(self, token: str):
        self._token = token

    def close(self) -> None:
        self._token = ""

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        allow: tuple[int, ...] = (),
        accept: str = "application/vnd.github+json",
    ) -> tuple[int, Any]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self._token}",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(6):
            request = urllib.request.Request(API + path, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=75) as response:
                    raw = response.read()
                    if not raw:
                        return response.status, None
                    content_type = response.headers.get("Content-Type", "")
                    if "json" in content_type:
                        return response.status, json.loads(raw)
                    return response.status, raw.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as error:
                raw = error.read(8192)
                try:
                    message = json.loads(raw).get("message", "unknown error")
                except Exception:
                    message = raw.decode("utf-8", errors="replace")
                if error.code in allow:
                    return error.code, None
                if error.code in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(min(2 ** (attempt + 1), 20))
                    continue
                raise ApiError(method, path, error.code, str(message)) from None
            except urllib.error.URLError as error:
                if attempt < 5:
                    time.sleep(min(2 ** (attempt + 1), 20))
                    continue
                raise RuntimeError(f"GitHub transport failed for {method} {path}: {error.reason}") from None
        raise AssertionError("unreachable")

    def get(self, path: str, *, allow: tuple[int, ...] = ()) -> tuple[int, Any]:
        return self.request("GET", path, allow=allow)

    def post(self, path: str, payload: Any, *, allow: tuple[int, ...] = ()) -> tuple[int, Any]:
        return self.request("POST", path, payload, allow=allow)

    def patch(self, path: str, payload: Any, *, allow: tuple[int, ...] = ()) -> tuple[int, Any]:
        return self.request("PATCH", path, payload, allow=allow)

    def put(self, path: str, payload: Any, *, allow: tuple[int, ...] = ()) -> tuple[int, Any]:
        return self.request("PUT", path, payload, allow=allow)


@dataclasses.dataclass(frozen=True)
class Feature:
    title: str
    body: str
    tag: str


@dataclasses.dataclass(frozen=True)
class Site:
    org: str
    title: str
    eyebrow: str
    headline: str
    summary: str
    promise: str
    mode: str
    audience: str
    cadence: str
    features: tuple[Feature, ...]
    steps: tuple[tuple[str, str], ...]
    keywords: tuple[str, ...]
    legal_note: str = ""

    @property
    def repo(self) -> str:
        return f"{self.org.lower()}.github.io"

    @property
    def full_name(self) -> str:
        return f"{self.org}/{self.repo}"

    @property
    def page_url(self) -> str:
        return f"https://{self.org.lower()}.github.io/"

    @property
    def org_url(self) -> str:
        return f"https://github.com/{self.org}"


SITES: dict[str, Site] = {
    site.org.lower(): site
    for site in (
        Site(
            org="embedded-alerts",
            title="Embedded Alerts",
            eyebrow="Signals inside the workflow",
            headline="Alerts that arrive with context, not interruption.",
            summary="Embedded Alerts gives product teams composable alert surfaces, policy-aware routing, and observable delivery across web, mobile, and service workflows.",
            promise="Turn high-value events into actionable moments without forcing users out of the product.",
            mode="Embedded",
            audience="Product + platform teams",
            cadence="Event driven",
            features=(
                Feature("Embeddable surfaces", "Ship inboxes, banners, activity streams, and contextual prompts through a shared contract.", "UI"),
                Feature("Policy-aware routing", "Apply channel, urgency, quiet-hour, tenant, and consent rules before delivery.", "Policy"),
                Feature("Observable delivery", "Trace every decision from source event through audience resolution and acknowledgement.", "OTEL"),
                Feature("Typed integration", "Keep SDKs, schemas, server behavior, and client rendering aligned across runtimes.", "SDK"),
                Feature("Safe escalation", "Promote unresolved signals deliberately instead of multiplying duplicate notifications.", "Reliability"),
                Feature("Human controls", "Give operators and end users explicit controls for review, snooze, mute, and recovery.", "Control"),
            ),
            steps=(
                ("Declare", "Model the event, audience, severity, and consent requirements in versioned interfaces."),
                ("Resolve", "Evaluate policy and delivery state close to the product workflow."),
                ("Observe", "Measure delivery, interaction, and escalation without losing causal context."),
            ),
            keywords=("embedded alerts", "notification infrastructure", "event routing", "observability"),
        ),
        Site(
            org="evento-globolo",
            title="Evento Globolo",
            eyebrow="One event, everywhere it matters",
            headline="Publish locally. Discover globally. Keep one source of truth.",
            summary="Evento Globolo connects organizers, communities, and attendees through structured event publishing, distribution, discovery, and lifecycle updates.",
            promise="Make every event easier to publish, easier to trust, and easier to find across channels and regions.",
            mode="Global discovery",
            audience="Organizers + communities",
            cadence="Lifecycle aware",
            features=(
                Feature("Structured publishing", "Capture venue, access, language, schedule, capacity, and organizer context once.", "Create"),
                Feature("Cross-channel distribution", "Adapt canonical event data for calendars, social surfaces, partner feeds, and embeds.", "Distribute"),
                Feature("Regional discovery", "Support place, time, language, topic, and community-aware exploration.", "Discover"),
                Feature("Change propagation", "Keep cancellations, venue changes, availability, and timing synchronized.", "Sync"),
                Feature("Organizer identity", "Connect public event claims to durable organization and collaborator records.", "Trust"),
                Feature("Open interfaces", "Use versioned schemas and clients so the event graph can grow without lock-in.", "API"),
            ),
            steps=(
                ("Compose", "Create a canonical event record with reusable venue, host, and access details."),
                ("Syndicate", "Publish channel-specific representations while preserving the canonical identity."),
                ("Evolve", "Propagate lifecycle updates and retain a clear history for attendees and partners."),
            ),
            keywords=("events", "event discovery", "event publishing", "community calendar"),
        ),
        Site(
            org="hacker-house-medellin",
            title="Hacker House Medellín",
            eyebrow="Live together. Build deliberately.",
            headline="A focused base for builders shipping from Medellín.",
            summary="Hacker House Medellín coordinates residencies, workspace, local onboarding, events, and community operations for technical builders and independent teams.",
            promise="Create the conditions for deep work, practical collaboration, and a confident landing in Medellín.",
            mode="Coliving + coworking",
            audience="Builders + small teams",
            cadence="Residency based",
            features=(
                Feature("Residency operations", "Coordinate applications, rooms, dates, expectations, and community fit with a clear workflow.", "Stay"),
                Feature("Deep-work space", "Design routines and shared spaces around focused building rather than constant programming.", "Build"),
                Feature("Local onboarding", "Collect practical neighborhood, mobility, connectivity, and arrival guidance in one place.", "Arrive"),
                Feature("Technical community", "Host small, useful sessions around shipping, infrastructure, product, and open source.", "Learn"),
                Feature("Shared accountability", "Make house norms, ownership, maintenance, and incident paths explicit.", "Operate"),
                Feature("Portable systems", "Use open tooling so the community model can be audited and improved over time.", "Open"),
            ),
            steps=(
                ("Apply", "Share dates, working style, goals, and practical needs through a structured intake."),
                ("Settle", "Use clear arrival, house, workspace, and neighborhood guidance."),
                ("Ship", "Protect deep-work time while making collaboration easy when it creates leverage."),
            ),
            keywords=("Medellín hacker house", "coliving", "coworking", "builder residency"),
        ),
        Site(
            org="apostille-me",
            title="Apostille Me",
            eyebrow="Cross-border documents, clearer steps",
            headline="Move important documents across borders with less uncertainty.",
            summary="Apostille Me organizes requirements, document handoffs, jurisdiction-specific steps, status, and evidence for apostille and legalization workflows.",
            promise="Replace scattered instructions and opaque handoffs with a visible, reviewable case timeline.",
            mode="Guided workflow",
            audience="Individuals + operators",
            cadence="Case based",
            features=(
                Feature("Requirements map", "Organize document, destination, issuing authority, translation, and timing requirements.", "Plan"),
                Feature("Case timeline", "Show every requested, received, submitted, returned, and blocked step.", "Track"),
                Feature("Secure handoff", "Keep document exchange and operator access explicit, bounded, and auditable.", "Protect"),
                Feature("Evidence ledger", "Retain receipts, identifiers, notices, and resolution history without mixing cases.", "Prove"),
                Feature("Exception handling", "Surface signature, seal, authority, translation, and jurisdiction mismatches early.", "Resolve"),
                Feature("Human review", "Keep consequential decisions visible to the person responsible for the case.", "Review"),
            ),
            steps=(
                ("Map", "Identify the document path and confirm the relevant authorities and dependencies."),
                ("Prepare", "Collect the required originals, certifications, translations, and supporting evidence."),
                ("Track", "Follow each handoff and resolve exceptions against a durable case record."),
            ),
            keywords=("apostille", "document legalization", "cross-border documents", "case tracking"),
            legal_note="General workflow information is not legal advice. Requirements can vary by authority and jurisdiction.",
        ),
        Site(
            org="messaging-intel",
            title="Messaging Intel",
            eyebrow="Conversation intelligence with human control",
            headline="Turn scattered conversations into deliberate follow-through.",
            summary="Messaging Intel brings contact discovery, conversation matching, review queues, follow-up rules, and provider-specific browser adapters into one consent-aware workflow.",
            promise="Help teams notice the right conversation, prepare the right next step, and keep a human in control before sending.",
            mode="Human in the loop",
            audience="Relationship workflows",
            cadence="Conversation aware",
            features=(
                Feature("Conversation matching", "Resolve provider rows to stable people and threads before automation acts.", "Match"),
                Feature("Follow-up rules", "Model conditions and message pairs with quiet hours, rate limits, and consent gates.", "Rules"),
                Feature("Review queues", "Confirm matching and non-matching candidates before any consequential action.", "Review"),
                Feature("Provider adapters", "Separate Instagram, WhatsApp Web, ColombianCupid, and future browser behavior behind explicit contracts.", "Adapters"),
                Feature("Contact discovery", "Normalize, deduplicate, thread-check, and stage candidates without silently messaging them.", "Discover"),
                Feature("Ephemeral delivery", "Retrieve approved message bodies only at send time and retain an auditable approval record.", "Safety"),
            ),
            steps=(
                ("Observe", "Collect bounded conversation and contact evidence from supported providers."),
                ("Review", "Resolve identity, rule matches, and proposed copy in a human approval queue."),
                ("Act", "Send only through certified adapters that honor provider, consent, rate, and quiet-hour gates."),
            ),
            keywords=("messaging intelligence", "follow-up automation", "conversation review", "browser adapters"),
        ),
        Site(
            org="networking-components",
            title="Networking Components",
            eyebrow="Composable primitives for distributed systems",
            headline="Build network behavior from contracts you can inspect.",
            summary="Networking Components develops reusable protocol, transport, discovery, resilience, and test primitives with aligned interfaces and clients across runtimes.",
            promise="Make network behavior easier to compose, test, observe, and evolve without hiding failure semantics.",
            mode="Contract first",
            audience="Platform engineers",
            cadence="Versioned",
            features=(
                Feature("Protocol contracts", "Declare wire formats, capabilities, negotiation, and compatibility in machine-readable schemas.", "Schema"),
                Feature("Transport primitives", "Compose connection, framing, multiplexing, retry, timeout, and backpressure behavior.", "Transport"),
                Feature("Typed clients", "Expose consistent clients across Rust, TypeScript, Dart, Go, and additional runtimes.", "Clients"),
                Feature("Failure semantics", "Keep cancellation, partial delivery, reconnection, and degradation explicit.", "Resilience"),
                Feature("Observability hooks", "Carry trace, metric, and structured-log context across component boundaries.", "OTEL"),
                Feature("Cross-runtime tests", "Certify implementations against shared fixtures and adversarial network scenarios.", "Test"),
            ),
            steps=(
                ("Specify", "Define protocol and behavioral invariants in shared interfaces."),
                ("Implement", "Build focused components with explicit composition and failure boundaries."),
                ("Certify", "Run parity, compatibility, and degradation tests across supported runtimes."),
            ),
            keywords=("networking components", "protocol libraries", "distributed systems", "typed clients"),
        ),
        Site(
            org="StreemPilot",
            title="StreemPilot",
            eyebrow="Live production from a resilient control plane",
            headline="Direct guests, streams, recording, and interaction without losing the room.",
            summary="StreemPilot coordinates WebRTC studios, guest contribution, multistream output, recording, moderation, and live audience interaction across web, desktop, and mobile surfaces.",
            promise="Give small teams a dependable production cockpit for live moments that need to travel across channels.",
            mode="Real time",
            audience="Creators + producers",
            cadence="Live",
            features=(
                Feature("WebRTC studios", "Create low-latency rooms with explicit roles, device state, and connection health.", "Studio"),
                Feature("Guest contribution", "Use bounded guest links, green rooms, permissions, and producer-controlled promotion.", "Guests"),
                Feature("Multistream output", "Coordinate destinations, credentials, health, and failover without duplicating show state.", "Output"),
                Feature("Recording pipeline", "Track local and server-side recordings, segments, uploads, and recovery evidence.", "Record"),
                Feature("Live interaction", "Bring chat, prompts, moderation, and audience signals into the production workflow.", "Engage"),
                Feature("Operational telemetry", "Expose room, media, destination, and control-plane health in real time.", "Observe"),
            ),
            steps=(
                ("Prepare", "Configure the room, destinations, roles, media, and contingency plan."),
                ("Direct", "Manage participants and production state from one consistent control surface."),
                ("Recover", "Retain recordings, timelines, and telemetry so incidents can be diagnosed and shows preserved."),
            ),
            keywords=("live streaming", "WebRTC studio", "multistream", "remote production"),
        ),
        Site(
            org="hypesiege",
            title="HypeSiege",
            eyebrow="Campaign operations without signal loss",
            headline="Coordinate communities and live moments from one campaign graph.",
            summary="HypeSiege connects campaign planning, channel operations, audience state, assets, live coordination, and outcome telemetry for fast-moving launches and events.",
            promise="Keep the message, audience, timing, and operating state aligned while a campaign moves across channels.",
            mode="Campaign graph",
            audience="Growth + community teams",
            cadence="Moment driven",
            features=(
                Feature("Campaign graph", "Relate objectives, audiences, assets, channels, operators, and moments explicitly.", "Plan"),
                Feature("Channel operations", "Coordinate publication and moderation work without treating every channel as an island.", "Operate"),
                Feature("Audience state", "Track consent, segment membership, engagement, and suppression as durable state.", "Audience"),
                Feature("Live command", "Expose run-of-show, assignments, incidents, and escalation during high-intensity moments.", "Live"),
                Feature("Asset lineage", "Keep variants, approvals, rights, and destination usage attached to source assets.", "Assets"),
                Feature("Outcome telemetry", "Connect activity to reach, interaction, conversion, and operational quality.", "Measure"),
            ),
            steps=(
                ("Shape", "Define the campaign graph, operating constraints, audiences, and success signals."),
                ("Coordinate", "Run channel and live work against a shared schedule and ownership model."),
                ("Learn", "Reconcile outcomes and operating evidence into the next campaign iteration."),
            ),
            keywords=("campaign operations", "community growth", "live campaign", "audience orchestration"),
        ),
        Site(
            org="opto-sync",
            title="Opto Sync",
            eyebrow="Offline-first data with explicit conflict semantics",
            headline="Synchronize local and cloud state without pretending conflicts do not exist.",
            summary="Opto Sync provides reusable synchronization contracts and runtimes for IndexedDB, SQLite, PostgreSQL, Supabase, service workers, and mobile background execution.",
            promise="Make replication, validation, conflict policy, and recovery observable across local-first applications.",
            mode="Offline first",
            audience="Application platforms",
            cadence="Incremental",
            features=(
                Feature("Shared envelopes", "Carry operation identity, revisions, causality, validation, and retry state consistently.", "Contract"),
                Feature("Local stores", "Support browser and device persistence through IndexedDB and SQLite adapters.", "Local"),
                Feature("Cloud persistence", "Connect PostgreSQL and Supabase while preserving tenant and authorization boundaries.", "Cloud"),
                Feature("Conflict policies", "Choose optimistic, synchronous, merge, reject, or manual-resolution behavior explicitly.", "Conflict"),
                Feature("Background execution", "Continue bounded synchronization through service workers and mobile background tasks.", "Background"),
                Feature("Cross-runtime parity", "Validate equivalent behavior across TypeScript, Rust, Dart, Go, and Java clients.", "Parity"),
            ),
            steps=(
                ("Record", "Capture validated local operations with stable identity and causal metadata."),
                ("Reconcile", "Apply explicit server and client conflict policies against current state."),
                ("Recover", "Resume safely after offline periods, partial failures, or interrupted background work."),
            ),
            keywords=("offline first", "data synchronization", "conflict resolution", "SQLite PostgreSQL sync"),
        ),
        Site(
            org="ores-otel",
            title="Ores OTEL",
            eyebrow="Context-preserving observability across runtimes",
            headline="Keep logs, traces, and metrics connected when execution crosses boundaries.",
            summary="Ores OTEL develops aligned observability interfaces and libraries for Rust, TypeScript, Go, Dart, Gleam, Erlang, Java, and other runtimes.",
            promise="Make causal context portable across threads, async tasks, services, clients, and language boundaries.",
            mode="OpenTelemetry aligned",
            audience="Platform + SDK teams",
            cadence="Cross-runtime",
            features=(
                Feature("Unified context", "Carry trace, request, actor, tenant, and operation context through explicit contracts.", "Context"),
                Feature("Structured logging", "Emit consistent fields and severity semantics without flattening runtime-specific capabilities.", "Logs"),
                Feature("Trace propagation", "Preserve parentage across async, thread-local, network, queue, and callback boundaries.", "Traces"),
                Feature("Metric conventions", "Share stable names, units, dimensions, and cardinality policy across implementations.", "Metrics"),
                Feature("Language parity", "Certify equivalent public behavior through shared schemas and fixtures.", "Parity"),
                Feature("Migration paths", "Adopt Ores OTEL incrementally from existing logger and telemetry libraries.", "Adopt"),
            ),
            steps=(
                ("Declare", "Define fields, propagation rules, and semantic conventions in shared interfaces."),
                ("Instrument", "Apply runtime-native libraries without losing the cross-language contract."),
                ("Correlate", "Query logs, traces, and metrics through stable context and operation identity."),
            ),
            keywords=("OpenTelemetry", "structured logging", "distributed tracing", "multi-language observability"),
        ),
    )
}

REQUIRED_ORGS = (
    "embedded-alerts",
    "evento-globolo",
    "hacker-house-medellin",
    "apostille-me",
    "messaging-intel",
    "networking-components",
    "StreemPilot",
    "hypesiege",
    "opto-sync",
    "ores-otel",
)


def encoded(value: str) -> str:
    return urllib.parse.quote(value, safe="/")


def json_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def render_layout(site: Site) -> str:
    return f'''---
import '../styles/global.css';

interface Props {{
  title?: string;
  description?: string;
  image?: string;
}}

const {{
  title = '{site.title}',
  description = '{site.summary.replace("'", "\\'")}',
  image = '/social-card.svg',
}} = Astro.props;
const canonical = Astro.site ? new URL(Astro.url.pathname, Astro.site) : Astro.url;
const pageTitle = title === '{site.title}' ? title : `${{title}} · {site.title}`;
---
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="generator" content={{Astro.generator}} />
    <meta name="description" content={{description}} />
    <meta name="keywords" content="{', '.join(site.keywords)}" />
    <meta name="theme-color" content="#071329" />
    <meta name="x-site-org" content="{site.org}" />
    <meta name="x-site-bootstrap" content="{GENERATED_MARKER}" />
    <link rel="canonical" href={{canonical}} />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <link rel="manifest" href="/site.webmanifest" />
    <meta property="og:type" content="website" />
    <meta property="og:title" content={{pageTitle}} />
    <meta property="og:description" content={{description}} />
    <meta property="og:url" content={{canonical}} />
    <meta property="og:image" content={{new URL(image, canonical)}} />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content={{pageTitle}} />
    <meta name="twitter:description" content={{description}} />
    <meta name="twitter:image" content={{new URL(image, canonical)}} />
    <title>{{pageTitle}}</title>
  </head>
  <body>
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="site-header">
      <div class="shell header-inner">
        <a class="brand" href="/" aria-label="{site.title} home">
          <span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span>
          <span>{site.title}</span>
        </a>
        <nav class="primary-nav" aria-label="Primary navigation">
          <a href="/#capabilities">Capabilities</a>
          <a href="/#workflow">Workflow</a>
          <details>
            <summary>Project</summary>
            <div class="nav-menu">
              <a href="{site.org_url}">GitHub organization</a>
              <a href="{site.org_url}?tab=repositories">Repositories</a>
              <a href="/privacy/">Privacy</a>
            </div>
          </details>
          <a class="button button-small" href="{site.org_url}">Explore the code</a>
        </nav>
      </div>
    </header>
    <main id="main">
      <slot />
    </main>
    <footer class="site-footer">
      <div class="shell footer-grid">
        <div>
          <a class="brand footer-brand" href="/">
            <span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span>
            <span>{site.title}</span>
          </a>
          <p>{site.promise}</p>
        </div>
        <div>
          <strong>Project</strong>
          <a href="{site.org_url}">GitHub</a>
          <a href="{site.org_url}?tab=repositories">Repositories</a>
          <a href="/privacy/">Privacy</a>
        </div>
        <div>
          <strong>Network</strong>
          <a href="https://oresoftware.github.io/">ORESoftware projects</a>
          <a href="https://the1mills.github.io/">Alexander Mills</a>
        </div>
      </div>
      <div class="shell footer-bottom">
        <span>© {{new Date().getFullYear()}} {site.title}</span>
        <span>Built with Astro · Deployed on GitHub Pages</span>
      </div>
    </footer>
  </body>
</html>
'''


def render_index(site: Site) -> str:
    features = [dataclasses.asdict(feature) for feature in site.features]
    steps = [{"number": f"0{index}", "title": title, "body": body} for index, (title, body) in enumerate(site.steps, 1)]
    legal = f'<p class="legal-note">{html.escape(site.legal_note)}</p>' if site.legal_note else ""
    return f'''---
import SiteLayout from '../layouts/SiteLayout.astro';

const features = {json_script(features)};
const steps = {json_script(steps)};
const operatingSignals = [
  {{ label: 'Mode', value: '{site.mode}' }},
  {{ label: 'Built for', value: '{site.audience}' }},
  {{ label: 'Cadence', value: '{site.cadence}' }},
];
---
<SiteLayout>
  <section class="hero section-pad">
    <div class="shell hero-grid">
      <div class="hero-copy">
        <p class="eyebrow"><span></span>{site.eyebrow}</p>
        <h1>{site.headline}</h1>
        <p class="lede">{site.summary}</p>
        <div class="hero-actions">
          <a class="button" href="{site.org_url}">Explore the project <span aria-hidden="true">↗</span></a>
          <a class="text-link" href="#capabilities">See the capability map <span aria-hidden="true">↓</span></a>
        </div>
        <dl class="signal-strip">
          {{operatingSignals.map((signal) => (
            <div>
              <dt>{{signal.label}}</dt>
              <dd>{{signal.value}}</dd>
            </div>
          ))}}
        </dl>
      </div>
      <div class="hero-visual" aria-label="Abstract system map">
        <div class="visual-grid" aria-hidden="true"></div>
        <div class="orbit orbit-one" aria-hidden="true"></div>
        <div class="orbit orbit-two" aria-hidden="true"></div>
        <div class="system-card system-card-main">
          <span class="card-kicker">{site.title}</span>
          <strong>One operating model</strong>
          <p>{site.promise}</p>
          <div class="status-row"><span class="status-dot"></span> Public foundation online</div>
        </div>
        <div class="system-card system-card-a"><span>01</span><strong>Contracts</strong></div>
        <div class="system-card system-card-b"><span>02</span><strong>Runtime</strong></div>
        <div class="system-card system-card-c"><span>03</span><strong>Evidence</strong></div>
      </div>
    </div>
  </section>

  <section class="proof-band" aria-label="Project principles">
    <div class="shell proof-grid">
      <p><span>01</span> Explicit contracts</p>
      <p><span>02</span> Human-visible state</p>
      <p><span>03</span> Observable behavior</p>
      <p><span>04</span> Portable interfaces</p>
    </div>
  </section>

  <section id="capabilities" class="section-pad">
    <div class="shell">
      <div class="section-heading">
        <div>
          <p class="eyebrow"><span></span>Capability map</p>
          <h2>Focused components. Shared operating context.</h2>
        </div>
        <p>Each capability is designed to stand on its own while participating in a versioned, observable system.</p>
      </div>
      <div class="card-grid">
        {{features.map((feature, index) => (
          <article class="feature-card">
            <div class="feature-top"><span>{{String(index + 1).padStart(2, '0')}}</span><small>{{feature.tag}}</small></div>
            <h3>{{feature.title}}</h3>
            <p>{{feature.body}}</p>
            <span class="card-arrow" aria-hidden="true">↗</span>
          </article>
        ))}}
      </div>
    </div>
  </section>

  <section id="workflow" class="section-pad workflow-section">
    <div class="shell workflow-grid">
      <div class="workflow-copy">
        <p class="eyebrow"><span></span>Operating loop</p>
        <h2>Make state visible before it becomes operational debt.</h2>
        <p>{site.promise}</p>
        <a class="text-link" href="{site.org_url}?tab=repositories">Inspect the repository fleet <span aria-hidden="true">↗</span></a>
      </div>
      <ol class="steps">
        {{steps.map((step) => (
          <li>
            <span>{{step.number}}</span>
            <div><h3>{{step.title}}</h3><p>{{step.body}}</p></div>
          </li>
        ))}}
      </ol>
    </div>
  </section>

  <section class="section-pad architecture-section">
    <div class="shell architecture-grid">
      <div class="architecture-panel">
        <p class="eyebrow"><span></span>Architecture posture</p>
        <h2>Open at the edges. Deliberate at the boundaries.</h2>
        <p>The public organization is the durable map of interfaces, reusable libraries, clients, runtimes, infrastructure, and test evidence.</p>
        <div class="architecture-list">
          <div><span>Interfaces</span><strong>schemas · events · APIs</strong></div>
          <div><span>Libraries</span><strong>domain behavior · policy</strong></div>
          <div><span>Clients</span><strong>multi-runtime parity</strong></div>
          <div><span>Evidence</span><strong>tests · telemetry · ledgers</strong></div>
        </div>
      </div>
      <aside class="repository-card">
        <span class="card-kicker">GitHub organization</span>
        <h3>{site.org}</h3>
        <p>Follow implementation progress, interfaces, clients, deployment definitions, and test repositories in the public source graph.</p>
        <a class="button" href="{site.org_url}">Open GitHub <span aria-hidden="true">↗</span></a>
        <div class="repo-terminal" aria-label="Example repository command">
          <span>$</span><code>git ls-remote {site.org_url}</code>
        </div>
      </aside>
    </div>
  </section>

  <section class="section-pad cta-section">
    <div class="shell cta-panel">
      <div>
        <p class="eyebrow"><span></span>Public foundation</p>
        <h2>Build from the source graph, not a black box.</h2>
        <p>{site.summary}</p>
        {legal}
      </div>
      <div class="cta-actions">
        <a class="button button-light" href="{site.org_url}">Explore {site.title}</a>
        <a class="text-link text-link-light" href="https://oresoftware.github.io/">Browse the wider portfolio ↗</a>
      </div>
    </div>
  </section>
</SiteLayout>
'''


def render_privacy(site: Site) -> str:
    return f'''---
import SiteLayout from '../layouts/SiteLayout.astro';
---
<SiteLayout title="Privacy" description="Privacy information for {site.title}.">
  <section class="section-pad page-hero">
    <div class="shell narrow">
      <p class="eyebrow"><span></span>Privacy</p>
      <h1>Public site privacy</h1>
      <p class="lede">This static marketing site does not include application sign-in, advertising trackers, or a first-party analytics script.</p>
    </div>
  </section>
  <section class="section-pad page-content">
    <div class="shell narrow prose">
      <h2>GitHub Pages hosting</h2>
      <p>The site is delivered by GitHub Pages. GitHub may process request and security metadata under its own service terms and privacy practices.</p>
      <h2>Outbound links</h2>
      <p>Links to GitHub and related project sites take you to separate services with their own policies.</p>
      <h2>Application data</h2>
      <p>This public site is separate from any product application or service environment. Product-specific collection and retention rules must be documented in the relevant application repository and runtime.</p>
      <h2>Questions</h2>
      <p>Use the public GitHub organization to open an issue in the appropriate repository.</p>
      <a class="button" href="{site.org_url}">Open the organization ↗</a>
    </div>
  </section>
</SiteLayout>
'''


def render_404(site: Site) -> str:
    return f'''---
import SiteLayout from '../layouts/SiteLayout.astro';
---
<SiteLayout title="Page not found" description="The requested {site.title} page could not be found.">
  <section class="section-pad not-found">
    <div class="shell narrow">
      <p class="eyebrow"><span></span>404</p>
      <h1>This route is not part of the public map.</h1>
      <p class="lede">Return to the project overview or inspect the source organization.</p>
      <div class="hero-actions">
        <a class="button" href="/">Return home</a>
        <a class="text-link" href="{site.org_url}">Open GitHub ↗</a>
      </div>
    </div>
  </section>
</SiteLayout>
'''


def render_css() -> str:
    return r''':root {
  color-scheme: dark;
  --ink: #f5f8ff;
  --muted: #9babc8;
  --muted-strong: #c6d2e8;
  --night: #050b18;
  --navy: #071329;
  --panel: #0b1a35;
  --panel-strong: #10264a;
  --line: rgba(151, 187, 255, 0.17);
  --line-strong: rgba(151, 187, 255, 0.32);
  --blue: #64a8ff;
  --cyan: #71e1ff;
  --mint: #7fffd1;
  --shadow: 0 24px 80px rgba(0, 0, 0, 0.34);
  --radius: 20px;
  --shell: min(1180px, calc(100vw - 40px));
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; background: var(--night); }
body {
  margin: 0;
  min-width: 320px;
  overflow-x: hidden;
  color: var(--ink);
  background:
    radial-gradient(circle at 72% 8%, rgba(46, 118, 255, 0.15), transparent 32rem),
    radial-gradient(circle at 12% 36%, rgba(38, 220, 255, 0.08), transparent 28rem),
    var(--night);
  line-height: 1.6;
  text-rendering: optimizeLegibility;
}

::selection { color: #031020; background: var(--cyan); }
a { color: inherit; text-decoration: none; }
a, button, summary { -webkit-tap-highlight-color: transparent; }
img, svg { display: block; max-width: 100%; }
button, input, textarea, select { font: inherit; }

.shell { width: var(--shell); margin-inline: auto; }
.narrow { max-width: 780px; }
.section-pad { padding: 112px 0; }
.skip-link {
  position: fixed; z-index: 100; left: 16px; top: 16px; padding: 10px 16px;
  color: var(--night); background: var(--cyan); border-radius: 8px; transform: translateY(-180%);
}
.skip-link:focus { transform: translateY(0); }

.site-header {
  position: sticky; z-index: 50; top: 0;
  border-bottom: 1px solid rgba(151, 187, 255, 0.11);
  background: rgba(5, 11, 24, 0.78);
  backdrop-filter: blur(20px) saturate(140%);
}
.header-inner { min-height: 72px; display: flex; align-items: center; justify-content: space-between; gap: 24px; }
.brand { display: inline-flex; align-items: center; gap: 12px; font-size: 0.95rem; font-weight: 760; letter-spacing: -0.01em; }
.brand-mark { position: relative; width: 30px; height: 30px; display: grid; place-items: center; }
.brand-mark::before { content: ""; position: absolute; inset: 0; border: 1px solid var(--line-strong); border-radius: 9px; transform: rotate(45deg); }
.brand-mark span { position: absolute; width: 5px; height: 5px; border-radius: 999px; background: var(--cyan); box-shadow: 0 0 18px rgba(113, 225, 255, 0.7); }
.brand-mark span:nth-child(1) { transform: translate(-7px, 4px); }
.brand-mark span:nth-child(2) { transform: translate(7px, -4px); background: var(--blue); }
.brand-mark span:nth-child(3) { width: 4px; height: 16px; border-radius: 4px; transform: rotate(45deg); background: linear-gradient(var(--blue), var(--mint)); opacity: 0.65; }
.primary-nav { display: flex; align-items: center; gap: 26px; color: var(--muted-strong); font-size: 0.88rem; }
.primary-nav > a:not(.button), .primary-nav summary { transition: color 160ms ease; }
.primary-nav > a:not(.button):hover, .primary-nav summary:hover { color: var(--ink); }
.primary-nav details { position: relative; }
.primary-nav summary { cursor: pointer; list-style: none; }
.primary-nav summary::-webkit-details-marker { display: none; }
.primary-nav summary::after { content: "⌄"; margin-left: 6px; color: var(--blue); }
.nav-menu {
  position: absolute; right: 0; top: calc(100% + 16px); width: 210px; padding: 10px;
  border: 1px solid var(--line); border-radius: 14px; background: rgba(8, 20, 42, 0.98); box-shadow: var(--shadow);
}
.nav-menu a { display: block; padding: 10px 12px; border-radius: 9px; }
.nav-menu a:hover { background: rgba(100, 168, 255, 0.1); }

.button {
  display: inline-flex; align-items: center; justify-content: center; gap: 9px; min-height: 48px; padding: 0 22px;
  border: 1px solid rgba(113, 225, 255, 0.36); border-radius: 999px;
  color: #f9fdff; background: linear-gradient(135deg, rgba(45, 112, 246, 0.92), rgba(26, 150, 205, 0.88));
  box-shadow: 0 12px 36px rgba(30, 112, 225, 0.25); font-weight: 750; font-size: 0.9rem;
  transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
}
.button:hover { transform: translateY(-2px); border-color: rgba(113, 225, 255, 0.72); box-shadow: 0 18px 44px rgba(30, 112, 225, 0.34); }
.button-small { min-height: 38px; padding: 0 17px; font-size: 0.82rem; }
.button-light { color: #041124; background: #f5f8ff; border-color: #fff; box-shadow: 0 14px 44px rgba(0, 0, 0, 0.18); }
.text-link { display: inline-flex; align-items: center; gap: 8px; color: var(--muted-strong); font-weight: 690; font-size: 0.91rem; }
.text-link:hover { color: var(--cyan); }
.text-link-light { color: rgba(255, 255, 255, 0.82); }
:focus-visible { outline: 3px solid var(--cyan); outline-offset: 4px; border-radius: 6px; }

.hero { position: relative; padding-top: 96px; }
.hero-grid { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(420px, 0.95fr); gap: 72px; align-items: center; }
.hero-copy { min-width: 0; }
.eyebrow { display: flex; align-items: center; gap: 10px; margin: 0 0 22px; color: var(--cyan); font-size: 0.76rem; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase; }
.eyebrow > span { width: 24px; height: 1px; background: linear-gradient(90deg, var(--cyan), transparent); }
h1, h2, h3 { margin: 0; line-height: 1.04; letter-spacing: -0.045em; text-wrap: balance; }
h1 { max-width: 770px; font-size: clamp(3.35rem, 6.5vw, 6.8rem); font-weight: 760; }
h2 { font-size: clamp(2.25rem, 4.3vw, 4.25rem); font-weight: 730; }
h3 { font-size: 1.35rem; letter-spacing: -0.025em; }
.lede { max-width: 670px; margin: 28px 0 0; color: var(--muted-strong); font-size: clamp(1.06rem, 1.5vw, 1.28rem); line-height: 1.75; }
.hero-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 22px; margin-top: 38px; }
.signal-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0; margin: 52px 0 0; padding-top: 24px; border-top: 1px solid var(--line); }
.signal-strip div { min-width: 0; padding-right: 18px; }
.signal-strip div + div { padding-left: 18px; border-left: 1px solid var(--line); }
.signal-strip dt { color: var(--muted); font-size: 0.72rem; letter-spacing: 0.1em; text-transform: uppercase; }
.signal-strip dd { margin: 5px 0 0; color: var(--ink); font-size: 0.88rem; font-weight: 700; overflow-wrap: anywhere; }

.hero-visual { position: relative; min-height: 560px; isolation: isolate; }
.visual-grid { position: absolute; inset: 0; border: 1px solid var(--line); border-radius: 32px; background-image: linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px); background-size: 44px 44px; mask-image: radial-gradient(circle at center, black 22%, transparent 76%); opacity: 0.55; }
.orbit { position: absolute; border: 1px solid rgba(113, 225, 255, 0.2); border-radius: 50%; }
.orbit-one { width: 380px; height: 380px; top: 74px; left: 46px; }
.orbit-two { width: 250px; height: 250px; top: 142px; left: 110px; border-style: dashed; animation: rotate 30s linear infinite; }
.system-card { position: absolute; border: 1px solid var(--line-strong); border-radius: 16px; background: linear-gradient(145deg, rgba(15, 39, 75, 0.94), rgba(7, 19, 41, 0.94)); box-shadow: var(--shadow); backdrop-filter: blur(14px); }
.system-card-main { z-index: 2; width: min(360px, calc(100% - 72px)); left: 50%; top: 50%; padding: 30px; transform: translate(-50%, -50%); }
.card-kicker { display: block; color: var(--cyan); font-size: 0.7rem; font-weight: 800; letter-spacing: 0.15em; text-transform: uppercase; }
.system-card-main strong { display: block; margin-top: 16px; font-size: 1.6rem; line-height: 1.15; }
.system-card-main p { margin: 14px 0 22px; color: var(--muted-strong); font-size: 0.9rem; }
.status-row { display: flex; align-items: center; gap: 9px; padding-top: 18px; border-top: 1px solid var(--line); color: var(--muted-strong); font-size: 0.78rem; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--mint); box-shadow: 0 0 16px rgba(127, 255, 209, 0.8); }
.system-card-a, .system-card-b, .system-card-c { z-index: 3; display: flex; align-items: center; gap: 10px; padding: 13px 15px; font-size: 0.77rem; }
.system-card-a span, .system-card-b span, .system-card-c span { color: var(--cyan); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.system-card-a { top: 62px; right: 6px; }
.system-card-b { left: 0; bottom: 86px; }
.system-card-c { right: 18px; bottom: 34px; }
@keyframes rotate { to { transform: rotate(360deg); } }

.proof-band { border-block: 1px solid var(--line); background: rgba(9, 24, 50, 0.46); }
.proof-grid { display: grid; grid-template-columns: repeat(4, 1fr); }
.proof-grid p { margin: 0; padding: 23px 20px; color: var(--muted-strong); font-size: 0.78rem; font-weight: 650; letter-spacing: 0.03em; text-align: center; }
.proof-grid p + p { border-left: 1px solid var(--line); }
.proof-grid span { margin-right: 8px; color: var(--blue); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

.section-heading { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.65fr); gap: 56px; align-items: end; margin-bottom: 52px; }
.section-heading > p { margin: 0 0 4px; color: var(--muted); font-size: 0.98rem; line-height: 1.75; }
.card-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.feature-card { position: relative; min-height: 285px; padding: 28px; overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius); background: linear-gradient(145deg, rgba(12, 31, 63, 0.78), rgba(7, 19, 41, 0.66)); transition: transform 180ms ease, border-color 180ms ease, background 180ms ease; }
.feature-card::after { content: ""; position: absolute; width: 150px; height: 150px; right: -80px; top: -80px; border-radius: 50%; background: rgba(100, 168, 255, 0.08); filter: blur(2px); transition: transform 180ms ease; }
.feature-card:hover { transform: translateY(-5px); border-color: var(--line-strong); background: linear-gradient(145deg, rgba(16, 43, 83, 0.9), rgba(7, 19, 41, 0.8)); }
.feature-card:hover::after { transform: scale(1.45); }
.feature-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 54px; color: var(--blue); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.73rem; }
.feature-top small { padding: 5px 9px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted-strong); font: 700 0.65rem/1 Inter, sans-serif; letter-spacing: 0.06em; text-transform: uppercase; }
.feature-card p { margin: 16px 0 0; color: var(--muted); font-size: 0.9rem; line-height: 1.72; }
.card-arrow { position: absolute; right: 24px; bottom: 20px; color: var(--cyan); opacity: 0.65; }

.workflow-section { border-block: 1px solid var(--line); background: linear-gradient(135deg, rgba(8, 24, 52, 0.88), rgba(5, 12, 27, 0.72)); }
.workflow-grid { display: grid; grid-template-columns: minmax(0, 0.82fr) minmax(400px, 1.18fr); gap: 92px; align-items: start; }
.workflow-copy { position: sticky; top: 128px; }
.workflow-copy > p:not(.eyebrow) { margin: 26px 0 32px; color: var(--muted); font-size: 1rem; line-height: 1.78; }
.steps { margin: 0; padding: 0; list-style: none; }
.steps li { display: grid; grid-template-columns: 68px 1fr; gap: 26px; padding: 34px 0; border-top: 1px solid var(--line); }
.steps li:last-child { border-bottom: 1px solid var(--line); }
.steps > li > span { color: var(--cyan); font: 700 0.78rem/1 ui-monospace, SFMono-Regular, Menlo, monospace; }
.steps h3 { font-size: 1.65rem; }
.steps p { margin: 12px 0 0; color: var(--muted); }

.architecture-grid { display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 22px; }
.architecture-panel, .repository-card { border: 1px solid var(--line); border-radius: 26px; background: rgba(9, 24, 49, 0.62); }
.architecture-panel { padding: 48px; }
.architecture-panel > p:not(.eyebrow) { max-width: 700px; margin: 24px 0 36px; color: var(--muted); }
.architecture-list { border-top: 1px solid var(--line); }
.architecture-list div { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 17px 0; border-bottom: 1px solid var(--line); }
.architecture-list span { color: var(--muted-strong); }
.architecture-list strong { color: var(--blue); font: 650 0.76rem/1.3 ui-monospace, SFMono-Regular, Menlo, monospace; text-align: right; }
.repository-card { display: flex; flex-direction: column; align-items: flex-start; padding: 40px; background: linear-gradient(160deg, rgba(16, 47, 91, 0.9), rgba(7, 19, 41, 0.92)); }
.repository-card h3 { margin-top: 22px; font-size: clamp(2rem, 4vw, 3.3rem); overflow-wrap: anywhere; }
.repository-card p { margin: 20px 0 30px; color: var(--muted-strong); }
.repo-terminal { width: 100%; margin-top: auto; padding: 18px; overflow: hidden; border: 1px solid var(--line); border-radius: 13px; background: rgba(3, 10, 22, 0.78); color: var(--muted); font: 0.72rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; text-overflow: ellipsis; }
.repo-terminal span { margin-right: 8px; color: var(--mint); }

.cta-section { padding-top: 24px; }
.cta-panel { display: grid; grid-template-columns: minmax(0, 1.25fr) auto; gap: 64px; align-items: center; padding: 64px; overflow: hidden; border: 1px solid rgba(113, 225, 255, 0.3); border-radius: 30px; background: linear-gradient(135deg, #1358b7, #0b7da2 58%, #087378); box-shadow: 0 30px 90px rgba(3, 64, 125, 0.32); }
.cta-panel .eyebrow { color: #e5fbff; }
.cta-panel > div > p:not(.eyebrow):not(.legal-note) { max-width: 720px; margin: 22px 0 0; color: rgba(255, 255, 255, 0.85); }
.legal-note { margin: 18px 0 0; color: rgba(255, 255, 255, 0.7); font-size: 0.76rem; }
.cta-actions { display: flex; flex-direction: column; align-items: center; gap: 16px; }

.site-footer { margin-top: 112px; padding: 68px 0 28px; border-top: 1px solid var(--line); background: #040914; }
.footer-grid { display: grid; grid-template-columns: 1.6fr 0.7fr 0.7fr; gap: 64px; }
.footer-grid > div { display: flex; flex-direction: column; align-items: flex-start; gap: 11px; }
.footer-grid p { max-width: 440px; margin: 8px 0 0; color: var(--muted); font-size: 0.87rem; }
.footer-grid strong { margin-bottom: 5px; font-size: 0.78rem; letter-spacing: 0.08em; text-transform: uppercase; }
.footer-grid a:not(.brand) { color: var(--muted); font-size: 0.84rem; }
.footer-grid a:hover { color: var(--cyan); }
.footer-bottom { display: flex; justify-content: space-between; gap: 24px; margin-top: 48px; padding-top: 22px; border-top: 1px solid var(--line); color: #71819d; font-size: 0.72rem; }

.page-hero { padding-bottom: 58px; }
.page-hero h1, .not-found h1 { font-size: clamp(3rem, 7vw, 6.2rem); }
.page-content { padding-top: 10px; }
.prose h2 { margin: 42px 0 12px; font-size: 1.7rem; }
.prose p { color: var(--muted-strong); }
.prose .button { margin-top: 24px; }
.not-found { min-height: 72vh; display: grid; align-items: center; }

@media (max-width: 1040px) {
  .hero-grid { grid-template-columns: 1fr; gap: 58px; }
  .hero-visual { width: min(620px, 100%); margin-inline: auto; }
  .card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .workflow-grid { grid-template-columns: 1fr; gap: 56px; }
  .workflow-copy { position: static; }
  .architecture-grid { grid-template-columns: 1fr; }
  .cta-panel { grid-template-columns: 1fr; }
  .cta-actions { align-items: flex-start; }
}

@media (max-width: 760px) {
  :root { --shell: min(100% - 28px, 1180px); }
  .section-pad { padding: 78px 0; }
  .site-header { position: relative; }
  .header-inner { min-height: 66px; }
  .primary-nav > a:not(.button), .primary-nav details { display: none; }
  .button-small { min-height: 36px; padding-inline: 14px; }
  .hero { padding-top: 64px; }
  h1 { font-size: clamp(3rem, 15vw, 4.7rem); }
  .signal-strip { grid-template-columns: 1fr; gap: 14px; }
  .signal-strip div, .signal-strip div + div { padding: 0; border-left: 0; }
  .hero-visual { min-height: 470px; }
  .orbit-one { width: 310px; height: 310px; left: 50%; transform: translateX(-50%); }
  .orbit-two { width: 210px; height: 210px; left: 50%; transform: translateX(-50%); animation: none; }
  .system-card-a { right: 0; }
  .system-card-b { left: 0; bottom: 54px; }
  .system-card-c { right: 4px; bottom: 4px; }
  .proof-grid { grid-template-columns: repeat(2, 1fr); }
  .proof-grid p:nth-child(3) { border-left: 0; border-top: 1px solid var(--line); }
  .proof-grid p:nth-child(4) { border-top: 1px solid var(--line); }
  .section-heading { grid-template-columns: 1fr; gap: 24px; }
  .card-grid { grid-template-columns: 1fr; }
  .feature-card { min-height: 245px; }
  .steps li { grid-template-columns: 46px 1fr; gap: 16px; }
  .architecture-panel, .repository-card { padding: 28px; }
  .architecture-list div { align-items: flex-start; flex-direction: column; gap: 6px; }
  .architecture-list strong { text-align: left; }
  .cta-panel { padding: 38px 26px; }
  .footer-grid { grid-template-columns: 1fr 1fr; }
  .footer-grid > div:first-child { grid-column: 1 / -1; }
  .footer-bottom { flex-direction: column; }
}

@media (max-width: 460px) {
  .primary-nav .button { display: none; }
  .hero-actions { align-items: flex-start; flex-direction: column; }
  .hero-visual { min-height: 430px; }
  .system-card-main { width: calc(100% - 32px); padding: 24px; }
  .system-card-a, .system-card-b, .system-card-c { font-size: 0.68rem; }
  .footer-grid { grid-template-columns: 1fr; }
  .footer-grid > div:first-child { grid-column: auto; }
}

@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { animation-duration: 0.01ms !important; animation-iteration-count: 1 !important; transition-duration: 0.01ms !important; }
}
'''


def render_favicon(site: Site) -> str:
    initials = "".join(word[0] for word in re.findall(r"[A-Za-z0-9]+", site.title)[:2]).upper() or "O"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#4f83ff"/><stop offset="1" stop-color="#6fffe0"/></linearGradient></defs>
  <rect width="64" height="64" rx="16" fill="#071329"/>
  <path d="M16 32 32 16l16 16-16 16z" fill="none" stroke="url(#g)" stroke-width="3"/>
  <text x="32" y="37" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" font-weight="800" fill="#f5f8ff">{initials}</text>
</svg>
'''


def render_social_card(site: Site) -> str:
    safe_title = html.escape(site.title)
    safe_headline = html.escape(site.headline)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#050b18"/><stop offset="1" stop-color="#102c59"/></linearGradient>
    <radialGradient id="glow"><stop stop-color="#5dc9ff" stop-opacity=".32"/><stop offset="1" stop-color="#5dc9ff" stop-opacity="0"/></radialGradient>
  </defs>
  <rect width="1200" height="630" fill="url(#bg)"/>
  <circle cx="960" cy="80" r="420" fill="url(#glow)"/>
  <g opacity=".18" stroke="#8bbcff"><path d="M0 90h1200M0 180h1200M0 270h1200M0 360h1200M0 450h1200M0 540h1200"/><path d="M120 0v630M240 0v630M360 0v630M480 0v630M600 0v630M720 0v630M840 0v630M960 0v630M1080 0v630"/></g>
  <rect x="78" y="76" width="56" height="56" rx="14" fill="none" stroke="#70ddff" stroke-width="2" transform="rotate(45 106 104)"/>
  <text x="78" y="196" font-family="system-ui,sans-serif" font-size="30" font-weight="750" fill="#71e1ff">{safe_title}</text>
  <foreignObject x="78" y="238" width="930" height="270"><div xmlns="http://www.w3.org/1999/xhtml" style="font:750 68px/1.06 system-ui,sans-serif;color:#f5f8ff;letter-spacing:-3px">{safe_headline}</div></foreignObject>
  <text x="78" y="566" font-family="ui-monospace,monospace" font-size="18" fill="#9babc8">{site.org.lower()}.github.io</text>
</svg>
'''


def render_pages_workflow() -> str:
    return f'''name: Deploy Astro to GitHub Pages

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    steps:
      - name: Check out source
        uses: actions/checkout@{CHECKOUT_SHA} # v7.0.1
        with:
          persist-credentials: false

      - name: Build and upload Astro site
        uses: withastro/action@{ASTRO_ACTION_SHA} # v6.1.1
        with:
          node-version: 24
          package-manager: npm@11

  deploy:
    needs: build
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    environment:
      name: github-pages
      url: ${{{{ steps.deployment.outputs.page_url }}}}
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@{DEPLOY_PAGES_SHA} # v4
'''


def generated_files(site: Site) -> dict[str, str]:
    package = {
        "name": site.repo,
        "version": "0.1.0",
        "private": True,
        "type": "module",
        "engines": {"node": ">=24.0.0"},
        "scripts": {
            "dev": "astro dev",
            "build": "astro build",
            "preview": "astro preview",
            "test": "astro build",
        },
        "devDependencies": {"astro": ASTRO_VERSION},
    }
    manifest = {
        "name": site.title,
        "short_name": site.title[:28],
        "start_url": "/",
        "display": "standalone",
        "background_color": "#050b18",
        "theme_color": "#071329",
        "icons": [{"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }
    readme = f'''# {site.repo}

Astro marketing site for [{site.title}]({site.page_url}).

## Local development

```bash
npm install
npm run dev
```

## Production build

```bash
npm test
```

The `main` branch deploys through the pinned Astro GitHub Pages workflow in `.github/workflows/pages.yml`.

Generated and hardened by `{GENERATED_MARKER}`.
'''
    astro_config = f'''import {{ defineConfig }} from 'astro/config';

export default defineConfig({{
  site: '{site.page_url}',
  output: 'static',
  trailingSlash: 'always',
}});
'''
    return {
        ".github/workflows/pages.yml": render_pages_workflow(),
        ".gitignore": "node_modules/\ndist/\n.astro/\n.env\n.env.*\n!.env.example\n.DS_Store\n",
        "README.md": readme,
        "astro.config.mjs": astro_config,
        "package.json": json.dumps(package, indent=2, ensure_ascii=False) + "\n",
        "tsconfig.json": json.dumps({"extends": "astro/tsconfigs/strict"}, indent=2) + "\n",
        "public/favicon.svg": render_favicon(site),
        "public/robots.txt": f"User-agent: *\nAllow: /\n\nSitemap: {site.page_url}sitemap.xml\n",
        "public/site.webmanifest": json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        "public/sitemap.xml": f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/sitemap/0.9">
  <url><loc>{site.page_url}</loc></url>
  <url><loc>{site.page_url}privacy/</loc></url>
</urlset>
''',
        "public/social-card.svg": render_social_card(site),
        "src/layouts/SiteLayout.astro": render_layout(site),
        "src/pages/404.astro": render_404(site),
        "src/pages/index.astro": render_index(site),
        "src/pages/privacy.astro": render_privacy(site),
        "src/styles/global.css": render_css(),
    }


def repository_path(site: Site) -> str:
    return f"/repos/{encoded(site.full_name)}"


def ensure_repository(gh: GitHub, site: Site) -> tuple[dict[str, Any], bool]:
    path = repository_path(site)
    status, data = gh.get(path, allow=(404,))
    created = False
    if status == 404:
        _, data = gh.post(
            f"/orgs/{urllib.parse.quote(site.org, safe='')}/repos",
            {
                "name": site.repo,
                "description": f"Astro marketing site for {site.title}.",
                "homepage": site.page_url,
                "private": False,
                "visibility": "public",
                "has_issues": True,
                "has_projects": False,
                "has_wiki": False,
                "auto_init": True,
                "allow_squash_merge": True,
                "allow_merge_commit": True,
                "allow_rebase_merge": False,
                "delete_branch_on_merge": True,
            },
        )
        created = True

    assert isinstance(data, dict)
    if data.get("private") or data.get("visibility") != "public":
        raise RuntimeError(f"Refusing to publish non-public repository {site.full_name}")
    actual_full = str(data.get("full_name", ""))
    if actual_full.lower() != site.full_name.lower():
        raise RuntimeError(f"Repository identity mismatch: expected {site.full_name}, observed {actual_full}")

    _, data = gh.patch(
        path,
        {
            "description": data.get("description") or f"Astro marketing site for {site.title}.",
            "homepage": site.page_url,
            "has_issues": True,
            "has_projects": False,
            "has_wiki": False,
            "allow_squash_merge": True,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
        },
    )
    return data, created


def wait_for_main(gh: GitHub, site: Site) -> tuple[str, str]:
    base = encoded(site.full_name)
    for attempt in range(30):
        status, ref = gh.get(f"/repos/{base}/git/ref/heads/main", allow=(404, 409))
        if status == 200:
            sha = ref["object"]["sha"]
            _, commit = gh.get(f"/repos/{base}/git/commits/{sha}")
            return sha, commit["tree"]["sha"]
        time.sleep(min(1 + attempt // 3, 5))
    raise RuntimeError(f"main did not initialize for {site.full_name}")


def read_file(gh: GitHub, site: Site, path: str) -> tuple[str | None, str | None]:
    status, data = gh.get(f"/repos/{encoded(site.full_name)}/contents/{encoded(path)}?ref=main", allow=(404,))
    if status == 404:
        return None, None
    if not isinstance(data, dict) or data.get("type") != "file":
        raise RuntimeError(f"Expected file at {site.full_name}:{path}")
    raw = base64.b64decode(data["content"])
    return raw.decode("utf-8"), data.get("sha")


def detect_astro(gh: GitHub, site: Site) -> tuple[bool, dict[str, bool]]:
    evidence: dict[str, bool] = {}
    package_text, _ = read_file(gh, site, "package.json")
    if package_text:
        try:
            package = json.loads(package_text)
            evidence["package"] = "astro" in package.get("dependencies", {}) or "astro" in package.get("devDependencies", {})
        except json.JSONDecodeError:
            evidence["package"] = False
    else:
        evidence["package"] = False
    config, _ = read_file(gh, site, "astro.config.mjs")
    evidence["config"] = bool(config and "astro/config" in config)
    index, _ = read_file(gh, site, "src/pages/index.astro")
    evidence["index"] = bool(index and "<" in index)
    return all(evidence.values()), evidence


def commit_files(gh: GitHub, site: Site, content: dict[str, str], message: str) -> tuple[str, list[str]]:
    base_sha, tree_sha = wait_for_main(gh, site)
    entries: list[dict[str, str]] = []
    changed: list[str] = []
    base = encoded(site.full_name)

    for path in sorted(content):
        current, _ = read_file(gh, site, path)
        if current == content[path]:
            continue
        _, blob = gh.post(
            f"/repos/{base}/git/blobs",
            {"content": content[path], "encoding": "utf-8"},
        )
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        changed.append(path)

    if not entries:
        return base_sha, []

    _, tree = gh.post(f"/repos/{base}/git/trees", {"base_tree": tree_sha, "tree": entries})
    _, commit = gh.post(
        f"/repos/{base}/git/commits",
        {"message": message, "tree": tree["sha"], "parents": [base_sha]},
    )
    commit_sha = commit["sha"]
    gh.patch(f"/repos/{base}/git/refs/heads/main", {"sha": commit_sha, "force": False})
    return commit_sha, changed


def configure_pages(gh: GitHub, site: Site) -> dict[str, Any]:
    path = f"/repos/{encoded(site.full_name)}/pages"
    status, existing = gh.get(path, allow=(404,))
    if status == 404:
        try:
            _, existing = gh.post(path, {"build_type": "workflow"})
        except ApiError as error:
            if error.status != 422:
                raise
            gh.post(path, {"source": {"branch": "main", "path": "/"}})
            _, existing = gh.put(path, {"build_type": "workflow"})
    else:
        _, existing = gh.put(path, {"build_type": "workflow"})

    try:
        gh.put(path, {"https_enforced": True})
    except ApiError as error:
        if error.status not in (409, 422):
            raise
    _, current = gh.get(path)
    if current.get("build_type") != "workflow":
        raise RuntimeError(f"Pages build_type did not converge to workflow for {site.full_name}")
    return current


def dispatch_pages(gh: GitHub, site: Site) -> dt.datetime:
    requested = dt.datetime.now(dt.timezone.utc)
    gh.post(
        f"/repos/{encoded(site.full_name)}/actions/workflows/pages.yml/dispatches",
        {"ref": "main"},
        allow=(204,),
    )
    return requested


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def wait_for_successful_run(
    gh: GitHub,
    site: Site,
    commit_sha: str,
    requested_at: dt.datetime,
    *,
    timeout_seconds: int = 720,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_seen: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        _, data = gh.get(
            f"/repos/{encoded(site.full_name)}/actions/workflows/pages.yml/runs?branch=main&per_page=20"
        )
        runs = data.get("workflow_runs", []) if isinstance(data, dict) else []
        eligible = []
        for run in runs:
            created = parse_time(run["created_at"])
            if run.get("head_sha") == commit_sha and created >= requested_at - dt.timedelta(minutes=3):
                eligible.append(run)
        eligible.sort(key=lambda item: item.get("run_number", 0), reverse=True)
        for run in eligible:
            last_seen = run
            if run.get("status") == "completed" and run.get("conclusion") == "success":
                return run
        terminal = [run for run in eligible if run.get("status") == "completed"]
        active = [run for run in eligible if run.get("status") != "completed"]
        if terminal and not active and all(run.get("conclusion") != "success" for run in terminal):
            conclusions = ", ".join(str(run.get("conclusion")) for run in terminal[:3])
            raise RuntimeError(f"Pages workflow completed without success for {site.full_name}: {conclusions}")
        time.sleep(10)
    detail = "no matching workflow run observed" if last_seen is None else f"last run {last_seen.get('html_url')} status={last_seen.get('status')} conclusion={last_seen.get('conclusion')}"
    raise RuntimeError(f"Timed out waiting for Pages workflow on {site.full_name}: {detail}")


def fetch_live(url: str) -> tuple[int, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read(1_500_000).decode("utf-8", errors="replace")
        return response.status, response.geturl(), body


def wait_for_live(site: Site, *, generated: bool, timeout_seconds: int = 420) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not requested"
    while time.monotonic() < deadline:
        try:
            status, resolved, body = fetch_live(site.page_url)
            marker_ok = (f'content="{site.org}"' in body or f"content='{site.org}'" in body) if generated else True
            astro_ok = True if not generated else ("Astro" in body or "astro" in body)
            if status == 200 and marker_ok and astro_ok:
                return {
                    "status": status,
                    "resolved_url": resolved,
                    "marker_verified": marker_ok,
                    "astro_verified": astro_ok,
                }
            last_error = f"status={status}, marker={marker_ok}, astro={astro_ok}, resolved={resolved}"
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
        except urllib.error.URLError as error:
            last_error = f"transport {error.reason}"
        time.sleep(10)
    raise RuntimeError(f"Live verification failed for {site.page_url}: {last_error}")


def bootstrap(gh: GitHub, site: Site) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    repository, created = ensure_repository(gh, site)
    astro_before, evidence = detect_astro(gh, site)

    all_files = generated_files(site)
    if created or not astro_before:
        desired = all_files
        source_action = "generated" if created else "converted_to_astro"
        generated = True
    else:
        desired = {".github/workflows/pages.yml": all_files[".github/workflows/pages.yml"]}
        source_action = "preserved_existing_astro"
        generated = False

    commit_sha, changed_paths = commit_files(
        gh,
        site,
        desired,
        f"deploy: standardize Astro GitHub Pages for {site.title}",
    )
    pages = configure_pages(gh, site)
    requested_at = dispatch_pages(gh, site)
    run = wait_for_successful_run(gh, site, commit_sha, requested_at)
    live = wait_for_live(site, generated=generated)
    _, pages_after = gh.get(f"/repos/{encoded(site.full_name)}/pages")

    completed = dt.datetime.now(dt.timezone.utc)
    return {
        "schema_version": 1,
        "organization": site.org,
        "repository": repository.get("full_name"),
        "repository_url": repository.get("html_url"),
        "repository_created": created,
        "source_action": source_action,
        "astro_evidence_before": evidence,
        "changed_paths": changed_paths,
        "commit_sha": commit_sha,
        "pages_url": pages_after.get("html_url") or pages.get("html_url") or site.page_url,
        "pages_build_type": pages_after.get("build_type"),
        "pages_status": pages_after.get("status"),
        "https_enforced": pages_after.get("https_enforced"),
        "workflow_run_id": run.get("id"),
        "workflow_run_url": run.get("html_url"),
        "workflow_conclusion": run.get("conclusion"),
        "live": live,
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
        "duration_seconds": round((completed - started).total_seconds(), 3),
    }


def select_site(org: str) -> Site:
    key = org.strip().lower()
    if key not in SITES:
        allowed = ", ".join(REQUIRED_ORGS)
        raise SystemExit(f"unsupported organization {org!r}; allowed: {allowed}")
    return SITES[key]


def write_rendered(site: Site, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for relative, content in generated_files(site).items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def self_test() -> None:
    assert len(SITES) == len(REQUIRED_ORGS) == 10
    assert {item.lower() for item in REQUIRED_ORGS} == set(SITES)
    seen_repos: set[str] = set()
    for requested in REQUIRED_ORGS:
        site = select_site(requested)
        assert site.full_name.lower() not in seen_repos
        seen_repos.add(site.full_name.lower())
        assert len(site.features) == 6
        assert len(site.steps) == 3
        assert len(site.keywords) >= 3
        files = generated_files(site)
        required = {
            ".github/workflows/pages.yml",
            "astro.config.mjs",
            "package.json",
            "src/pages/index.astro",
            "src/layouts/SiteLayout.astro",
            "src/styles/global.css",
            "public/favicon.svg",
        }
        assert required.issubset(files)
        assert f'content="{site.org}"' in files["src/layouts/SiteLayout.astro"]
        assert ASTRO_VERSION in files["package.json"]
        assert ASTRO_ACTION_SHA in files[".github/workflows/pages.yml"]
        assert DEPLOY_PAGES_SHA in files[".github/workflows/pages.yml"]
        corpus = "\n".join(files.values())
        if re.search(r"gh[pousr]_[A-Za-z0-9]{20,}|lin_api_[A-Za-z0-9]{20,}", corpus):
            raise AssertionError(f"credential-shaped content generated for {site.org}")
        if any(marker in corpus for marker in ("<<<<<<<", "=======", ">>>>>>>")):
            raise AssertionError(f"conflict marker generated for {site.org}")
    print(json.dumps({"sites": len(SITES), "astro_version": ASTRO_VERSION, "status": "ok"}, sort_keys=True))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create, repair, deploy, and verify an allowlisted Astro GitHub Pages site.")
    parser.add_argument("--org", help="Exact allowlisted GitHub organization login")
    parser.add_argument("--result", type=Path, help="Path for the JSON result ledger")
    parser.add_argument("--render-dir", type=Path, help="Render the selected site locally without GitHub access")
    parser.add_argument("--self-test", action="store_true", help="Validate all allowlisted templates without network access")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        self_test()
        return 0
    if not args.org:
        parser.error("--org is required unless --self-test is used")
    site = select_site(args.org)
    if args.render_dir:
        write_rendered(site, args.render_dir)
        print(json.dumps({"organization": site.org, "render_dir": str(args.render_dir), "files": len(generated_files(site))}, sort_keys=True))
        return 0
    if not args.result:
        parser.error("--result is required for live bootstrap")

    token = os.environ.get("GH_TOKEN", "").strip()
    if len(token) < 20 or any(character.isspace() for character in token):
        raise SystemExit("GH_TOKEN is missing or malformed")
    gh = GitHub(token)
    token = ""
    try:
        _, profile = gh.get("/user")
        result = bootstrap(gh, site)
        result["authenticated_login"] = profile.get("login")
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({
            "organization": result["organization"],
            "repository": result["repository"],
            "repository_created": result["repository_created"],
            "source_action": result["source_action"],
            "pages_url": result["pages_url"],
            "workflow_conclusion": result["workflow_conclusion"],
            "live_status": result["live"]["status"],
        }, sort_keys=True))
        return 0
    except Exception as error:
        failure = {
            "schema_version": 1,
            "organization": site.org,
            "repository": site.full_name,
            "status": "failed",
            "error_type": type(error).__name__,
            "error": str(error)[:1000],
            "failed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        gh.close()


if __name__ == "__main__":
    raise SystemExit(main())
