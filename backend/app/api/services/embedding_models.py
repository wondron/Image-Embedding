"""HTTP adapter for the model versions (app.bootstrap.model_registry).

NOT_READY 503 while the registry is not built (retrieval switched off) or the catalog's
PostgreSQL is down. Routes are sync: FastAPI runs them in the threadpool (a switch loads the
version's encoder and opens its collection before it answers).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.bootstrap.state import ApplicationState
from app.core import errors
from app.core.middleware import get_trace_id

logger = logging.getLogger("svc.embedding_models")


def _registry(runtime: ApplicationState) -> Any:
    registry = runtime.models
    if registry is None:
        raise errors.not_ready(runtime.retrieval_error or "model versions are not available")
    return registry


def list_models(runtime: ApplicationState) -> dict[str, Any]:
    return {"ok": True, **_registry(runtime).overview()}


def register_model(runtime: ApplicationState, request: Request, *, name: str | None, version: str | None) -> dict[str, Any]:
    registry = _registry(runtime)
    row = registry.register(name=name, version=version)
    logger.info("embedding model registered id=%s %s:%s | trace_id=%s", row["id"], row["name"], row["version"], get_trace_id(request))
    return {"ok": True, "model": registry.item(int(row["id"]))}


def start_build(runtime: ApplicationState, request: Request, model_id: int) -> dict[str, Any]:
    build = _registry(runtime).start_build(model_id)
    logger.info("embedding build started model=%s | trace_id=%s", model_id, get_trace_id(request))
    return {"ok": True, "build": build}


def stop_build(runtime: ApplicationState, request: Request, model_id: int) -> dict[str, Any]:
    build = _registry(runtime).stop_build(model_id)
    logger.info("embedding build stop requested model=%s | trace_id=%s", model_id, get_trace_id(request))
    return {"ok": True, "build": build}


def activate_model(runtime: ApplicationState, request: Request, model_id: int, *, allow_missing: bool) -> dict[str, Any]:
    registry = _registry(runtime)
    result = registry.activate(model_id, allow_missing=allow_missing)
    logger.info(
        "embedding model activate id=%s switched=%s previous=%s allow_missing=%s | trace_id=%s",
        model_id, result["switched"], result["previous_id"], allow_missing, get_trace_id(request),
    )
    return {"ok": True, "model": registry.item(model_id), "previous_id": result["previous_id"], "switched": result["switched"]}


def retire_model(runtime: ApplicationState, request: Request, model_id: int) -> dict[str, Any]:
    registry = _registry(runtime)
    result = registry.retire(model_id)
    logger.info(
        "embedding model retired id=%s dropped=%s ledger_rows=%s | trace_id=%s",
        model_id, result["dropped"], result["ledger_rows"], get_trace_id(request),
    )
    return {"ok": True, "model": registry.item(model_id), "dropped": result["dropped"], "ledger_rows": result["ledger_rows"]}
