#!/usr/bin/env python3
"""Fail-closed validator for the MemeBank mb-infra staging blueprint."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from infra_common import (
    COMMIT_PATTERN,
    DNS_PATTERN,
    NAME_PATTERN,
    SECRET_REFERENCE_PATTERN,
    SHA256_PATTERN,
    is_placeholder_artifact,
    is_placeholder_commit,
    load_json,
    reject_unknown_fields,
    require_array,
    require_bool,
    require_int,
    require_object,
    require_string,
    sha256_value,
    write_json,
)

CANONICAL_PACKAGE = "memebank/mb-infra"
CANONICAL_REPOSITORY_URL = "https://github.com/memebank/mb-infra.git"
ENVIRONMENTS = ("dev", "staging", "prod")
ALLOWED_PROJECTS = {
    "memebank-bootstrap",
    "memebank-platform",
    "memebank-data",
    "memebank-apps",
    "memebank-workers",
}
SECRET_KEYS = {
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "private_key",
    "signing_key",
    "credential",
    "credentials",
}
SECRET_VALUE_PATTERNS = (
    re.compile(r"PRIVATE KEY"),
    re.compile(r"(?:X-Amz-Signature|X-Goog-Signature|[?&]sig=)", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


class Documents(dict[str, Any]):
    pass


def blueprint_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_documents(root: Path) -> Documents:
    return Documents(
        fleet=load_json(root / "control-plane/fleet.json"),
        worker_profiles=load_json(root / "control-plane/worker-profiles.json"),
        bundle=load_json(root / "artifact-locks/bundle.json"),
        root_template=load_json(
            root / "bootstrap/argocd/root-application.template.json"
        ),
        environments={
            name: load_json(root / f"environments/{name}.json")
            for name in ENVIRONMENTS
        },
    )


def scan_for_plaintext_secrets(value: Any, path: str = "documents") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            child_path = f"{path}.{key}"
            is_reference = normalized.endswith("_ref") or normalized.endswith("_refs")
            if normalized in SECRET_KEYS and not is_reference:
                raise ValueError(
                    f"plaintext secret-bearing field is forbidden: {child_path}"
                )
            scan_for_plaintext_secrets(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_for_plaintext_secrets(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
            raise ValueError(f"secret-like value is forbidden at {path}")


def validate_fleet(value: Any) -> tuple[list[dict[str, Any]], set[str]]:
    fleet = require_object(value, "fleet")
    reject_unknown_fields(
        fleet,
        {
            "schema_version",
            "package",
            "repository_url",
            "default_branch",
            "forbidden_repository_names",
            "applications",
        },
        "fleet",
    )
    if fleet.get("schema_version") != 1:
        raise ValueError("fleet.schema_version must equal 1")
    if require_string(fleet.get("package"), "fleet.package") != CANONICAL_PACKAGE:
        raise ValueError(f"fleet.package must equal {CANONICAL_PACKAGE}")
    if require_string(fleet.get("repository_url"), "fleet.repository_url") != CANONICAL_REPOSITORY_URL:
        raise ValueError(f"fleet.repository_url must equal {CANONICAL_REPOSITORY_URL}")
    if fleet.get("default_branch") != "main":
        raise ValueError("fleet.default_branch must equal main")
    forbidden = set(
        require_array(
            fleet.get("forbidden_repository_names"),
            "fleet.forbidden_repository_names",
        )
    )
    if "memebank-infra" not in forbidden:
        raise ValueError("fleet must forbid the superseded memebank-infra name")

    applications: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    waves: set[int] = set()
    profiles: set[str] = set()
    for index, raw in enumerate(require_array(fleet.get("applications"), "fleet.applications")):
        field = f"fleet.applications[{index}]"
        application = require_object(raw, field)
        reject_unknown_fields(
            application,
            {
                "name",
                "sync_wave",
                "component_path",
                "namespace",
                "project",
                "required",
                "profile",
                "depends_on",
            },
            field,
        )
        name = require_string(application.get("name"), f"{field}.name")
        if not NAME_PATTERN.fullmatch(name):
            raise ValueError(f"{field}.name is invalid")
        if name in by_name:
            raise ValueError(f"duplicate application name: {name}")
        wave = require_int(application.get("sync_wave"), f"{field}.sync_wave", minimum=0)
        if wave in waves:
            raise ValueError(f"duplicate sync wave: {wave}")
        waves.add(wave)
        component_path = require_string(
            application.get("component_path"), f"{field}.component_path"
        )
        if not component_path.startswith("components/") or ".." in component_path.split("/"):
            raise ValueError(f"{field}.component_path must be under components/")
        namespace = require_string(application.get("namespace"), f"{field}.namespace")
        if not NAME_PATTERN.fullmatch(namespace):
            raise ValueError(f"{field}.namespace is invalid")
        project = require_string(application.get("project"), f"{field}.project")
        if project not in ALLOWED_PROJECTS:
            raise ValueError(f"{field}.project is not approved")
        require_bool(application.get("required"), f"{field}.required")
        profile = application.get("profile")
        if profile is not None:
            profiles.add(require_string(profile, f"{field}.profile"))
        dependencies = require_array(application.get("depends_on"), f"{field}.depends_on")
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f"{name} repeats a dependency")
        if name in dependencies:
            raise ValueError(f"{name} cannot depend on itself")
        normalized = copy.deepcopy(application)
        applications.append(normalized)
        by_name[name] = normalized

    for application in applications:
        for dependency in application["depends_on"]:
            if dependency not in by_name:
                raise ValueError(
                    f"{application['name']} depends on unknown application {dependency}"
                )
            if by_name[dependency]["sync_wave"] >= application["sync_wave"]:
                raise ValueError(
                    f"{application['name']} dependency {dependency} must have an earlier sync wave"
                )

    ordered = sorted(applications, key=lambda item: item["sync_wave"])
    if not ordered or ordered[0]["name"] != "argocd-projects" or ordered[0]["sync_wave"] != 0:
        raise ValueError("argocd-projects must be the wave-zero child")
    if ordered[-1]["name"] != "smoke-tests":
        raise ValueError("smoke-tests must be the final child")
    return ordered, profiles


def validate_security_context(value: Any, field: str) -> None:
    security = require_object(value, field)
    true_fields = {"run_as_non_root", "read_only_root_filesystem"}
    false_fields = {
        "allow_privilege_escalation",
        "privileged",
        "host_network",
        "host_pid",
        "host_ipc",
        "host_path",
        "automount_service_account_token",
    }
    reject_unknown_fields(
        security,
        true_fields | false_fields | {"seccomp_profile", "capabilities_drop"},
        field,
    )
    for name in true_fields:
        if require_bool(security.get(name), f"{field}.{name}") is not True:
            raise ValueError(f"{field}.{name} must be true")
    for name in false_fields:
        if require_bool(security.get(name), f"{field}.{name}") is not False:
            raise ValueError(f"{field}.{name} must be false")
    if security.get("seccomp_profile") != "RuntimeDefault":
        raise ValueError(f"{field}.seccomp_profile must be RuntimeDefault")
    if security.get("capabilities_drop") != ["ALL"]:
        raise ValueError(f"{field}.capabilities_drop must equal ['ALL']")


def validate_worker_profiles(value: Any) -> dict[str, dict[str, Any]]:
    root = require_object(value, "worker_profiles")
    reject_unknown_fields(root, {"schema_version", "profiles"}, "worker_profiles")
    if root.get("schema_version") != 1:
        raise ValueError("worker_profiles.schema_version must equal 1")
    raw_profiles = require_object(root.get("profiles"), "worker_profiles.profiles")
    expected = {
        "enrichment-local-cpu",
        "enrichment-local-gpu",
        "enrichment-cloud-dispatch",
    }
    if set(raw_profiles) != expected:
        raise ValueError("worker profile set must define CPU, GPU, and cloud dispatch")

    profiles: dict[str, dict[str, Any]] = {}
    for name, raw in sorted(raw_profiles.items()):
        field = f"worker_profiles.profiles.{name}"
        profile = require_object(raw, field)
        reject_unknown_fields(
            profile,
            {
                "class",
                "default_enabled",
                "service_account",
                "secret_refs",
                "egress",
                "security_context",
                "resources",
                "probes",
                "scaling",
            },
            field,
        )
        profile_class = require_string(profile.get("class"), f"{field}.class")
        if profile_class not in {"local-inference", "cloud-dispatch"}:
            raise ValueError(f"{field}.class is invalid")
        require_bool(profile.get("default_enabled"), f"{field}.default_enabled")
        if not NAME_PATTERN.fullmatch(require_string(profile.get("service_account"), f"{field}.service_account")):
            raise ValueError(f"{field}.service_account is invalid")
        secret_refs = require_array(profile.get("secret_refs"), f"{field}.secret_refs")
        for index, reference in enumerate(secret_refs):
            if not SECRET_REFERENCE_PATTERN.fullmatch(
                require_string(reference, f"{field}.secret_refs[{index}]")
            ):
                raise ValueError(f"{field}.secret_refs[{index}] is invalid")
        egress = require_object(profile.get("egress"), f"{field}.egress")
        reject_unknown_fields(
            egress, {"mode", "dns_names", "internal_services"}, f"{field}.egress"
        )
        if egress.get("mode") != "allowlist":
            raise ValueError(f"{field}.egress.mode must equal allowlist")
        dns_names = require_array(egress.get("dns_names"), f"{field}.egress.dns_names")
        internal_services = require_array(
            egress.get("internal_services"), f"{field}.egress.internal_services"
        )
        for group_name, hosts in (("dns_names", dns_names), ("internal_services", internal_services)):
            for index, host in enumerate(hosts):
                if not DNS_PATTERN.fullmatch(
                    require_string(host, f"{field}.egress.{group_name}[{index}]")
                ):
                    raise ValueError(f"{field}.egress.{group_name}[{index}] is invalid")
        validate_security_context(profile.get("security_context"), f"{field}.security_context")
        resources = require_object(profile.get("resources"), f"{field}.resources")
        requests = require_object(resources.get("requests"), f"{field}.resources.requests")
        limits = require_object(resources.get("limits"), f"{field}.resources.limits")
        if set(requests) != set(limits) or not {"cpu", "memory"}.issubset(requests):
            raise ValueError(f"{field}.resources must have matching CPU/memory requests and limits")
        probes = require_object(profile.get("probes"), f"{field}.probes")
        if set(probes) != {"startup", "readiness", "liveness"}:
            raise ValueError(f"{field}.probes must define startup/readiness/liveness")
        scaling = require_object(profile.get("scaling"), f"{field}.scaling")
        if scaling.get("metric") != "nats_consumer_lag":
            raise ValueError(f"{field}.scaling.metric must be nats_consumer_lag")
        minimum = require_int(scaling.get("min_replicas"), f"{field}.scaling.min_replicas", minimum=0)
        maximum = require_int(scaling.get("max_replicas"), f"{field}.scaling.max_replicas", minimum=1)
        if maximum < minimum:
            raise ValueError(f"{field}.scaling.max_replicas must be >= min_replicas")
        require_int(
            scaling.get("max_in_flight_per_replica"),
            f"{field}.scaling.max_in_flight_per_replica",
            minimum=1,
        )
        require_int(
            scaling.get("termination_grace_seconds"),
            f"{field}.scaling.termination_grace_seconds",
            minimum=30,
        )
        if profile_class == "local-inference" and secret_refs:
            raise ValueError(f"{name} local inference profile must not carry secrets")
        if profile_class == "local-inference" and dns_names:
            raise ValueError(f"{name} local inference profile must not have external DNS egress")
        if profile_class == "cloud-dispatch" and (not secret_refs or not dns_names):
            raise ValueError("cloud-dispatch profile requires scoped secrets and endpoint allowlist")
        profiles[name] = copy.deepcopy(profile)
    return profiles


def validate_digest(value: Any, field: str, placeholder: bool) -> str:
    digest = require_string(value, field)
    if placeholder and digest == "sha256:PLACEHOLDER":
        return digest
    if not SHA256_PATTERN.fullmatch(digest):
        raise ValueError(f"{field} must be sha256:<64 lowercase hex>")
    return digest


def validate_bundle(value: Any) -> dict[str, Any]:
    bundle = require_object(value, "bundle")
    reject_unknown_fields(
        bundle,
        {
            "schema_version",
            "bundle_id",
            "promotable",
            "interfaces",
            "images",
            "models",
            "compatibility_sets",
        },
        "bundle",
    )
    if bundle.get("schema_version") != 1:
        raise ValueError("bundle.schema_version must equal 1")
    require_string(bundle.get("bundle_id"), "bundle.bundle_id")
    bundle_promotable = require_bool(bundle.get("promotable"), "bundle.promotable")

    interfaces = require_object(bundle.get("interfaces"), "bundle.interfaces")
    if interfaces.get("package") != "memebank/mb-interfaces":
        raise ValueError("bundle.interfaces.package must equal memebank/mb-interfaces")
    interface_placeholder = require_bool(
        interfaces.get("bootstrap_placeholder"),
        "bundle.interfaces.bootstrap_placeholder",
    )
    interface_promotable = require_bool(
        interfaces.get("promotable"), "bundle.interfaces.promotable"
    )
    interface_commit = require_string(
        interfaces.get("source_commit"), "bundle.interfaces.source_commit"
    )
    if interface_commit != "PLACEHOLDER" and not COMMIT_PATTERN.fullmatch(interface_commit):
        raise ValueError("bundle.interfaces.source_commit must be a commit SHA")
    if interfaces.get("schema_revision") != "mb-interfaces/v1":
        raise ValueError("bundle.interfaces.schema_revision must equal mb-interfaces/v1")
    if interface_placeholder and interface_promotable:
        raise ValueError("placeholder interfaces cannot be promotable")

    images = require_object(bundle.get("images"), "bundle.images")
    if not images:
        raise ValueError("bundle.images must not be empty")
    for name, raw in images.items():
        field = f"bundle.images.{name}"
        image = require_object(raw, field)
        placeholder = require_bool(
            image.get("bootstrap_placeholder"), f"{field}.bootstrap_placeholder"
        )
        promotable = require_bool(image.get("promotable"), f"{field}.promotable")
        reference = require_string(image.get("ref"), f"{field}.ref")
        if "@sha256:" not in reference:
            raise ValueError(f"{field}.ref must use an immutable digest")
        validate_digest(reference.rsplit("@", 1)[-1], f"{field}.ref digest", placeholder)
        commit = require_string(image.get("source_commit"), f"{field}.source_commit")
        if commit != "PLACEHOLDER" and not COMMIT_PATTERN.fullmatch(commit):
            raise ValueError(f"{field}.source_commit must be a commit SHA")
        validate_digest(image.get("sbom_digest"), f"{field}.sbom_digest", placeholder)
        validate_digest(
            image.get("provenance_digest"), f"{field}.provenance_digest", placeholder
        )
        if placeholder and promotable:
            raise ValueError(f"{field} placeholder image cannot be promotable")

    models = require_object(bundle.get("models"), "bundle.models")
    if not models:
        raise ValueError("bundle.models must not be empty")
    for name, raw in models.items():
        field = f"bundle.models.{name}"
        model = require_object(raw, field)
        placeholder = require_bool(
            model.get("bootstrap_placeholder"), f"{field}.bootstrap_placeholder"
        )
        promotable = require_bool(model.get("promotable"), f"{field}.promotable")
        artifact = require_string(model.get("artifact"), f"{field}.artifact")
        if not artifact.startswith("oci://") or "@sha256:" not in artifact:
            raise ValueError(f"{field}.artifact must be an immutable OCI reference")
        checksum = validate_digest(model.get("checksum"), f"{field}.checksum", placeholder)
        if validate_digest(artifact.rsplit("@", 1)[-1], f"{field}.artifact digest", placeholder) != checksum:
            raise ValueError(f"{field}.artifact digest must match checksum")
        for required in ("logical_id", "license", "processor_revision", "source_url"):
            require_string(model.get(required), f"{field}.{required}")
        if not model["source_url"].startswith("https://"):
            raise ValueError(f"{field}.source_url must use HTTPS")
        if not require_string(
            model.get("observation_schema"), f"{field}.observation_schema"
        ).startswith("mb-interfaces/v1/"):
            raise ValueError(f"{field}.observation_schema must reference mb-interfaces/v1")
        runtime = require_object(model.get("runtime"), f"{field}.runtime")
        require_string(runtime.get("name"), f"{field}.runtime.name")
        require_string(runtime.get("version"), f"{field}.runtime.version")
        if not require_array(
            runtime.get("execution_providers"), f"{field}.runtime.execution_providers"
        ):
            raise ValueError(f"{field}.runtime.execution_providers must not be empty")
        if placeholder and promotable:
            raise ValueError(f"{field} placeholder model cannot be promotable")

    compatibility_sets = require_object(
        bundle.get("compatibility_sets"), "bundle.compatibility_sets"
    )
    if not compatibility_sets:
        raise ValueError("bundle.compatibility_sets must not be empty")
    for name, raw in compatibility_sets.items():
        field = f"bundle.compatibility_sets.{name}"
        compatibility = require_object(raw, field)
        for image_name in require_array(compatibility.get("images"), f"{field}.images"):
            if image_name not in images:
                raise ValueError(f"{field} references unknown image {image_name}")
        for model_name in require_array(compatibility.get("models"), f"{field}.models"):
            if model_name not in models:
                raise ValueError(f"{field} references unknown model {model_name}")
        set_promotable = require_bool(
            compatibility.get("promotable"), f"{field}.promotable"
        )
        if set_promotable and (
            interface_placeholder
            or any(is_placeholder_artifact(images[item]) for item in compatibility["images"])
            or any(is_placeholder_artifact(models[item]) for item in compatibility["models"])
        ):
            raise ValueError(f"{field} promotable set contains placeholders")

    if bundle_promotable and (
        interface_placeholder
        or not interface_promotable
        or any(is_placeholder_artifact(item) or not item["promotable"] for item in images.values())
        or any(is_placeholder_artifact(item) or not item["promotable"] for item in models.values())
    ):
        raise ValueError("promotable bundle contains placeholders or unpromotable artifacts")
    return copy.deepcopy(bundle)


def validate_root_template(value: Any) -> None:
    root = require_object(value, "root_template")
    if root.get("apiVersion") != "argoproj.io/v1alpha1" or root.get("kind") != "Application":
        raise ValueError("root template must be an Argo CD Application")
    metadata = require_object(root.get("metadata"), "root_template.metadata")
    if metadata.get("name") != "memebank-root-${ENVIRONMENT}" or metadata.get("namespace") != "argocd":
        raise ValueError("root template metadata must retain the canonical placeholders")
    spec = require_object(root.get("spec"), "root_template.spec")
    source = require_object(spec.get("source"), "root_template.spec.source")
    if source != {
        "repoURL": CANONICAL_REPOSITORY_URL,
        "targetRevision": "${SOURCE_REVISION}",
        "path": "bootstrap/argocd/${ENVIRONMENT}/children",
    }:
        raise ValueError("root template source is not canonical")
    destination = require_object(spec.get("destination"), "root_template.spec.destination")
    if destination != {"server": "${CLUSTER_SERVER}", "namespace": "argocd"}:
        raise ValueError("root template destination is not canonical")
    sync_policy = require_object(spec.get("syncPolicy"), "root_template.spec.syncPolicy")
    if "automated" in sync_policy:
        raise ValueError("root template must not hard-code automated sync")
    if "PruneLast=true" not in require_array(
        sync_policy.get("syncOptions"), "root_template.spec.syncPolicy.syncOptions"
    ):
        raise ValueError("root template must use PruneLast=true")


def validate_environment(
    value: Any,
    name: str,
    profiles: set[str],
    bundle: dict[str, Any],
) -> dict[str, Any]:
    field = f"environments.{name}"
    environment = require_object(value, field)
    if environment.get("schema_version") != 1:
        raise ValueError(f"{field}.schema_version must equal 1")
    if environment.get("repository") != CANONICAL_PACKAGE:
        raise ValueError(f"{field}.repository must equal {CANONICAL_PACKAGE}")
    if environment.get("environment") != name:
        raise ValueError(f"{field}.environment must equal {name}")
    source_revision = require_string(
        environment.get("source_revision"), f"{field}.source_revision"
    )
    if source_revision != "PLACEHOLDER" and not COMMIT_PATTERN.fullmatch(source_revision):
        raise ValueError(f"{field}.source_revision must be a 40-character SHA")
    deployment_enabled = require_bool(
        environment.get("deployment_enabled"), f"{field}.deployment_enabled"
    )
    auto_sync = require_bool(environment.get("auto_sync"), f"{field}.auto_sync")
    if name in {"staging", "prod"} and auto_sync:
        raise ValueError(f"{field}.auto_sync must remain false")
    enabled_profiles = set(
        require_array(environment.get("enabled_profiles"), f"{field}.enabled_profiles")
    )
    if not enabled_profiles <= profiles:
        raise ValueError(f"{field} enables unknown profiles")
    if "enrichment-local-cpu" not in enabled_profiles:
        raise ValueError(f"{field} must retain the CPU-local fallback")
    network = require_object(environment.get("network_policy"), f"{field}.network_policy")
    if not network.get("default_deny_ingress") or not network.get("default_deny_egress"):
        raise ValueError(f"{field} must default-deny ingress and egress")
    backend = require_object(environment.get("secret_backend"), f"{field}.secret_backend")
    if backend.get("type") != "external-secrets" or not SECRET_REFERENCE_PATTERN.fullmatch(
        require_string(backend.get("cluster_store_ref"), f"{field}.secret_backend.cluster_store_ref")
    ):
        raise ValueError(f"{field} must use a valid External Secrets store reference")
    promotion = require_object(
        environment.get("promotion_policy"), f"{field}.promotion_policy"
    )
    allow_placeholders = require_bool(
        promotion.get("allow_bootstrap_placeholders"),
        f"{field}.promotion_policy.allow_bootstrap_placeholders",
    )
    if not promotion.get("require_immutable_artifacts"):
        raise ValueError(f"{field} must require immutable artifacts")
    if name in {"staging", "prod"} and (
        allow_placeholders or not promotion.get("require_review")
    ):
        raise ValueError(f"{field} must reject placeholders and require review")

    cloud = require_object(environment.get("cloud_dispatch"), f"{field}.cloud_dispatch")
    cloud_enabled = require_bool(cloud.get("enabled"), f"{field}.cloud_dispatch.enabled")
    cloud_profile = "enrichment-cloud-dispatch" in enabled_profiles
    if cloud_enabled != cloud_profile:
        raise ValueError(f"{field} cloud dispatch flag and profile must agree")
    secret_refs = require_array(cloud.get("secret_refs"), f"{field}.cloud_dispatch.secret_refs")
    hosts = require_array(cloud.get("egress_dns_names"), f"{field}.cloud_dispatch.egress_dns_names")
    if cloud_enabled and (not secret_refs or not hosts):
        raise ValueError(f"{field} cloud dispatch requires secrets and endpoint allowlist")
    if not cloud_enabled and (secret_refs or hosts):
        raise ValueError(f"{field} disabled cloud dispatch must not retain secrets or egress")

    gpu = require_object(environment.get("gpu"), f"{field}.gpu")
    gpu_enabled = require_bool(gpu.get("enabled"), f"{field}.gpu.enabled")
    if gpu_enabled != ("enrichment-local-gpu" in enabled_profiles):
        raise ValueError(f"{field} GPU flag and profile must agree")
    if gpu_enabled and (
        not gpu.get("runtime_class")
        or not gpu.get("node_selector")
        or not gpu.get("tolerations")
    ):
        raise ValueError(f"{field} enabled GPU requires explicit scheduling")
    if not gpu_enabled and (
        gpu.get("runtime_class") is not None
        or gpu.get("node_selector")
        or gpu.get("tolerations")
    ):
        raise ValueError(f"{field} disabled GPU must not retain scheduling config")

    blockers: list[str] = []
    if not deployment_enabled:
        blockers.append("deployment is disabled")
    if is_placeholder_commit(source_revision):
        blockers.append("canonical source revision is a placeholder")
    if not bundle["promotable"]:
        blockers.append("artifact compatibility bundle is not promotable")
    if any(is_placeholder_artifact(item) for item in bundle["images"].values()):
        blockers.append("one or more image locks are bootstrap placeholders")
    if any(is_placeholder_artifact(item) for item in bundle["models"].values()):
        blockers.append("one or more model locks are bootstrap placeholders")
    if bundle["interfaces"]["bootstrap_placeholder"]:
        blockers.append("interface source revision is a bootstrap placeholder")
    if deployment_enabled and not allow_placeholders and blockers:
        raise ValueError(f"{field} is enabled with non-promotable blockers")
    return {
        "environment": name,
        "renderable": True,
        "deployment_enabled": deployment_enabled,
        "promotion_ready": deployment_enabled and not blockers,
        "auto_sync": auto_sync,
        "enabled_profiles": sorted(enabled_profiles),
        "blockers": blockers,
    }


def validate_documents(documents: Documents) -> dict[str, Any]:
    scan_for_plaintext_secrets(documents)
    applications, fleet_profiles = validate_fleet(documents["fleet"])
    profiles = validate_worker_profiles(documents["worker_profiles"])
    if fleet_profiles != set(profiles):
        raise ValueError("fleet profile set must match worker profile definitions")
    bundle = validate_bundle(documents["bundle"])
    validate_root_template(documents["root_template"])
    environments = require_object(documents.get("environments"), "environments")
    if set(environments) != set(ENVIRONMENTS):
        raise ValueError("exactly dev, staging, and prod are required")
    environment_reports = {
        name: validate_environment(environments[name], name, set(profiles), bundle)
        for name in ENVIRONMENTS
    }
    return {
        "schema_version": 1,
        "package": CANONICAL_PACKAGE,
        "repository_url": CANONICAL_REPOSITORY_URL,
        "valid": True,
        "application_count": len(applications),
        "application_order": [item["name"] for item in applications],
        "worker_profile_count": len(profiles),
        "worker_profiles": sorted(profiles),
        "artifact_bundle_id": bundle["bundle_id"],
        "artifact_bundle_digest": sha256_value(bundle),
        "artifact_bundle_promotable": bundle["promotable"],
        "environments": environment_reports,
        "promotion_ready_environment_count": sum(
            item["promotion_ready"] for item in environment_reports.values()
        ),
        "input_digest": sha256_value(documents),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=blueprint_root())
    parser.add_argument("--report", type=Path)
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = validate_documents(load_documents(args.root))
    if args.report is not None:
        path = args.report if args.report.is_absolute() else args.root / args.report
        write_json(path, report)
    print(
        json.dumps(
            report,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
