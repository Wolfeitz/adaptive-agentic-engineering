from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Mapping, Protocol
import urllib.error
import urllib.parse
import urllib.request

from .semantic import canonical_digest, export_tracker_items


class TrackerTransport(Protocol):
    def send(
        self, url: str, headers: Mapping[str, str], body: bytes
    ) -> Mapping[str, Any]: ...


class UrllibTrackerTransport:
    def send(
        self, url: str, headers: Mapping[str, str], body: bytes
    ) -> Mapping[str, Any]:
        request = urllib.request.Request(
            url, data=body, headers=dict(headers), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response_body = response.read()
                parsed: object = (
                    json.loads(response_body.decode("utf-8"))
                    if response_body
                    else None
                )
                return {
                    "status": response.status,
                    "response": parsed,
                }
        except urllib.error.HTTPError as error:
            response_body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"tracker returned HTTP {error.code}: {response_body[:500]}"
            ) from error


def _validate_endpoint(endpoint: str) -> None:
    parsed = urllib.parse.urlparse(endpoint)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("tracker endpoint must use HTTPS (HTTP is allowed only locally)")
    if not parsed.netloc:
        raise ValueError("tracker endpoint must include a host")


def _request_payload(provider: str, payload: Mapping[str, Any]) -> object:
    if provider != "azure":
        return payload
    fields = payload.get("fields")
    if not isinstance(fields, Mapping):
        raise ValueError("Azure tracker payload has no fields object")
    return [
        {"op": "add", "path": f"/fields/{field}", "value": value}
        for field, value in sorted(fields.items())
    ]


def _deep_merge(base: object, additions: Mapping[str, Any]) -> object:
    if not isinstance(base, Mapping):
        return dict(additions)
    result = dict(base)
    for key, value in additions.items():
        existing = result.get(key)
        result[key] = (
            _deep_merge(existing, value)
            if isinstance(value, Mapping) and isinstance(existing, Mapping)
            else value
        )
    return result


def build_tracker_request_specs(
    root: Path,
    provider: str,
    endpoint: str,
    *,
    payload_defaults: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    _validate_endpoint(endpoint)
    items = export_tracker_items(root, provider)
    if provider == "azure":
        content_type = "application/json-patch+json"
    else:
        content_type = "application/json"
    specs: list[dict[str, Any]] = []
    for item in items:
        merged = _deep_merge(item["payload"], payload_defaults or {})
        if not isinstance(merged, Mapping):
            raise ValueError("tracker payload must be an object")
        if provider == "jira":
            fields = merged.get("fields")
            if not isinstance(fields, Mapping) or not all(
                field in fields for field in ("project", "issuetype")
            ):
                raise ValueError(
                    "Jira submission requires payload defaults for fields.project "
                    "and fields.issuetype"
                )
        payload = _request_payload(provider, merged)
        public = {
            "schema_version": 1,
            "provider": provider,
            "endpoint": endpoint,
            "task_id": item["task_id"],
            "task_packet_sha256": item["task_packet_sha256"],
            "headers": {
                "Accept": "application/json",
                "Content-Type": content_type,
            },
            "payload": payload,
        }
        specs.append(
            {
                **public,
                "request_sha256": canonical_digest(public),
            }
        )
    return specs


def submit_tracker_items(
    root: Path,
    provider: str,
    endpoint: str,
    token: str,
    *,
    confirm_external_write: bool,
    payload_defaults: Mapping[str, Any] | None = None,
    transport: TrackerTransport | None = None,
) -> dict[str, Any]:
    if not confirm_external_write:
        raise ValueError("external tracker mutation requires explicit confirmation")
    if not token:
        raise ValueError("tracker token must not be empty")
    specs = build_tracker_request_specs(
        root, provider, endpoint, payload_defaults=payload_defaults
    )
    sender = transport or UrllibTrackerTransport()
    authorization = (
        "Basic " + base64.b64encode(f":{token}".encode()).decode()
        if provider == "azure"
        else f"Bearer {token}"
    )
    results: list[dict[str, Any]] = []
    for spec in specs:
        headers = {
            **spec["headers"],
            "Authorization": authorization,
        }
        response = sender.send(
            spec["endpoint"],
            headers,
            json.dumps(spec["payload"], separators=(",", ":")).encode("utf-8"),
        )
        results.append(
            {
                "task_id": spec["task_id"],
                "request_sha256": spec["request_sha256"],
                "response": dict(response),
            }
        )
    result: dict[str, Any] = {
        "schema_version": 1,
        "provider": provider,
        "endpoint": endpoint,
        "submitted": len(results),
        "results": results,
    }
    result["submission_sha256"] = canonical_digest(result)
    return result
