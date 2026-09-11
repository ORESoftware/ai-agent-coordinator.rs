from __future__ import annotations

from typing import Any

from .constants import BootstrapError
from .interfaces import interface_files
from .scaffolds_apps import clients_files, desktop_files, flutter_files, lambda_files
from .scaffolds_core import lib_core_files, orm_core_files, sync_files

def repo_specific_files(manifest: dict[str, Any], repo: dict[str, Any]) -> dict[str, str]:
    kind = repo["kind"]
    if kind == "interfaces":
        return interface_files(manifest)
    if kind == "lib-core":
        return lib_core_files()
    if kind == "orm-core":
        return orm_core_files()
    if kind == "clients":
        return clients_files(manifest)
    if kind == "sync":
        return sync_files()
    if kind == "flutter":
        return flutter_files()
    if kind == "desktop-rust":
        return desktop_files()
    if kind == "lambdas":
        return lambda_files()
    raise BootstrapError(f"unknown scaffold kind: {kind}")
