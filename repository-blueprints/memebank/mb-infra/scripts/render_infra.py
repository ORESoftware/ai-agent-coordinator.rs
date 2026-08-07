#!/usr/bin/env python3
"""Deterministically render Argo CD root/child Applications from reviewed inputs."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from infra_common import tree_digest, write_json
from validate_infra import (
    CANONICAL_REPOSITORY_URL,
    ENVIRONMENTS,
    blueprint_root,
    load_documents,
    validate_documents,
)

PLACEHOLDERS = {
    "${ENVIRONMENT}": "environment",
    "${SOURCE_REVISION}": "source_revision",
    "${CLUSTER_SERVER}": "cluster_server",
}


def substitute(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: substitute(child, context) for key, child in value.items()}
    if isinstance(value, list):
        return [substitute(child, context) for child in value]
    if isinstance(value, str):
        result = value
        for placeholder, context_key in PLACEHOLDERS.items():
            result = result.replace(placeholder, context[context_key])
        if "${" in result:
            raise ValueError(f"unresolved template placeholder in {result!r}")
        return result
    return value


def selected_applications(
    applications: list[dict[str, Any]],
    enabled_profiles: set[str],
) -> list[dict[str, Any]]:
    return [
        application
        for application in applications
        if application["required"]
        or (
            application["profile"] is not None
            and application["profile"] in enabled_profiles
        )
    ]


def child_application(
    application: dict[str, Any],
    environment: dict[str, Any],
) -> dict[str, Any]:
    sync_policy: dict[str, Any] = {
        "syncOptions": [
            "ApplyOutOfSyncOnly=true",
            "CreateNamespace=true",
            "PruneLast=true",
        ]
    }
    if environment["auto_sync"]:
        sync_policy["automated"] = {
            "allowEmpty": False,
            "prune": True,
            "selfHeal": True,
        }
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": f"memebank-{environment['environment']}-{application['name']}",
            "namespace": "argocd",
            "annotations": {
                "argocd.argoproj.io/sync-wave": str(application["sync_wave"]),
                "memebank.dev/depends-on": ",".join(application["depends_on"]),
            },
            "labels": {
                "app.kubernetes.io/part-of": "memebank",
                "memebank.dev/environment": environment["environment"],
            },
        },
        "spec": {
            "project": application["project"],
            "source": {
                "repoURL": CANONICAL_REPOSITORY_URL,
                "targetRevision": environment["source_revision"],
                "path": application["component_path"],
            },
            "destination": {
                "server": environment["cluster_server"],
                "namespace": application["namespace"],
            },
            "syncPolicy": sync_policy,
        },
    }


def render_environment(
    root: Path,
    output_root: Path,
    documents: dict[str, Any],
    validation_report: dict[str, Any],
    environment_name: str,
) -> dict[str, Any]:
    environment = documents["environments"][environment_name]
    applications = sorted(
        documents["fleet"]["applications"], key=lambda item: item["sync_wave"]
    )
    enabled_profiles = set(environment["enabled_profiles"])
    selected = selected_applications(applications, enabled_profiles)
    target = output_root / "bootstrap" / "argocd" / environment_name
    children_root = target / "children"

    context = {
        "environment": environment_name,
        "source_revision": environment["source_revision"],
        "cluster_server": environment["cluster_server"],
    }
    root_application = substitute(copy.deepcopy(documents["root_template"]), context)
    if environment["auto_sync"]:
        root_application["spec"]["syncPolicy"]["automated"] = {
            "allowEmpty": False,
            "prune": True,
            "selfHeal": True,
        }
    write_json(target / "root-application.json", root_application)

    resource_paths: list[str] = []
    missing_component_paths: list[str] = []
    for application in selected:
        relative = f"applications/{application['name']}.json"
        resource_paths.append(relative)
        write_json(
            children_root / relative,
            child_application(application, environment),
        )
        if not (root / application["component_path"]).exists():
            missing_component_paths.append(application["component_path"])

    write_json(
        children_root / "kustomization.json",
        {
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            "resources": resource_paths,
        },
    )

    environment_report = validation_report["environments"][environment_name]
    blockers = list(environment_report["blockers"])
    blockers.extend(
        f"component path is not implemented: {path}"
        for path in sorted(missing_component_paths)
    )
    plan = {
        "schema_version": 1,
        "package": "memebank/mb-infra",
        "environment": environment_name,
        "deployment_enabled": environment["deployment_enabled"],
        "auto_sync": environment["auto_sync"],
        "source_revision": environment["source_revision"],
        "artifact_bundle_digest": validation_report["artifact_bundle_digest"],
        "selected_applications": [item["name"] for item in selected],
        "selected_application_count": len(selected),
        "enabled_profiles": sorted(enabled_profiles),
        "promotion_ready": environment_report["promotion_ready"] and not blockers,
        "blockers": blockers,
    }
    write_json(target / "plan.json", plan)
    return {
        "environment": environment_name,
        "tree_digest": tree_digest(target),
        "selected_application_count": len(selected),
        "promotion_ready": plan["promotion_ready"],
        "blocker_count": len(blockers),
    }


def render_all(
    root: Path,
    output_root: Path,
    *,
    environments: tuple[str, ...] = ENVIRONMENTS,
) -> dict[str, Any]:
    documents = load_documents(root)
    validation_report = validate_documents(documents)
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    rendered = [
        render_environment(
            root,
            output_root,
            documents,
            validation_report,
            environment_name,
        )
        for environment_name in environments
    ]
    index = {
        "schema_version": 1,
        "package": "memebank/mb-infra",
        "input_digest": validation_report["input_digest"],
        "artifact_bundle_digest": validation_report["artifact_bundle_digest"],
        "environments": rendered,
    }
    write_json(output_root / "render-index.json", index)
    return index


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=blueprint_root())
    parser.add_argument("--output", type=Path, default=Path(".artifacts/rendered"))
    parser.add_argument(
        "--environment",
        action="append",
        choices=ENVIRONMENTS,
        dest="environments",
    )
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    output = args.output
    if not output.is_absolute():
        output = args.root / output
    selected = tuple(args.environments) if args.environments else ENVIRONMENTS
    index = render_all(args.root, output, environments=selected)
    print(
        json.dumps(
            index,
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
