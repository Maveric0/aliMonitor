#!/usr/bin/env python3
import argparse
import copy
import json
import mimetypes
import pathlib
import shutil
import subprocess
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import failover_realm as core


BASE_DIR = pathlib.Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "webui_assets"
INDEX_PATH = STATIC_DIR / "index.html"
ACTION_LOCK = threading.Lock()


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = int(status)


def read_text(path: pathlib.Path) -> str:
    if not path.exists():
        raise RuntimeError(f"missing static file: {path}")
    return path.read_text(encoding="utf-8")


def resolve_static_path(request_path: str) -> pathlib.Path:
    decoded = unquote(request_path or "/")
    if decoded == "/":
        return INDEX_PATH
    relative = pathlib.PurePosixPath(decoded.lstrip("/"))
    if any(part == ".." for part in relative.parts):
        raise ApiError("invalid static path", status=404)
    candidate = (STATIC_DIR / pathlib.Path(*relative.parts)).resolve()
    static_root = STATIC_DIR.resolve()
    if candidate != static_root and static_root not in candidate.parents:
        raise ApiError("invalid static path", status=404)
    if not candidate.exists() or not candidate.is_file():
        raise ApiError(f"not found: {decoded}", status=404)
    return candidate


def guess_content_type(path: pathlib.Path) -> str:
    content_type, _ = mimetypes.guess_type(str(path))
    return content_type or "application/octet-stream"


def read_settings_text_for_setup() -> str:
    if core.SETTINGS_PATH.exists():
        return core.SETTINGS_PATH.read_text(encoding="utf-8")
    return json.dumps(core.load_settings_template(), ensure_ascii=False, indent=2)


def build_setup_payload() -> dict:
    configured = False
    validation_error = ""
    redacted_settings = None
    settings_text = ""
    if core.settings_exists():
        try:
            settings = core.load_settings()
            configured = True
            redacted_settings = redact_settings(settings)
        except Exception as exc:
            validation_error = str(exc)
            settings_text = read_settings_text_for_setup()
    else:
        settings_text = read_settings_text_for_setup()
    return {
        "configured": configured,
        "validation_error": validation_error,
        "settings_exists": core.settings_exists(),
        "settings_text": settings_text,
        "template_source": str(core.settings_template_path().name),
        "paths": {
            "settings": str(core.SETTINGS_PATH),
            "state": str(core.STATE_PATH),
            "frontend_installed": str(core.FORWARD_INSTALLED_PATH),
            "legacy_frontend_config": str(core.REALM_CONFIG_PATH),
            "iepl_config": str(core.IEPL_CONFIG_PATH),
        },
        "settings": redacted_settings,
    }


def ensure_monitor_service_started() -> str:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return "settings saved; systemctl not available, start aliMonitor.service manually"
    try:
        subprocess.run(
            [systemctl, "daemon-reload"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        subprocess.run(
            [systemctl, "enable", "--now", "aliMonitor.service"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        return f"settings saved, but failed to start aliMonitor.service automatically: {exc}"
    return ""


def redact_settings(settings: dict) -> dict:
    cloned = copy.deepcopy(settings)
    if "komari" in cloned and cloned["komari"].get("api_key"):
        cloned["komari"]["api_key"] = "<redacted>"
    if "cloudflare" in cloned and cloned["cloudflare"].get("api_token"):
        cloned["cloudflare"]["api_token"] = "<redacted>"
    if "ssh" in cloned:
        if cloned["ssh"].get("password"):
            cloned["ssh"]["password"] = "<redacted>"
        if cloned["ssh"].get("private_key"):
            cloned["ssh"]["private_key"] = "<redacted>"
    return cloned


def tail_text(content: str, max_lines: int = 200) -> str:
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return content
    return "\n".join(lines[-max_lines:])


def read_log_tail(max_lines: int = 200) -> dict:
    candidates = [
        BASE_DIR / "aliMonitor.log",
        BASE_DIR / "failover.log",
        BASE_DIR / "webui.log",
    ]
    for path in candidates:
        if path.exists():
            return {
                "source": str(path),
                "content": tail_text(path.read_text(encoding="utf-8", errors="replace"), max_lines=max_lines),
            }
    return {
        "source": "journalctl",
        "content": (
            "No local log file found in workspace.\n"
            "Use systemd logs on server:\n"
            "journalctl -u aliMonitor.service -n 200 --no-pager"
        ),
    }


def current_dns_record_for_domain(settings: dict, record_name: str) -> dict:
    try:
        cf = core.CloudflareClient(settings["cloudflare"]["api_token"], settings["cloudflare"]["zone_id"])
        record = cf.get_a_record(record_name)
        return {
            "ok": True,
            "id": record.get("id", ""),
            "content": record.get("content", ""),
            "ttl": int(record.get("ttl", settings["cloudflare"].get("record_ttl", 60)) or 0),
            "proxied": bool(record.get("proxied", settings["cloudflare"].get("proxied", False))),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def current_dns_records(settings: dict) -> dict[str, dict]:
    return {
        record_name: current_dns_record_for_domain(settings, record_name)
        for record_name in core.list_frontend_domain_names(settings, enabled_only=False)
    }


def summarize_node(item: dict | None) -> dict | None:
    if not item:
        return None
    uptime_days = float(item["status"].get("uptime", 0) or 0) / 86400.0
    traffic_gb = float(item.get("traffic_bytes", 0) or 0) / core.BYTES_PER_GB
    return {
        "uuid": item["uuid"],
        "name": item["name"],
        "ipv4": item.get("ipv4", ""),
        "online": bool(item.get("online")),
        "stale": bool(item.get("stale")),
        "status_age_sec": item.get("status_age_sec"),
        "traffic_gb": round(traffic_gb, 2),
        "uptime_days": round(uptime_days, 2),
    }


def serialize_frontend_item(
    settings: dict,
    state: dict,
    item: dict,
    installed: dict[str, dict],
    owner_map: dict[str, str],
) -> dict:
    uptime_days = float(item["status"].get("uptime", 0) or 0) / 86400.0
    traffic_gb = float(item.get("traffic_bytes", 0) or 0) / core.BYTES_PER_GB
    traffic_limit_gb, traffic_limit_source = core.get_frontend_node_traffic_limit_info(settings, item)
    over_limit, over_limit_detail = core.over_limit_detail_for_node(settings, item)
    record = installed.get(item["uuid"], {})
    current_primary_domains = []
    for record_name in core.list_frontend_domain_names(settings, enabled_only=False):
        domain_state = core.get_domain_runtime_state(state, record_name)
        if domain_state.get("current_primary_uuid") == item["uuid"]:
            current_primary_domains.append(record_name)
    return {
        "uuid": item["uuid"],
        "name": item["name"],
        "ipv4": item.get("ipv4", ""),
        "online": bool(item.get("online")),
        "stale": bool(item.get("stale")),
        "status_age_sec": item.get("status_age_sec"),
        "traffic_gb": round(traffic_gb, 2),
        "uptime_days": round(uptime_days, 2),
        "healthy": bool(core.eligible_for_promotion(settings, item)),
        "fallback_healthy": bool(core.fallback_eligible(item)),
        "owner_domain": owner_map.get(item["uuid"]),
        "current_primary_domains": current_primary_domains,
        "over_limit": bool(over_limit),
        "over_limit_detail": over_limit_detail,
        "traffic_limit_gb": int(traffic_limit_gb),
        "traffic_limit_source": traffic_limit_source,
        "installed_profile": record.get("profile", ""),
        "installed_config": record.get("config_file", ""),
        "installed_at": record.get("installed_at", ""),
        "installed_domain_name": record.get("domain_name", ""),
    }


def serialize_domain(
    settings: dict,
    state: dict,
    snapshot: dict,
    installed: dict[str, dict],
    dns_records: dict[str, dict],
    record_name: str,
) -> dict:
    domain = core.get_frontend_domain(settings, record_name)
    domain_state = core.get_domain_runtime_state(state, record_name)
    members = core.frontend_domain_member_items(settings, snapshot, record_name)
    items_by_uuid = {item["uuid"]: item for item in members}
    current_primary = items_by_uuid.get(domain_state.get("current_primary_uuid", ""))
    preferred_primary = items_by_uuid.get(domain.get("preferred_primary_uuid", ""))
    editor_primary_uuid = (
        str(domain.get("preferred_primary_uuid", "")).strip()
        or str(domain_state.get("current_primary_uuid", "")).strip()
        or (members[0]["uuid"] if members else "")
    )
    if domain.get("legacy_pool"):
        editor_backup_uuids = [item["uuid"] for item in members if item["uuid"] != editor_primary_uuid]
    else:
        editor_backup_uuids = list(domain.get("backup_uuids", []))
    backups = [items_by_uuid[uuid] for uuid in editor_backup_uuids if uuid in items_by_uuid]
    member_records = []
    for item in members:
        installed_record = installed.get(item["uuid"], {})
        role = "member"
        if item["uuid"] == domain_state.get("current_primary_uuid"):
            role = "current_primary"
        elif item["uuid"] == editor_primary_uuid:
            role = "preferred_primary"
        elif item["uuid"] in editor_backup_uuids:
            role = "backup"
        member_records.append(
            {
                **summarize_node(item),
                "role": role,
                "installed_domain_name": installed_record.get("domain_name", ""),
                "installed_config": installed_record.get("config_file", ""),
                "installed_at": installed_record.get("installed_at", ""),
            }
        )
    return {
        "record_name": record_name,
        "enabled": bool(domain.get("enabled", True)),
        "legacy_pool": bool(domain.get("legacy_pool", False)),
        "preferred_primary_uuid": str(domain.get("preferred_primary_uuid", "")).strip(),
        "backup_uuids": list(domain.get("backup_uuids", [])),
        "editor_preferred_primary_uuid": editor_primary_uuid,
        "editor_backup_uuids": editor_backup_uuids,
        "current_primary_uuid": str(domain_state.get("current_primary_uuid", "")).strip(),
        "current_primary_name": str(domain_state.get("current_primary_name", "")).strip(),
        "current_primary_ip": str(domain_state.get("current_primary_ip", "")).strip(),
        "last_switch_reason": str(domain_state.get("last_switch_reason", "")).strip(),
        "last_switch_at": str(domain_state.get("last_switch_at", "")).strip(),
        "offline_fail_count": int(domain_state.get("offline_fail_count", 0) or 0),
        "forward_rules": list(domain.get("forward_rules", [])),
        "forward_rule_count": len(domain.get("forward_rules", [])),
        "dns": dns_records.get(record_name, {"ok": False, "error": "DNS lookup not available"}),
        "current_primary": summarize_node(current_primary),
        "preferred_primary": summarize_node(preferred_primary),
        "backups": [summarize_node(item) for item in backups],
        "members": member_records,
    }


def serialize_iepl_item(settings: dict, item: dict, installed: dict[str, dict]) -> dict:
    uptime_days = float(item["status"].get("uptime", 0) or 0) / 86400.0
    traffic_gb = float(item.get("traffic_bytes", 0) or 0) / core.BYTES_PER_GB
    target_ip = ""
    target_ip_error = ""
    try:
        target_ip = core.resolve_iepl_target_ip(settings, item)
    except Exception as exc:
        target_ip_error = str(exc)
    record = installed.get(item["uuid"], {})
    return {
        "uuid": item["uuid"],
        "name": item["name"],
        "ipv4": item.get("ipv4", ""),
        "online": bool(item.get("online")),
        "stale": bool(item.get("stale")),
        "status_age_sec": item.get("status_age_sec"),
        "traffic_gb": round(traffic_gb, 2),
        "uptime_days": round(uptime_days, 2),
        "target_ip": target_ip,
        "target_ip_error": target_ip_error,
        "installed_profile": record.get("profile", ""),
        "installed_config": record.get("config_file", ""),
        "installed_at": record.get("installed_at", ""),
    }


def build_overview() -> dict:
    settings = core.load_settings()
    state = core.load_runtime_state(settings)
    snapshot = core.fetch_snapshot(settings)
    iepl_snapshot = core.fetch_iepl_snapshot(settings)
    installed = core.load_installed_records()
    owner_map = core.frontend_domain_owner_map(settings, snapshot)
    dns_records = current_dns_records(settings)
    frontend_nodes = sorted(
        [serialize_frontend_item(settings, state, item, installed, owner_map) for item in snapshot["nodes"]],
        key=lambda item: (item["owner_domain"] or "zzz", item["name"], item["uuid"]),
    )
    domains = [
        serialize_domain(settings, state, snapshot, installed, dns_records, record_name)
        for record_name in core.list_frontend_domain_names(settings, enabled_only=False)
    ]
    iepl_rules = core.list_forward_rules_from_path(core.IEPL_CONFIG_PATH) if core.IEPL_CONFIG_PATH.exists() else []
    iepl_nodes = sorted(
        [serialize_iepl_item(settings, item, installed) for item in iepl_snapshot["nodes"]],
        key=lambda item: (item["name"], item["uuid"]),
    )
    assigned_frontend_nodes = len([item for item in frontend_nodes if item.get("owner_domain")])
    return {
        "generated_at": core.now_iso(),
        "default_domain_name": core.default_frontend_domain_name(settings),
        "domains": domains,
        "frontend_nodes": frontend_nodes,
        "iepl_rules": iepl_rules,
        "iepl_nodes": iepl_nodes,
        "settings": redact_settings(settings),
        "paths": {
            "settings": str(core.SETTINGS_PATH),
            "state": str(core.STATE_PATH),
            "frontend_installed": str(core.FORWARD_INSTALLED_PATH),
            "legacy_frontend_config": str(core.REALM_CONFIG_PATH),
            "iepl_config": str(core.IEPL_CONFIG_PATH),
        },
        "stats": {
            "domain_count": len(domains),
            "enabled_domain_count": len([item for item in domains if item["enabled"]]),
            "frontend_node_count": len(frontend_nodes),
            "assigned_frontend_node_count": assigned_frontend_nodes,
            "iepl_node_count": len(iepl_nodes),
            "iepl_rule_count": len(iepl_rules),
        },
        "logs": read_log_tail(),
    }


def describe_apply_result(result: dict) -> str:
    summary = core.format_forward_apply_summary(result)
    errors = result.get("errors", [])
    if not errors:
        return summary
    details = ", ".join(
        f"{item.get('name') or item.get('uuid') or '-'}: {item.get('error') or 'unknown error'}"
        for item in errors[:2]
    )
    if len(errors) > 2:
        details = f"{details}, +{len(errors) - 2} more"
    return f"{summary}; {details}"


def parse_port(value, field_name: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"{field_name} must be an integer") from None
    if port < 1 or port > 65535:
        raise ApiError(f"{field_name} must be between 1 and 65535")
    return port


def parse_positive_int(value, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"{field_name} must be an integer") from None
    if parsed <= 0:
        raise ApiError(f"{field_name} must be a positive integer")
    return parsed


def parse_forward_rule_payload(payload: dict) -> tuple[int, str, int]:
    listen_port = parse_port(payload.get("listen_port"), "listen_port")
    remote_host = str(payload.get("remote_host", "")).strip()
    if not remote_host:
        raise ApiError("remote_host is required")
    remote_port = parse_port(payload.get("remote_port"), "remote_port")
    return listen_port, remote_host, remote_port


def action_sync(record_name: str | None = None) -> dict:
    settings = core.load_settings()
    core.cmd_sync(settings, record_name=record_name)
    return {"message": f"sync completed for {record_name or 'all frontend domains'}"}


def action_switch_now(record_name: str) -> dict:
    settings = core.load_settings()
    resolved = core.resolve_frontend_domain_name(settings, record_name)
    core.cmd_switch_now(settings, record_name=resolved)
    return {"message": f"manual switch completed for {resolved}"}


def action_reinstall_domain_forward(record_name: str) -> dict:
    settings = core.load_settings()
    resolved = core.resolve_frontend_domain_name(settings, record_name)
    result = core.cmd_reinstall_forward(settings, record_name=resolved)
    return {"message": f"frontend forwarding reinstalled for {resolved}: {describe_apply_result(result)}", "result": result}


def action_save_frontend_domain(original_record_name: str | None, payload: dict) -> dict:
    settings = core.load_settings()
    domain_payload = {
        "record_name": str(payload.get("record_name", "")).strip(),
        "enabled": bool(payload.get("enabled", True)),
        "preferred_primary_uuid": str(payload.get("preferred_primary_uuid", "")).strip(),
        "backup_uuids": [str(item).strip() for item in payload.get("backup_uuids", [])],
        "forward_rules": list(payload.get("forward_rules", [])),
    }
    saved_domain, result = core.save_frontend_domain_transactionally(settings, original_record_name, domain_payload)
    return {
        "message": f"frontend domain saved: {saved_domain['record_name']} ({describe_apply_result(result)})",
        "domain": saved_domain,
        "result": result,
    }


def action_delete_frontend_domain(record_name: str) -> dict:
    settings = core.load_settings()
    removed, result = core.delete_frontend_domain_transactionally(settings, record_name)
    return {
        "message": f"frontend domain deleted: {removed['record_name']} ({describe_apply_result(result)})",
        "domain": removed,
        "result": result,
    }


def action_add_forward(payload: dict) -> dict:
    settings = core.load_settings()
    listen_port, remote_host, remote_port = parse_forward_rule_payload(payload)
    record_name = payload.get("record_name") or payload.get("domain")
    result = core.cmd_add_forward(settings, record_name, listen_port, remote_host, remote_port)
    return {"message": f"frontend rule added ({describe_apply_result(result)})", "result": result}


def action_update_forward(payload: dict) -> dict:
    settings = core.load_settings()
    listen_port, remote_host, remote_port = parse_forward_rule_payload(payload)
    old_listen_port = parse_port(payload.get("old_listen_port"), "old_listen_port")
    record_name = payload.get("record_name") or payload.get("domain")
    result = core.cmd_update_forward(settings, record_name, old_listen_port, listen_port, remote_host, remote_port)
    return {"message": f"frontend rule updated ({describe_apply_result(result)})", "result": result}


def action_remove_forward(payload: dict) -> dict:
    settings = core.load_settings()
    listen_port = parse_port(payload.get("listen_port"), "listen_port")
    record_name = payload.get("record_name") or payload.get("domain")
    result = core.cmd_remove_forward(settings, record_name, listen_port)
    return {"message": f"frontend rule removed ({describe_apply_result(result)})", "result": result}


def action_add_iepl_forward(payload: dict) -> dict:
    settings = core.load_settings()
    listen_port, remote_host, remote_port = parse_forward_rule_payload(payload)
    added, result = core.add_forward_rule_transactionally(
        settings,
        core.IEPL_CONFIG_PATH,
        core.apply_iepl_forwards_now,
        listen_port,
        remote_host,
        remote_port,
    )
    return {"message": f"IEPL rule added on {added['listen_port']} ({describe_apply_result(result)})", "result": result}


def action_update_iepl_forward(payload: dict) -> dict:
    settings = core.load_settings()
    listen_port, remote_host, remote_port = parse_forward_rule_payload(payload)
    old_listen_port = parse_port(payload.get("old_listen_port"), "old_listen_port")
    updated, result = core.update_forward_rule_transactionally(
        settings,
        core.IEPL_CONFIG_PATH,
        core.apply_iepl_forwards_now,
        old_listen_port,
        listen_port,
        remote_host,
        remote_port,
    )
    return {"message": f"IEPL rule updated ({describe_apply_result(result)})", "change": updated, "result": result}


def action_remove_iepl_forward(payload: dict) -> dict:
    settings = core.load_settings()
    listen_port = parse_port(payload.get("listen_port"), "listen_port")
    removed, result = core.remove_forward_rule_transactionally(
        settings,
        core.IEPL_CONFIG_PATH,
        core.apply_iepl_forwards_now,
        listen_port,
    )
    return {"message": f"IEPL rule removed on {removed['listen_port']} ({describe_apply_result(result)})", "result": result}


def action_reinstall_forward(uuid: str) -> dict:
    settings = core.load_settings()
    result = core.cmd_reinstall_forward(settings, uuid=uuid)
    message = f"forwarding reinstalled for {uuid}"
    if result.get("domain_name"):
        message = f"{message} in domain {result['domain_name']}"
    return {"message": message, "result": result}


def action_cleanup_frontend_legacy() -> dict:
    settings = core.load_settings()
    count = core.cleanup_frontend_legacy_now(settings)
    return {"message": f"legacy frontend forwarding cleanup finished on {count} nodes", "count": count}


def action_cleanup_iepl_legacy(uuid: str) -> dict:
    settings = core.load_settings()
    target_ip = core.cleanup_iepl_legacy_now(settings, uuid)
    return {"message": f"legacy forwarding cleaned on IEPL target {target_ip}", "target_ip": target_ip}


def action_restart_iepl_realm(uuid: str) -> dict:
    settings = core.load_settings()
    target_ip = core.restart_iepl_realm_now(settings, uuid)
    return {"message": f"realm restarted on IEPL target {target_ip}", "target_ip": target_ip}


def action_set_frontend_node_traffic_limit(uuid: str, limit_gb: int) -> dict:
    settings = core.load_settings()
    core.set_frontend_node_traffic_limit_gb(settings, uuid, limit_gb)
    return {"message": f"traffic limit saved for {uuid}: {limit_gb}GB"}


def action_clear_frontend_node_traffic_limit(uuid: str) -> dict:
    settings = core.load_settings()
    core.clear_frontend_node_traffic_limit_gb(settings, uuid)
    return {"message": f"traffic limit reset to default for {uuid}"}


def action_setup_save(payload: dict) -> dict:
    settings_text = str(payload.get("settings_text", "")).strip()
    if not settings_text:
        raise ApiError("settings_text is required")
    try:
        raw_settings = json.loads(settings_text)
    except json.JSONDecodeError as exc:
        raise ApiError(f"invalid settings JSON: {exc}") from exc
    normalized = core.normalize_settings(raw_settings)
    core.save_settings(normalized)
    warning = ensure_monitor_service_started()
    message = "settings.json saved successfully"
    if warning:
        message = f"{message}; {warning}"
    return {"message": message, "warning": warning}


class Handler(BaseHTTPRequestHandler):
    server_version = "aliMonitorWebUI/2"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: pathlib.Path, content_type: str | None = None) -> None:
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or guess_content_type(path))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError(f"invalid JSON payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiError("JSON payload must be an object")
        return payload

    def _require_string(self, payload: dict, field: str) -> str:
        value = str(payload.get(field, "")).strip()
        if not value:
            raise ApiError(f"{field} is required")
        return value

    def _perform_action(self, callback):
        with ACTION_LOCK:
            return callback()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/overview":
                self._send_json({"ok": True, "data": build_overview()})
                return
            if parsed.path == "/api/bootstrap":
                self._send_json({"ok": True, "data": build_setup_payload()})
                return
            if parsed.path == "/api/logs":
                self._send_json({"ok": True, "data": read_log_tail()})
                return
            self._send_file(resolve_static_path(parsed.path))
        except ApiError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc), "traceback": traceback.format_exc()}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/sync":
                result = self._perform_action(lambda: action_sync())
            elif parsed.path == "/api/domain-sync":
                result = self._perform_action(lambda: action_sync(self._require_string(payload, "record_name")))
            elif parsed.path == "/api/domain-switch-now":
                result = self._perform_action(lambda: action_switch_now(self._require_string(payload, "record_name")))
            elif parsed.path == "/api/domain-reinstall-forward":
                result = self._perform_action(lambda: action_reinstall_domain_forward(self._require_string(payload, "record_name")))
            elif parsed.path == "/api/frontend-domain-save":
                result = self._perform_action(
                    lambda: action_save_frontend_domain(
                        str(payload.get("original_record_name", "")).strip() or None,
                        payload.get("domain") if isinstance(payload.get("domain"), dict) else payload,
                    )
                )
            elif parsed.path == "/api/frontend-domain-delete":
                result = self._perform_action(lambda: action_delete_frontend_domain(self._require_string(payload, "record_name")))
            elif parsed.path == "/api/add-forward":
                result = self._perform_action(lambda: action_add_forward(payload))
            elif parsed.path == "/api/update-forward":
                result = self._perform_action(lambda: action_update_forward(payload))
            elif parsed.path == "/api/remove-forward":
                result = self._perform_action(lambda: action_remove_forward(payload))
            elif parsed.path == "/api/add-iepl-forward":
                result = self._perform_action(lambda: action_add_iepl_forward(payload))
            elif parsed.path == "/api/update-iepl-forward":
                result = self._perform_action(lambda: action_update_iepl_forward(payload))
            elif parsed.path == "/api/remove-iepl-forward":
                result = self._perform_action(lambda: action_remove_iepl_forward(payload))
            elif parsed.path == "/api/reinstall-forward":
                result = self._perform_action(lambda: action_reinstall_forward(self._require_string(payload, "uuid")))
            elif parsed.path == "/api/cleanup-frontend-legacy":
                result = self._perform_action(action_cleanup_frontend_legacy)
            elif parsed.path == "/api/cleanup-iepl-legacy":
                result = self._perform_action(lambda: action_cleanup_iepl_legacy(self._require_string(payload, "uuid")))
            elif parsed.path == "/api/restart-iepl-realm":
                result = self._perform_action(lambda: action_restart_iepl_realm(self._require_string(payload, "uuid")))
            elif parsed.path == "/api/set-frontend-node-traffic-limit":
                uuid = self._require_string(payload, "uuid")
                limit_gb = parse_positive_int(payload.get("traffic_limit_gb"), "traffic_limit_gb")
                result = self._perform_action(lambda: action_set_frontend_node_traffic_limit(uuid, limit_gb))
            elif parsed.path == "/api/clear-frontend-node-traffic-limit":
                result = self._perform_action(lambda: action_clear_frontend_node_traffic_limit(self._require_string(payload, "uuid")))
            elif parsed.path == "/api/setup-save":
                result = self._perform_action(lambda: action_setup_save(payload))
            else:
                self._send_json({"ok": False, "error": f"not found: {parsed.path}"}, status=404)
                return
            self._send_json({"ok": True, "result": result})
        except ApiError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception as exc:
            status = 400 if isinstance(exc, (RuntimeError, ValueError)) else 500
            self._send_json({"ok": False, "error": str(exc), "traceback": traceback.format_exc()}, status=status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="aliMonitor WebUI")
    parser.add_argument("--host", default="0.0.0.0", help="listen host")
    parser.add_argument("--port", default=8080, type=int, help="listen port")
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[*] aliMonitor WebUI listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
