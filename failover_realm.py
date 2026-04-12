#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
import pathlib
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import copy
import urllib.error
import urllib.parse
import urllib.request


BASE_DIR = pathlib.Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"
SETTINGS_TEMPLATE_PATH = BASE_DIR / "settings.multi-domain.example.json"
LEGACY_SETTINGS_TEMPLATE_PATH = BASE_DIR / "settings.komari.example.json"
STATE_PATH = BASE_DIR / "komari_state.json"
FORWARD_INSTALLED_PATH = BASE_DIR / "forward_installed.json"
TAG_CACHE_PATH = BASE_DIR / "tag_cache.json"
REALM_CONFIG_PATH = BASE_DIR / "config.toml"
IEPL_CONFIG_PATH = BASE_DIR / "iepl_config.toml"
UNINSTALL_REALM_SCRIPT_PATH = BASE_DIR / "uninstall_realm.sh"
TCP_POOL_BINARY_PATH = "/root/tcp_pool"
TCP_POOL_CONFIG_PATH = "/etc/tcp_pool/relays.conf"

UPTIME_LIMIT_DAYS = 80
TRAFFIC_LIMIT_BYTES = 450 * 1024 * 1024 * 1024
BYTES_PER_GB = 1024 * 1024 * 1024
DEFAULT_TRAFFIC_LIMIT_GB = TRAFFIC_LIMIT_BYTES // BYTES_PER_GB


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_json(path: pathlib.Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_name = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)


def save_text(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def file_sha256(path: pathlib.Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_remote_host(remote_host: str) -> str:
    remote_host = str(remote_host).strip()
    if not remote_host:
        raise RuntimeError("remote host is required")
    try:
        return str(ipaddress.ip_address(remote_host))
    except ValueError:
        return remote_host


def run_cmd(
    cmd: list[str],
    check: bool = True,
    input_text: str | None = None,
    timeout_sec: int | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
        timeout=timeout_sec,
    )


def load_settings() -> dict:
    settings = load_json(SETTINGS_PATH, None)
    if settings is None:
        template_names = [path.name for path in [SETTINGS_TEMPLATE_PATH, LEGACY_SETTINGS_TEMPLATE_PATH] if path.exists()]
        template_hint = template_names[0] if template_names else SETTINGS_TEMPLATE_PATH.name
        raise RuntimeError(
            f"missing {SETTINGS_PATH}. Copy {template_hint} to settings.json first."
        )

    required = ["komari", "cloudflare", "ssh"]
    missing = [k for k in required if k not in settings]
    if missing:
        raise RuntimeError(f"settings.json missing keys: {', '.join(missing)}")

    komari_required = ["base_url", "api_key", "group_name"]
    komari_missing = [k for k in komari_required if not settings["komari"].get(k)]
    if komari_missing:
        raise RuntimeError(f"settings.json komari missing keys: {', '.join(komari_missing)}")

    cf_required = ["api_token", "zone_id"]
    cf_missing = [k for k in cf_required if not settings["cloudflare"].get(k)]
    if cf_missing:
        raise RuntimeError(f"settings.json cloudflare missing keys: {', '.join(cf_missing)}")

    ssh_required = ["user"]
    ssh_missing = [k for k in ssh_required if not settings["ssh"].get(k)]
    if ssh_missing:
        raise RuntimeError(f"settings.json ssh missing keys: {', '.join(ssh_missing)}")

    iepl_target_ips = settings.get("iepl_target_ips", {})
    if iepl_target_ips and not isinstance(iepl_target_ips, dict):
        raise RuntimeError("settings.json iepl_target_ips must be an object mapping node uuid/name to IPv4")

    if "frontend_domains" in settings:
        settings["frontend_domains"] = normalize_frontend_domains(settings["frontend_domains"])
    else:
        record_name = str(settings["cloudflare"].get("record_name", "")).strip()
        if not record_name:
            raise RuntimeError("settings.json cloudflare missing keys: record_name")
        settings["frontend_domains"] = build_legacy_frontend_domains(settings)

    settings["frontend_node_traffic_limits_gb"] = normalize_frontend_node_traffic_limits_gb(
        settings.get("frontend_node_traffic_limits_gb", {})
    )

    return settings


def save_settings(settings: dict) -> None:
    save_json(SETTINGS_PATH, settings)


def normalize_uuid_list(raw_value) -> list[str]:
    if raw_value in (None, []):
        return []
    if not isinstance(raw_value, list):
        raise RuntimeError("uuid list must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_value:
        uuid = str(raw_item).strip()
        if not uuid or uuid in seen:
            continue
        seen.add(uuid)
        result.append(uuid)
    return result


def normalize_frontend_forward_rules(raw_rules, record_name: str) -> list[dict]:
    if raw_rules in (None, []):
        return []
    if not isinstance(raw_rules, list):
        raise RuntimeError(f"frontend_domains[{record_name}] forward_rules must be an array")
    rules: list[dict] = []
    seen_ports: set[int] = set()
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            raise RuntimeError(f"frontend_domains[{record_name}] contains an invalid forward rule")
        try:
            listen_port = int(raw_rule.get("listen_port"))
            remote_port = int(raw_rule.get("remote_port"))
        except (TypeError, ValueError):
            raise RuntimeError(f"frontend_domains[{record_name}] forward rule ports must be integers") from None
        if listen_port < 1 or listen_port > 65535:
            raise RuntimeError(f"frontend_domains[{record_name}] listen_port out of range: {listen_port}")
        if remote_port < 1 or remote_port > 65535:
            raise RuntimeError(f"frontend_domains[{record_name}] remote_port out of range: {remote_port}")
        if listen_port in seen_ports:
            raise RuntimeError(f"frontend_domains[{record_name}] listen_port duplicated: {listen_port}")
        seen_ports.add(listen_port)
        rules.append(
            {
                "listen_port": listen_port,
                "remote_host": normalize_remote_host(raw_rule.get("remote_host", "")),
                "remote_port": remote_port,
            }
        )
    return sorted(rules, key=lambda item: item["listen_port"])


def normalize_frontend_domain(record_name: str, raw_domain: dict, allow_legacy: bool = False) -> dict:
    if not isinstance(raw_domain, dict):
        raise RuntimeError(f"frontend_domains[{record_name}] must be an object")
    normalized_record_name = str(raw_domain.get("record_name") or record_name).strip()
    if not normalized_record_name:
        raise RuntimeError("frontend domain record_name is required")
    preferred_primary_uuid = str(raw_domain.get("preferred_primary_uuid", "")).strip()
    backup_uuids = normalize_uuid_list(raw_domain.get("backup_uuids", []))
    if preferred_primary_uuid and preferred_primary_uuid in backup_uuids:
        raise RuntimeError(f"frontend_domains[{normalized_record_name}] backup_uuids cannot contain preferred_primary_uuid")
    legacy_pool = bool(raw_domain.get("legacy_pool", False)) if allow_legacy else False
    if not preferred_primary_uuid and not legacy_pool:
        raise RuntimeError(f"frontend_domains[{normalized_record_name}] preferred_primary_uuid is required")
    return {
        "record_name": normalized_record_name,
        "enabled": bool(raw_domain.get("enabled", True)),
        "preferred_primary_uuid": preferred_primary_uuid,
        "backup_uuids": backup_uuids,
        "forward_rules": normalize_frontend_forward_rules(raw_domain.get("forward_rules", []), normalized_record_name),
        **({"legacy_pool": True} if legacy_pool else {}),
    }


def normalize_frontend_domains(raw_domains, allow_legacy: bool = False) -> dict[str, dict]:
    if raw_domains in (None, {}):
        raise RuntimeError("settings.json frontend_domains must contain at least one domain")
    if not isinstance(raw_domains, dict):
        raise RuntimeError("settings.json frontend_domains must be an object mapping record_name to config")
    normalized: dict[str, dict] = {}
    claimed_nodes: dict[str, str] = {}
    for raw_record_name, raw_domain in raw_domains.items():
        record_name = str(raw_record_name).strip()
        if not record_name:
            raise RuntimeError("settings.json frontend_domains contains an empty record_name key")
        domain = normalize_frontend_domain(record_name, raw_domain, allow_legacy=allow_legacy)
        normalized[record_name] = domain
        if domain.get("legacy_pool"):
            continue
        for uuid in [domain["preferred_primary_uuid"], *domain["backup_uuids"]]:
            owner = claimed_nodes.get(uuid)
            if owner and owner != record_name:
                raise RuntimeError(f"node {uuid} is assigned to multiple frontend domains: {owner}, {record_name}")
            claimed_nodes[uuid] = record_name
    return normalized


def build_legacy_frontend_domains(settings: dict) -> dict[str, dict]:
    record_name = str(settings.get("cloudflare", {}).get("record_name", "")).strip()
    if not record_name:
        raise RuntimeError("settings.json cloudflare missing keys: record_name")
    legacy_state = load_json(STATE_PATH, {})
    preferred_primary_uuid = str(legacy_state.get("current_primary_uuid", "")).strip()
    rules = list_forward_rules_from_path(REALM_CONFIG_PATH) if REALM_CONFIG_PATH.exists() else []
    return normalize_frontend_domains(
        {
            record_name: {
                "record_name": record_name,
                "enabled": True,
                "preferred_primary_uuid": preferred_primary_uuid,
                "backup_uuids": [],
                "forward_rules": rules,
                "legacy_pool": True,
            }
        },
        allow_legacy=True,
    )


def list_frontend_domain_names(settings: dict, enabled_only: bool = False) -> list[str]:
    names: list[str] = []
    for record_name, domain in settings.get("frontend_domains", {}).items():
        if enabled_only and not domain.get("enabled", True):
            continue
        names.append(record_name)
    return names


def get_frontend_domain(settings: dict, record_name: str) -> dict:
    record_name = str(record_name).strip()
    domain = settings.get("frontend_domains", {}).get(record_name)
    if domain is None:
        raise RuntimeError(f"frontend domain not found: {record_name}")
    return domain


def default_frontend_domain_name(settings: dict) -> str | None:
    names = list_frontend_domain_names(settings, enabled_only=False)
    return names[0] if names else None


def resolve_frontend_domain_name(settings: dict, record_name: str | None) -> str:
    if record_name:
        return get_frontend_domain(settings, record_name)["record_name"]
    names = list_frontend_domain_names(settings, enabled_only=False)
    if len(names) == 1:
        return names[0]
    raise RuntimeError("multiple frontend domains configured, --domain is required")


def make_frontend_rules_hash(rules: list[dict]) -> str:
    payload = json.dumps(sorted(rules, key=lambda item: item["listen_port"]), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def frontend_domain_member_uuids(settings: dict, record_name: str, snapshot: dict | None = None) -> list[str]:
    domain = get_frontend_domain(settings, record_name)
    preferred_primary_uuid = str(domain.get("preferred_primary_uuid", "")).strip()
    explicit = []
    if preferred_primary_uuid:
        explicit.append(preferred_primary_uuid)
    explicit.extend([uuid for uuid in domain.get("backup_uuids", []) if uuid != preferred_primary_uuid])
    if domain.get("legacy_pool"):
        if snapshot is None:
            return explicit
        ordered_snapshot = [item["uuid"] for item in snapshot["nodes"]]
        if preferred_primary_uuid and preferred_primary_uuid in ordered_snapshot:
            return [preferred_primary_uuid, *[uuid for uuid in ordered_snapshot if uuid != preferred_primary_uuid]]
        return ordered_snapshot
    return explicit


def frontend_domain_member_items(settings: dict, snapshot: dict, record_name: str) -> list[dict]:
    items_by_uuid = {item["uuid"]: item for item in snapshot["nodes"]}
    return [items_by_uuid[uuid] for uuid in frontend_domain_member_uuids(settings, record_name, snapshot) if uuid in items_by_uuid]


def frontend_domain_owner_map(settings: dict, snapshot: dict | None = None) -> dict[str, str]:
    owners: dict[str, str] = {}
    for record_name in list_frontend_domain_names(settings, enabled_only=False):
        for uuid in frontend_domain_member_uuids(settings, record_name, snapshot):
            owners[uuid] = record_name
    return owners


def validate_frontend_domain_assignments(settings: dict, snapshot: dict) -> None:
    items_by_uuid = {item["uuid"]: item for item in snapshot["nodes"]}
    owners: dict[str, str] = {}
    for record_name in list_frontend_domain_names(settings, enabled_only=False):
        domain = get_frontend_domain(settings, record_name)
        if not domain.get("enabled", True):
            continue
        if domain.get("legacy_pool"):
            continue
        preferred_primary_uuid = str(domain.get("preferred_primary_uuid", "")).strip()
        if not preferred_primary_uuid:
            raise RuntimeError(f"{record_name} missing preferred_primary_uuid")
        if preferred_primary_uuid not in items_by_uuid:
            raise RuntimeError(f"{record_name} primary node not found in current frontend candidates: {preferred_primary_uuid}")
        for uuid in [preferred_primary_uuid, *domain.get("backup_uuids", [])]:
            if uuid not in items_by_uuid:
                raise RuntimeError(f"{record_name} backup node not found in current frontend candidates: {uuid}")
            owner = owners.get(uuid)
            if owner and owner != record_name:
                raise RuntimeError(f"node {uuid} is already assigned to frontend domain {owner}")
            owners[uuid] = record_name


def blank_domain_runtime_state() -> dict:
    return {
        "current_primary_uuid": "",
        "current_primary_name": "",
        "current_primary_ip": "",
        "offline_fail_count": 0,
        "last_switch_reason": "",
        "last_switch_at": "",
        "last_switch_ts": 0,
    }


def normalize_domain_runtime_state(raw_state: dict | None) -> dict:
    raw_state = raw_state or {}
    state = blank_domain_runtime_state()
    state["current_primary_uuid"] = str(raw_state.get("current_primary_uuid", "")).strip()
    state["current_primary_name"] = str(raw_state.get("current_primary_name", "")).strip()
    state["current_primary_ip"] = str(raw_state.get("current_primary_ip", "")).strip()
    state["offline_fail_count"] = int(raw_state.get("offline_fail_count", 0) or 0)
    state["last_switch_reason"] = str(raw_state.get("last_switch_reason", "")).strip()
    state["last_switch_at"] = str(raw_state.get("last_switch_at", "")).strip()
    state["last_switch_ts"] = int(raw_state.get("last_switch_ts", 0) or 0)
    return state


def normalize_runtime_state(raw_state: dict | None, settings: dict) -> dict:
    raw_state = raw_state if isinstance(raw_state, dict) else {}
    result = {"domains": {}}
    raw_domains = raw_state.get("domains")
    if isinstance(raw_domains, dict):
        for record_name in list_frontend_domain_names(settings, enabled_only=False):
            result["domains"][record_name] = normalize_domain_runtime_state(raw_domains.get(record_name))
    elif raw_state:
        default_record_name = default_frontend_domain_name(settings)
        if default_record_name:
            result["domains"][default_record_name] = normalize_domain_runtime_state(raw_state)
    for record_name in list_frontend_domain_names(settings, enabled_only=False):
        result["domains"].setdefault(record_name, blank_domain_runtime_state())
    return result


def load_runtime_state(settings: dict) -> dict:
    return normalize_runtime_state(load_json(STATE_PATH, {}), settings)


def save_runtime_state(state: dict) -> None:
    save_json(STATE_PATH, {"domains": state.get("domains", {})})


def get_domain_runtime_state(state: dict, record_name: str) -> dict:
    domains = state.setdefault("domains", {})
    if record_name not in domains:
        domains[record_name] = blank_domain_runtime_state()
    return domains[record_name]


def primary_uuid_for_jump_host(settings: dict, state: dict) -> str | None:
    for record_name in list_frontend_domain_names(settings, enabled_only=True):
        domain_state = get_domain_runtime_state(state, record_name)
        if domain_state.get("current_primary_uuid"):
            return domain_state["current_primary_uuid"]
    for record_name in list_frontend_domain_names(settings, enabled_only=False):
        domain_state = get_domain_runtime_state(state, record_name)
        if domain_state.get("current_primary_uuid"):
            return domain_state["current_primary_uuid"]
    return None


def normalize_frontend_node_traffic_limits_gb(raw_limits) -> dict[str, int]:
    if raw_limits in (None, {}):
        return {}
    if not isinstance(raw_limits, dict):
        raise RuntimeError("settings.json frontend_node_traffic_limits_gb must be an object mapping node uuid to GB")
    normalized: dict[str, int] = {}
    for raw_uuid, raw_value in raw_limits.items():
        uuid = str(raw_uuid).strip()
        if not uuid:
            raise RuntimeError("settings.json frontend_node_traffic_limits_gb contains an empty uuid key")
        if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value <= 0:
            raise RuntimeError(
                f"settings.json frontend_node_traffic_limits_gb[{uuid}] must be a positive integer GB value"
            )
        normalized[uuid] = int(raw_value)
    return normalized


def traffic_limit_gb_to_bytes(limit_gb: int) -> int:
    if isinstance(limit_gb, bool) or not isinstance(limit_gb, int) or limit_gb <= 0:
        raise RuntimeError("traffic limit GB must be a positive integer")
    return limit_gb * BYTES_PER_GB


def get_frontend_node_traffic_limit_info(settings: dict, item: dict) -> tuple[int, str]:
    limits = normalize_frontend_node_traffic_limits_gb(settings.get("frontend_node_traffic_limits_gb", {}))
    raw_value = limits.get(str(item.get("uuid", "")).strip())
    if raw_value is None:
        return DEFAULT_TRAFFIC_LIMIT_GB, "default"
    return int(raw_value), "custom"


def set_frontend_node_traffic_limit_gb(settings: dict, uuid: str, limit_gb: int) -> dict:
    uuid = str(uuid).strip()
    if not uuid:
        raise RuntimeError("uuid is required")
    traffic_limit_gb_to_bytes(limit_gb)
    limits = dict(normalize_frontend_node_traffic_limits_gb(settings.get("frontend_node_traffic_limits_gb", {})))
    limits[uuid] = int(limit_gb)
    settings["frontend_node_traffic_limits_gb"] = limits
    save_settings(settings)
    return settings


def clear_frontend_node_traffic_limit_gb(settings: dict, uuid: str) -> dict:
    uuid = str(uuid).strip()
    if not uuid:
        raise RuntimeError("uuid is required")
    limits = dict(normalize_frontend_node_traffic_limits_gb(settings.get("frontend_node_traffic_limits_gb", {})))
    limits.pop(uuid, None)
    settings["frontend_node_traffic_limits_gb"] = limits
    save_settings(settings)
    return settings


class CloudflareClient:
    def __init__(self, api_token: str, zone_id: str):
        self.api_token = api_token
        self.zone_id = zone_id
        self.base = "https://api.cloudflare.com/client/v4"

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        payload = None
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url=url, data=payload, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Cloudflare HTTP error {e.code}: {detail}") from e

        if not data.get("success", False):
            raise RuntimeError(f"Cloudflare API failed: {data}")
        return data

    def get_a_record(self, name: str) -> dict:
        data = self._request(
            "GET",
            f"/zones/{self.zone_id}/dns_records",
            params={"type": "A", "name": name, "per_page": 1},
        )
        result = data.get("result", [])
        if not result:
            raise RuntimeError(f"A record not found: {name}")
        return result[0]

    def update_record_content(self, record_id: str, name: str, ip: str, ttl: int, proxied: bool) -> None:
        self._request(
            "PUT",
            f"/zones/{self.zone_id}/dns_records/{record_id}",
            body={
                "type": "A",
                "name": name,
                "content": ip,
                "ttl": ttl,
                "proxied": proxied,
            },
        )


class KomariRpcClient:
    def __init__(self, base_url: str, api_key: str, timeout_sec: int = 20):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec
        self.rpc_url = f"{self.base_url}/api/rpc2"

    def call(self, method: str, params: dict | list | None = None):
        body = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method,
            "params": params if params is not None else {},
        }
        req = urllib.request.Request(
            url=self.rpc_url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Komari RPC HTTP error {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Komari RPC request failed: {e}") from e

        if payload.get("error"):
            raise RuntimeError(f"Komari RPC error for {method}: {payload['error']}")
        if "result" not in payload:
            raise RuntimeError(f"Komari RPC invalid response for {method}: {payload}")
        return payload["result"]

    def get_nodes(self) -> dict[str, dict]:
        result = self.call("common:getNodes", {})
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {item["uuid"]: item for item in result if isinstance(item, dict) and item.get("uuid")}
        raise RuntimeError(f"unexpected common:getNodes result: {type(result).__name__}")

    def get_latest_statuses(self, uuids: list[str]) -> dict[str, dict]:
        if not uuids:
            return {}
        result = self.call("common:getNodesLatestStatus", {"uuids": uuids})
        if not isinstance(result, dict):
            raise RuntimeError(
                f"unexpected common:getNodesLatestStatus result: {type(result).__name__}"
            )
        return result


class KomariAdminClient:
    def __init__(self, base_url: str, api_key: str, timeout_sec: int = 20):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec

    def edit_client(self, uuid: str, body: dict) -> dict:
        url = f"{self.base_url}/api/admin/client/{urllib.parse.quote(uuid)}/edit"
        req = urllib.request.Request(
            url=url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Komari admin HTTP error {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Komari admin request failed: {e}") from e


def build_ssh_base(settings: dict) -> list[str]:
    ssh = settings["ssh"]
    user = ssh.get("user", "root")
    port = str(ssh.get("port", 22))
    timeout = str(ssh.get("connect_timeout", 10))
    base = [
        "-p",
        port,
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={timeout}",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=3",
        f"{user}@{{host}}",
    ]

    if ssh.get("password"):
        base.insert(-1, "-o")
        base.insert(-1, "PreferredAuthentications=password")
        base.insert(-1, "-o")
        base.insert(-1, "PubkeyAuthentication=no")
    else:
        key = os.path.expanduser(ssh.get("private_key", "~/.ssh/id_rsa"))
        base.insert(2, "-i")
        base.insert(3, key)

    return base


def with_sshpass(settings: dict, cmd: list[str]) -> list[str]:
    password = settings.get("ssh", {}).get("password")
    if not password:
        return cmd
    return ["sshpass", "-p", password, *cmd]


def ssh_run_stream(settings: dict, host: str, script: str, timeout_sec: int = 60) -> None:
    base = build_ssh_base(settings)
    target = [x.format(host=host) for x in base]
    cmd = with_sshpass(settings, ["ssh", *target, "bash", "-s"])
    proc = subprocess.Popen(
        cmd,
        text=True,
        stdin=subprocess.PIPE,
        stdout=None,
        stderr=None,
    )
    try:
        if proc.stdin is not None:
            proc.stdin.write(script)
            proc.stdin.close()
        rc = proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired as e:
        proc.kill()
        raise RuntimeError(f"ssh command timed out after {timeout_sec}s on {host}") from e

    if rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)


def scp_upload(settings: dict, host: str, local_file: pathlib.Path, remote_file: str) -> None:
    ssh = settings["ssh"]
    user = ssh.get("user", "root")
    port = str(ssh.get("port", 22))
    timeout = str(ssh.get("connect_timeout", 10))
    cmd = ["scp", "-P", port, "-o", "StrictHostKeyChecking=accept-new", "-o", f"ConnectTimeout={timeout}"]
    if ssh.get("password"):
        cmd.extend(["-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no"])
    else:
        key = os.path.expanduser(ssh.get("private_key", "~/.ssh/id_rsa"))
        cmd.extend(["-i", key])
    cmd.extend([str(local_file), f"{user}@{host}:{remote_file}"])
    run_cmd(with_sshpass(settings, cmd), check=True, timeout_sec=120)


def parse_forward_rules_from_path(config_path: pathlib.Path, allow_empty: bool = False) -> list[dict]:
    if not config_path.exists():
        if allow_empty:
            return []
        raise RuntimeError(f"missing forwarding config: {config_path}")
    with config_path.open("rb") as f:
        data = tomllib.load(f)

    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        if allow_empty:
            return []
        raise RuntimeError(f"no [[endpoints]] found in {config_path}")

    rules = []
    for item in endpoints:
        if not isinstance(item, dict):
            continue
        listen = str(item.get("listen", "")).strip()
        remote = str(item.get("remote", "")).strip()
        if not listen or not remote:
            raise RuntimeError(f"invalid endpoint in {config_path}: listen/remote required")
        listen_host, listen_port = listen.rsplit(":", 1)
        remote_host, remote_port = remote.rsplit(":", 1)
        if listen_host not in {"0.0.0.0", "::", "[::]"}:
            raise RuntimeError(f"unsupported listen host in {config_path}: {listen}")
        rules.append(
            {
                "listen_port": int(listen_port),
                "remote_host": remote_host.strip("[]"),
                "remote_port": int(remote_port),
            }
        )
    return rules


def parse_forward_rules(allow_empty: bool = False) -> list[dict]:
    return parse_forward_rules_from_path(REALM_CONFIG_PATH, allow_empty=allow_empty)


def render_forward_config(rules: list[dict], use_udp: bool = True, no_tcp: bool = False) -> str:
    lines = [
        "[network]",
        f"no_tcp = {'true' if no_tcp else 'false'}",
        f"use_udp = {'true' if use_udp else 'false'}",
        "",
    ]
    for rule in sorted(rules, key=lambda item: item["listen_port"]):
        lines.extend(
            [
                "[[endpoints]]",
                f"listen = \"0.0.0.0:{rule['listen_port']}\"",
                f"remote = \"{rule['remote_host']}:{rule['remote_port']}\"",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def load_forward_config() -> tuple[list[dict], dict]:
    if not REALM_CONFIG_PATH.exists():
        return [], {"use_udp": True, "no_tcp": False}
    with REALM_CONFIG_PATH.open("rb") as f:
        data = tomllib.load(f)
    network = data.get("network") or {}
    rules = parse_forward_rules(allow_empty=True)
    options = {
        "use_udp": bool(network.get("use_udp", True)),
        "no_tcp": bool(network.get("no_tcp", False)),
    }
    return rules, options


def load_forward_options(config_path: pathlib.Path) -> dict:
    if not config_path.exists():
        return {"use_udp": True, "no_tcp": False}
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    network = data.get("network") or {}
    return {
        "use_udp": bool(network.get("use_udp", True)),
        "no_tcp": bool(network.get("no_tcp", False)),
    }


def save_forward_config(rules: list[dict], options: dict) -> None:
    content = render_forward_config(
        rules,
        use_udp=bool(options.get("use_udp", True)),
        no_tcp=bool(options.get("no_tcp", False)),
    )
    save_text(REALM_CONFIG_PATH, content)


def load_forward_config_from_path(config_path: pathlib.Path) -> tuple[list[dict], dict]:
    if not config_path.exists():
        return [], {"use_udp": True, "no_tcp": False}
    options = load_forward_options(config_path)
    rules = parse_forward_rules_from_path(config_path, allow_empty=True)
    return rules, options


def save_forward_config_to_path(config_path: pathlib.Path, rules: list[dict], options: dict) -> None:
    content = render_forward_config(
        rules,
        use_udp=bool(options.get("use_udp", True)),
        no_tcp=bool(options.get("no_tcp", False)),
    )
    save_text(config_path, content)


def apply_config_change_transactionally(
    settings: dict,
    config_path: pathlib.Path,
    apply_func,
    mutate_func,
    action_label: str,
):
    previous_exists = config_path.exists()
    previous_content = config_path.read_text(encoding="utf-8") if previous_exists else ""
    previous_rules, previous_options = load_forward_config_from_path(config_path)
    installed_snapshot = copy.deepcopy(load_installed_records())

    rules = [dict(rule) for rule in previous_rules]
    options = dict(previous_options)
    new_rules, new_options, meta = mutate_func(rules, options)
    save_forward_config_to_path(config_path, new_rules, new_options)
    try:
        result = apply_func(settings, strict=True)
        return meta, result
    except Exception as exc:
        rollback_error = None
        try:
            save_forward_config_to_path(config_path, previous_rules, previous_options)
            save_installed_records(installed_snapshot)
            apply_func(settings, strict=True)
        except Exception as rollback_exc:
            rollback_error = rollback_exc
        finally:
            if previous_exists:
                save_text(config_path, previous_content)
            elif config_path.exists():
                config_path.unlink()
            save_installed_records(installed_snapshot)
        if rollback_error is not None:
            raise RuntimeError(f"{action_label} failed and rollback failed: {exc}; rollback: {rollback_error}") from exc
        raise


def add_forward_rule(listen_port: int, remote_host: str, remote_port: int) -> None:
    remote_host = normalize_remote_host(remote_host)
    rules, options = load_forward_config()
    for rule in rules:
        if rule["listen_port"] == listen_port:
            raise RuntimeError(f"listen port already exists: {listen_port}")
    rules.append(
        {
            "listen_port": int(listen_port),
            "remote_host": remote_host,
            "remote_port": int(remote_port),
        }
    )
    save_forward_config(rules, options)


def add_forward_rule_to_path(config_path: pathlib.Path, listen_port: int, remote_host: str, remote_port: int) -> None:
    remote_host = normalize_remote_host(remote_host)
    rules, options = load_forward_config_from_path(config_path)
    for rule in rules:
        if rule["listen_port"] == listen_port:
            raise RuntimeError(f"listen port already exists: {listen_port}")
    rules.append(
        {
            "listen_port": int(listen_port),
            "remote_host": remote_host,
            "remote_port": int(remote_port),
        }
    )
    save_forward_config_to_path(config_path, rules, options)


def add_named_forward_rule_to_path(config_path: pathlib.Path, listen_port: int, remote_host: str, remote_port: int) -> None:
    remote_host = normalize_remote_host(remote_host)
    rules, options = load_forward_config_from_path(config_path)
    for rule in rules:
        if rule["listen_port"] == listen_port:
            raise RuntimeError(f"listen port already exists: {listen_port}")
    rules.append(
        {
            "listen_port": int(listen_port),
            "remote_host": remote_host,
            "remote_port": int(remote_port),
        }
    )
    save_forward_config_to_path(config_path, rules, options)


def remove_forward_rule(listen_port: int) -> dict:
    rules, options = load_forward_config()
    kept = [rule for rule in rules if rule["listen_port"] != listen_port]
    if len(kept) == len(rules):
        raise RuntimeError(f"listen port not found: {listen_port}")
    removed = next(rule for rule in rules if rule["listen_port"] == listen_port)
    save_forward_config(kept, options)
    return removed


def remove_forward_rule_from_path(config_path: pathlib.Path, listen_port: int) -> dict:
    rules, options = load_forward_config_from_path(config_path)
    kept = [rule for rule in rules if rule["listen_port"] != listen_port]
    if len(kept) == len(rules):
        raise RuntimeError(f"listen port not found: {listen_port}")
    removed = next(rule for rule in rules if rule["listen_port"] == listen_port)
    save_forward_config_to_path(config_path, kept, options)
    return removed


def list_forward_rules() -> list[dict]:
    rules, _options = load_forward_config()
    return sorted(rules, key=lambda item: item["listen_port"])


def list_forward_rules_from_path(config_path: pathlib.Path) -> list[dict]:
    rules, _options = load_forward_config_from_path(config_path)
    return sorted(rules, key=lambda item: item["listen_port"])


def add_forward_rule_transactionally(
    settings: dict,
    config_path: pathlib.Path,
    apply_func,
    listen_port: int,
    remote_host: str,
    remote_port: int,
) -> tuple[dict, dict]:
    remote_host = normalize_remote_host(remote_host)

    def mutate_func(rules: list[dict], options: dict):
        for rule in rules:
            if rule["listen_port"] == listen_port:
                raise RuntimeError(f"listen port already exists: {listen_port}")
        added = {
            "listen_port": int(listen_port),
            "remote_host": remote_host,
            "remote_port": int(remote_port),
        }
        rules.append(added)
        return rules, options, added

    return apply_config_change_transactionally(
        settings,
        config_path,
        apply_func,
        mutate_func,
        f"add forward rule {listen_port}",
    )


def remove_forward_rule_transactionally(
    settings: dict,
    config_path: pathlib.Path,
    apply_func,
    listen_port: int,
) -> tuple[dict, dict]:
    def mutate_func(rules: list[dict], options: dict):
        kept = [rule for rule in rules if rule["listen_port"] != listen_port]
        if len(kept) == len(rules):
            raise RuntimeError(f"listen port not found: {listen_port}")
        removed = next(rule for rule in rules if rule["listen_port"] == listen_port)
        return kept, options, removed

    return apply_config_change_transactionally(
        settings,
        config_path,
        apply_func,
        mutate_func,
        f"remove forward rule {listen_port}",
    )


def update_forward_rule_transactionally(
    settings: dict,
    config_path: pathlib.Path,
    apply_func,
    old_listen_port: int,
    listen_port: int,
    remote_host: str,
    remote_port: int,
) -> tuple[dict, dict]:
    remote_host = normalize_remote_host(remote_host)
    old_listen_port = int(old_listen_port)
    listen_port = int(listen_port)
    remote_port = int(remote_port)

    def mutate_func(rules: list[dict], options: dict):
        matched_index = next((idx for idx, rule in enumerate(rules) if rule["listen_port"] == old_listen_port), None)
        if matched_index is None:
            raise RuntimeError(f"listen port not found: {old_listen_port}")
        for idx, rule in enumerate(rules):
            if idx != matched_index and rule["listen_port"] == listen_port:
                raise RuntimeError(f"listen port already exists: {listen_port}")
        previous = dict(rules[matched_index])
        updated = {
            "listen_port": listen_port,
            "remote_host": remote_host,
            "remote_port": remote_port,
        }
        rules[matched_index] = updated
        return rules, options, {"before": previous, "after": updated}

    return apply_config_change_transactionally(
        settings,
        config_path,
        apply_func,
        mutate_func,
        f"update forward rule {old_listen_port}",
    )


def apply_settings_change_transactionally(settings: dict, mutate_func, apply_func, action_label: str):
    previous_settings = copy.deepcopy(settings)
    previous_state = copy.deepcopy(load_runtime_state(previous_settings))
    installed_snapshot = copy.deepcopy(load_installed_records())
    new_settings, meta = mutate_func(copy.deepcopy(previous_settings))
    save_settings(new_settings)
    try:
        result = apply_func(new_settings, strict=True)
        return meta, result
    except Exception as exc:
        rollback_error = None
        try:
            save_settings(previous_settings)
            save_runtime_state(previous_state)
            save_installed_records(installed_snapshot)
            apply_func(previous_settings, strict=True)
        except Exception as rollback_exc:
            rollback_error = rollback_exc
        finally:
            save_settings(previous_settings)
            save_runtime_state(previous_state)
            save_installed_records(installed_snapshot)
        if rollback_error is not None:
            raise RuntimeError(f"{action_label} failed and rollback failed: {exc}; rollback: {rollback_error}") from exc
        raise


def save_frontend_domain_transactionally(settings: dict, original_record_name: str | None, domain_payload: dict) -> tuple[dict, dict]:
    requested_record_name = str(domain_payload.get("record_name", "")).strip()
    if not requested_record_name:
        raise RuntimeError("record_name is required")

    def mutate_func(settings_copy: dict):
        domains = copy.deepcopy(settings_copy.get("frontend_domains", {}))
        if original_record_name and original_record_name not in domains:
            raise RuntimeError(f"frontend domain not found: {original_record_name}")
        if original_record_name and original_record_name != requested_record_name:
            domains.pop(original_record_name, None)
        domain_payload_copy = copy.deepcopy(domain_payload)
        domain_payload_copy.pop("legacy_pool", None)
        domains[requested_record_name] = normalize_frontend_domain(requested_record_name, domain_payload_copy)
        settings_copy["frontend_domains"] = normalize_frontend_domains(domains)
        validate_frontend_domain_assignments(settings_copy, fetch_snapshot(settings_copy))
        return settings_copy, settings_copy["frontend_domains"][requested_record_name]

    return apply_settings_change_transactionally(
        settings,
        mutate_func,
        apply_frontend_forwards_now,
        f"save frontend domain {requested_record_name}",
    )


def delete_frontend_domain_transactionally(settings: dict, record_name: str) -> tuple[dict, dict]:
    record_name = str(record_name).strip()
    if not record_name:
        raise RuntimeError("record_name is required")

    def mutate_func(settings_copy: dict):
        domains = copy.deepcopy(settings_copy.get("frontend_domains", {}))
        removed = domains.pop(record_name, None)
        if removed is None:
            raise RuntimeError(f"frontend domain not found: {record_name}")
        if not domains:
            raise RuntimeError("at least one frontend domain must remain")
        settings_copy["frontend_domains"] = normalize_frontend_domains(domains)
        return settings_copy, removed

    return apply_settings_change_transactionally(
        settings,
        mutate_func,
        apply_frontend_forwards_now,
        f"delete frontend domain {record_name}",
    )


def add_frontend_domain_forward_rule_transactionally(
    settings: dict,
    record_name: str,
    listen_port: int,
    remote_host: str,
    remote_port: int,
) -> tuple[dict, dict]:
    remote_host = normalize_remote_host(remote_host)
    record_name = resolve_frontend_domain_name(settings, record_name)

    def mutate_func(settings_copy: dict):
        domains = copy.deepcopy(settings_copy.get("frontend_domains", {}))
        domain = copy.deepcopy(get_frontend_domain(settings_copy, record_name))
        rules = list(domain.get("forward_rules", []))
        if any(rule["listen_port"] == int(listen_port) for rule in rules):
            raise RuntimeError(f"listen port already exists in {record_name}: {listen_port}")
        added = {"listen_port": int(listen_port), "remote_host": remote_host, "remote_port": int(remote_port)}
        rules.append(added)
        domain["forward_rules"] = normalize_frontend_forward_rules(rules, record_name)
        domain.pop("legacy_pool", None)
        domains[record_name] = normalize_frontend_domain(record_name, domain)
        settings_copy["frontend_domains"] = normalize_frontend_domains(domains)
        validate_frontend_domain_assignments(settings_copy, fetch_snapshot(settings_copy))
        return settings_copy, added

    return apply_settings_change_transactionally(
        settings,
        mutate_func,
        apply_frontend_forwards_now,
        f"add frontend forward rule {listen_port} for {record_name}",
    )


def remove_frontend_domain_forward_rule_transactionally(
    settings: dict,
    record_name: str,
    listen_port: int,
) -> tuple[dict, dict]:
    record_name = resolve_frontend_domain_name(settings, record_name)

    def mutate_func(settings_copy: dict):
        domains = copy.deepcopy(settings_copy.get("frontend_domains", {}))
        domain = copy.deepcopy(get_frontend_domain(settings_copy, record_name))
        rules = list(domain.get("forward_rules", []))
        kept = [rule for rule in rules if rule["listen_port"] != int(listen_port)]
        if len(kept) == len(rules):
            raise RuntimeError(f"listen port not found in {record_name}: {listen_port}")
        removed = next(rule for rule in rules if rule["listen_port"] == int(listen_port))
        domain["forward_rules"] = normalize_frontend_forward_rules(kept, record_name)
        domain.pop("legacy_pool", None)
        domains[record_name] = normalize_frontend_domain(record_name, domain)
        settings_copy["frontend_domains"] = normalize_frontend_domains(domains)
        validate_frontend_domain_assignments(settings_copy, fetch_snapshot(settings_copy))
        return settings_copy, removed

    return apply_settings_change_transactionally(
        settings,
        mutate_func,
        apply_frontend_forwards_now,
        f"remove frontend forward rule {listen_port} for {record_name}",
    )


def update_frontend_domain_forward_rule_transactionally(
    settings: dict,
    record_name: str,
    old_listen_port: int,
    listen_port: int,
    remote_host: str,
    remote_port: int,
) -> tuple[dict, dict]:
    remote_host = normalize_remote_host(remote_host)
    record_name = resolve_frontend_domain_name(settings, record_name)

    def mutate_func(settings_copy: dict):
        domains = copy.deepcopy(settings_copy.get("frontend_domains", {}))
        domain = copy.deepcopy(get_frontend_domain(settings_copy, record_name))
        rules = list(domain.get("forward_rules", []))
        matched_index = next((idx for idx, rule in enumerate(rules) if rule["listen_port"] == int(old_listen_port)), None)
        if matched_index is None:
            raise RuntimeError(f"listen port not found in {record_name}: {old_listen_port}")
        for idx, rule in enumerate(rules):
            if idx != matched_index and rule["listen_port"] == int(listen_port):
                raise RuntimeError(f"listen port already exists in {record_name}: {listen_port}")
        previous = dict(rules[matched_index])
        updated = {"listen_port": int(listen_port), "remote_host": remote_host, "remote_port": int(remote_port)}
        rules[matched_index] = updated
        domain["forward_rules"] = normalize_frontend_forward_rules(rules, record_name)
        domain.pop("legacy_pool", None)
        domains[record_name] = normalize_frontend_domain(record_name, domain)
        settings_copy["frontend_domains"] = normalize_frontend_domains(domains)
        validate_frontend_domain_assignments(settings_copy, fetch_snapshot(settings_copy))
        return settings_copy, {"before": previous, "after": updated}

    return apply_settings_change_transactionally(
        settings,
        mutate_func,
        apply_frontend_forwards_now,
        f"update frontend forward rule {old_listen_port} for {record_name}",
    )


def resolve_remote_host(host: str) -> str:
    try:
        ip = ipaddress.ip_address(host)
        if ip.version != 4:
            raise RuntimeError(f"only IPv4 remote targets are supported: {host}")
        return str(ip)
    except ValueError:
        pass

    try:
        return socket.gethostbyname(host)
    except OSError as e:
        raise RuntimeError(f"failed to resolve remote host {host}: {e}") from e


def resolve_forward_rules(rules: list[dict]) -> list[dict]:
    resolved = []
    for rule in rules:
        resolved.append(
            {
                **rule,
                "resolved_remote_host": resolve_remote_host(rule["remote_host"]),
            }
        )
    return resolved


def chunk_ports(ports: list[str], size: int = 15) -> list[list[str]]:
    return [ports[idx : idx + size] for idx in range(0, len(ports), size)]


def build_iptables_rules_text(rules: list[dict]) -> str:
    resolved_rules = resolve_forward_rules(rules)
    tcp_ports = [str(rule["listen_port"]) for rule in resolved_rules]
    udp_ports = list(tcp_ports)
    remote_hosts = sorted({rule["resolved_remote_host"] for rule in resolved_rules})

    lines = [
        "*nat",
        ":PREROUTING ACCEPT [0:0]",
        ":INPUT ACCEPT [0:0]",
        ":OUTPUT ACCEPT [0:0]",
        ":POSTROUTING ACCEPT [0:0]",
    ]
    for rule in resolved_rules:
        lp = rule["listen_port"]
        rh = rule["resolved_remote_host"]
        rp = rule["remote_port"]
        lines.append(f"-A PREROUTING -p tcp --dport {lp} -j DNAT --to-destination {rh}:{rp}")
        lines.append(f"-A PREROUTING -p udp --dport {lp} -j DNAT --to-destination {rh}:{rp}")
    for host in remote_hosts:
        lines.append(f"-A POSTROUTING -p tcp -d {host} -j MASQUERADE")
        lines.append(f"-A POSTROUTING -p udp -d {host} -j MASQUERADE")
    lines.append("COMMIT")
    lines.extend(
        [
            "*filter",
            ":INPUT ACCEPT [0:0]",
            ":FORWARD ACCEPT [0:0]",
            ":OUTPUT ACCEPT [0:0]",
        ]
    )
    for port_chunk in chunk_ports(tcp_ports):
        joined = ",".join(port_chunk)
        lines.append(
            f"-A FORWARD -p tcp -m multiport --dports {joined} -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT"
        )
        lines.append(
            f"-A FORWARD -p tcp -m multiport --sports {joined} -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
        )
    for port_chunk in chunk_ports(udp_ports):
        joined = ",".join(port_chunk)
        lines.append(f"-A FORWARD -p udp -m multiport --dports {joined} -j ACCEPT")
        lines.append(f"-A FORWARD -p udp -m multiport --sports {joined} -j ACCEPT")
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


def build_jump_ssh_command(settings: dict, target_host: str) -> str:
    ssh = settings["ssh"]
    user = ssh.get("user", "root")
    port = str(ssh.get("port", 22))
    timeout = str(ssh.get("connect_timeout", 10))
    parts = [
        "ssh",
        "-p",
        port,
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={timeout}",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=3",
    ]
    if ssh.get("password"):
        parts.extend(
            [
                "-o",
                "PreferredAuthentications=password",
                "-o",
                "PubkeyAuthentication=no",
            ]
        )
    else:
        key = os.path.expanduser(ssh.get("private_key", "~/.ssh/id_rsa"))
        parts.extend(["-i", key])
    parts.append(f"{user}@{target_host}")
    parts.extend(["bash", "-s"])
    if ssh.get("password"):
        return " ".join(shlex.quote(part) for part in ["sshpass", "-p", ssh["password"], *parts])
    return " ".join(shlex.quote(part) for part in parts)


def build_download_url(url: str, prefix: str | None) -> str:
    if not prefix:
        return url
    clean = str(prefix).strip().rstrip("/")
    return f"{clean}/{url}"


def realm_install_script(remote_config_path: str, github_proxy_prefix: str | None = None) -> str:
    quoted_cfg = shlex.quote(remote_config_path)
    base_url = "https://github.com/zhboner/realm/releases/latest/download/realm-${ASSET}.tar.gz"
    download_url = build_download_url(base_url, github_proxy_prefix)
    return f"""#!/usr/bin/env bash
set -euo pipefail

if ! command -v curl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl tar
  elif command -v yum >/dev/null 2>&1; then
    yum install -y curl tar
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y curl tar
  else
    echo "No supported package manager found" >&2
    exit 1
  fi
fi

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ASSET="x86_64-unknown-linux-gnu" ;;
  aarch64|arm64) ASSET="aarch64-unknown-linux-gnu" ;;
  *)
    echo "Unsupported architecture: $ARCH" >&2
    exit 1
    ;;
esac

TMP_DIR="$(mktemp -d)"
cleanup() {{ rm -rf "$TMP_DIR"; }}
trap cleanup EXIT
cd "$TMP_DIR"

URL="{download_url}"
curl -fL "$URL" -o realm.tar.gz
tar -xzf realm.tar.gz
install -m 0755 realm /usr/local/bin/realm
REALM_BIN="/usr/local/bin/realm"

mkdir -p "$(dirname {quoted_cfg})"
install -m 0644 /tmp/realm-config.toml {quoted_cfg}

cat >/etc/systemd/system/realm.service <<EOF
[Unit]
Description=realm traffic forwarder
After=network.target

[Service]
Type=simple
ExecStart=$REALM_BIN -c {remote_config_path}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable realm
systemctl restart realm
systemctl --no-pager --full status realm || true
"""


def render_tcp_pool_config(rules: list[dict], section_prefix: str) -> str:
    lines = []
    for idx, rule in enumerate(sorted(rules, key=lambda item: item["listen_port"]), start=1):
        tag = f"{section_prefix}_{idx}_{rule['listen_port']}"
        local_ip = "::" if ":" in str(rule.get("listen_host", "0.0.0.0")) else "0.0.0.0"
        lines.extend(
            [
                f"[{tag}]",
                f"LOCAL_IP={local_ip}",
                f"LOCAL_PORT={rule['listen_port']}",
                f"REMOTE_IP={rule['remote_host']}",
                f"REMOTE_TCP_PORT={rule['remote_port']}",
                f"REMOTE_UDP_PORT={rule['remote_port']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def tcp_pool_install_script() -> str:
    return r"""#!/usr/bin/env bash
set -euo pipefail

SRC_C="/root/tcp_pool.c"
BIN="/root/tcp_pool"
CFG_DIR="/etc/tcp_pool"
CFG_FILE="/etc/tcp_pool/relays.conf"
SERVICE_FILE="/etc/systemd/system/tcp-pool@.service"
PARSE_BIN="/usr/local/bin/tcp-pool-parse"
START_BIN="/usr/local/bin/tcp-pool-start"

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

if ! need_cmd gcc || ! need_cmd curl; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl build-essential
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y curl gcc make
  elif command -v yum >/dev/null 2>&1; then
    yum install -y curl gcc make
  else
    echo "No supported package manager found for installing tcp_pool dependencies" >&2
    exit 1
  fi
fi

curl -fsSL https://raw.githubusercontent.com/Xeloan/TCP-preconnection-relay/main/tcp_pool.c -o "$SRC_C"
gcc -O2 -pthread -march=native -o "$BIN" "$SRC_C"

mkdir -p "$CFG_DIR"
install -m 0600 /tmp/relays.conf "$CFG_FILE"

cat > "$PARSE_BIN" <<'EOF'
#!/bin/bash
set -euo pipefail

SRC="/etc/tcp_pool/relays.conf"
DST="/etc/tcp_pool"

[ -f "$SRC" ] || { echo "缺少 $SRC"; exit 1; }

mkdir -p "$DST"
find "$DST" -maxdepth 1 -type f -name '*.conf' ! -name 'relays.conf' -delete

current=""
declare -A section_seen
declare -A kv

trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

reset_kv() {
    kv=([LOCAL_IP]="" [LOCAL_PORT]="" [REMOTE_IP]="" [REMOTE_TCP_PORT]="" [REMOTE_UDP_PORT]="")
}

is_valid_port() {
    local p="$1"
    [[ "$p" =~ ^[0-9]+$ ]] || return 1
    (( p >= 1 && p <= 65535 )) || return 1
    return 0
}

validate_and_write_section() {
    [[ -n "$current" ]] || return 0
    local key
    for key in LOCAL_IP LOCAL_PORT REMOTE_IP REMOTE_TCP_PORT REMOTE_UDP_PORT; do
        if [[ -z "${kv[$key]}" ]]; then
            echo "[$current] 缺少: $key" >&2
            exit 1
        fi
    done
    is_valid_port "${kv[LOCAL_PORT]}" || { echo "[$current] 不合法 LOCAL_PORT: ${kv[LOCAL_PORT]}" >&2; exit 1; }
    is_valid_port "${kv[REMOTE_TCP_PORT]}" || { echo "[$current] 不合法 REMOTE_TCP_PORT: ${kv[REMOTE_TCP_PORT]}" >&2; exit 1; }
    is_valid_port "${kv[REMOTE_UDP_PORT]}" || { echo "[$current] 不合法 REMOTE_UDP_PORT: ${kv[REMOTE_UDP_PORT]}" >&2; exit 1; }

    local outfile="$DST/$current.conf"
    : > "$outfile"
    chmod 600 "$outfile"
    {
        printf 'LOCAL_IP=%s\n' "${kv[LOCAL_IP]}"
        printf 'LOCAL_PORT=%s\n' "${kv[LOCAL_PORT]}"
        printf 'REMOTE_IP=%s\n' "${kv[REMOTE_IP]}"
        printf 'REMOTE_TCP_PORT=%s\n' "${kv[REMOTE_TCP_PORT]}"
        printf 'REMOTE_UDP_PORT=%s\n' "${kv[REMOTE_UDP_PORT]}"
    } > "$outfile"
}

reset_kv

while IFS= read -r raw || [ -n "$raw" ]; do
    line="${raw%$'\r'}"
    line="$(trim "$line")"
    [[ -z "$line" ]] && continue
    [[ "$line" == \#* ]] && continue
    [[ "$line" == \;* ]] && continue

    if [[ "$line" =~ ^\[([A-Za-z0-9_-]+)\]$ ]]; then
        next_section="${BASH_REMATCH[1]}"
        validate_and_write_section
        current="$next_section"
        if [[ -n "${section_seen[$current]:-}" ]]; then
            echo "你标签写重复了: [$current]" >&2
            exit 1
        fi
        section_seen["$current"]=1
        reset_kv
        continue
    fi

    if [[ -z "$current" ]]; then
        echo "你漏写标签了: $line" >&2
        exit 1
    fi

    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
        key="${BASH_REMATCH[1]}"
        val="$(trim "${BASH_REMATCH[2]}")"
    else
        echo "标签不合法，参考python变量名 [$current]: $line" >&2
        exit 1
    fi

    case "$key" in
        LOCAL_IP|LOCAL_PORT|REMOTE_IP|REMOTE_TCP_PORT|REMOTE_UDP_PORT)
            kv["$key"]="$val"
            ;;
        *)
            echo "[$current] 不支持的配置: $key" >&2
            exit 1
            ;;
    esac
done < "$SRC"

validate_and_write_section
EOF

chmod +x "$PARSE_BIN"

cat > "$SERVICE_FILE" <<'EOF'
[Unit]
Description=High Performance TCP Connection Pool
Wants=network-online.target
After=network-online.target

[Service]
ExecStart=/root/tcp_pool
EnvironmentFile=/etc/tcp_pool/%i.conf
Nice=-10
LimitNOFILE=65535
Restart=always
RestartSec=3
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > "$START_BIN" <<'EOF'
#!/bin/bash
set -euo pipefail
/usr/local/bin/tcp-pool-parse

mapfile -t old_units < <(
    {
        systemctl list-units --full --all --no-legend 'tcp-pool@*.service' 2>/dev/null | awk '{print $1}'
        systemctl list-unit-files --full --no-legend 'tcp-pool@*.service' 2>/dev/null | awk '{print $1}'
    } | sort -u
)

for unit in "${old_units[@]}"; do
    [ -n "$unit" ] || continue
    systemctl stop "$unit" 2>/dev/null || true
    systemctl disable "$unit" 2>/dev/null || true
done

shopt -s nullglob
confs=(/etc/tcp_pool/*.conf)
instances=()
for conf in "${confs[@]}"; do
    name="$(basename "$conf")"
    [[ "$name" == "relays.conf" ]] && continue
    [[ "$name" != *.conf ]] && continue
    instances+=("${name%.conf}")
done

if [ "${#instances[@]}" -eq 0 ]; then
    echo "没有可启动的转发实例，请检查 /etc/tcp_pool/relays.conf"
    exit 1
fi

for inst in "${instances[@]}"; do
    systemctl enable "tcp-pool@$inst"
    systemctl restart "tcp-pool@$inst"
done
EOF

chmod +x "$START_BIN"

systemctl daemon-reload
/usr/local/bin/tcp-pool-start
systemctl --no-pager --full list-units 'tcp-pool@*.service' || true
"""


def iptables_install_script() -> str:
    return r"""#!/usr/bin/env bash
set -euo pipefail

RULES_FILE="/etc/ali-forward.rules"
SERVICE_FILE="/etc/systemd/system/ali-forward.service"
UNINSTALL_FILE="/usr/local/bin/uninstall-realm"

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

if ! need_cmd iptables-restore || ! need_cmd iptables-save; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y iptables
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y iptables iptables-services
  elif command -v yum >/dev/null 2>&1; then
    yum install -y iptables iptables-services
  else
    echo "No supported package manager found for installing iptables" >&2
    exit 1
  fi
fi

sysctl -w net.ipv4.ip_forward=1 >/dev/null
mkdir -p /etc/sysctl.d
cat >/etc/sysctl.d/99-ali-forward.conf <<'EOF'
net.ipv4.ip_forward=1
EOF

install -m 0600 /tmp/ali-forward.rules "$RULES_FILE"
iptables-restore < "$RULES_FILE"

cat > "$SERVICE_FILE" <<'EOF'
[Unit]
Description=AliMonitor iptables forward rules
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c '/sbin/iptables-restore < /etc/ali-forward.rules || /usr/sbin/iptables-restore < /etc/ali-forward.rules'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

cat > "$UNINSTALL_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

KEEP_FILES=0
if [ "${1:-}" = "--keep-files" ]; then
  KEEP_FILES=1
fi

RULES_FILE="/etc/ali-forward.rules"
SERVICE_FILE="/etc/systemd/system/ali-forward.service"

if [ -f "$RULES_FILE" ] && command -v iptables-restore >/dev/null 2>&1; then
  cat <<'EMPTY' | iptables-restore
*nat
:PREROUTING ACCEPT [0:0]
:INPUT ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
COMMIT
*filter
:INPUT ACCEPT [0:0]
:FORWARD ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
COMMIT
EMPTY
fi

sysctl -w net.ipv4.ip_forward=0 >/dev/null || true
rm -f /etc/sysctl.d/99-ali-forward.conf

if [ "$KEEP_FILES" -eq 0 ]; then
  systemctl disable --now ali-forward.service >/dev/null 2>&1 || true
  rm -f "$RULES_FILE" "$SERVICE_FILE"
  systemctl daemon-reload || true
else
  systemctl daemon-reload || true
fi

rm -f /usr/local/bin/realm /etc/systemd/system/realm.service /etc/realm/config.toml
systemctl disable --now realm >/dev/null 2>&1 || true
systemctl daemon-reload || true
EOF

chmod +x "$UNINSTALL_FILE"

systemctl daemon-reload
systemctl enable --now ali-forward.service
systemctl --no-pager --full status ali-forward.service || true
"""


def ensure_jump_host_tools_script(settings: dict) -> str:
    if not settings.get("ssh", {}).get("password"):
        return ""
    return r"""
if ! command -v sshpass >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y sshpass
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y sshpass
  elif command -v yum >/dev/null 2>&1; then
    yum install -y epel-release || true
    yum install -y sshpass
  else
    echo "No supported package manager found for installing sshpass on jump host" >&2
    exit 1
  fi
fi
"""


def forwarding_jump_script(settings: dict, target_host: str, rules_text: str) -> str:
    nested_cmd = build_jump_ssh_command(settings, target_host)
    jump_prep = ensure_jump_host_tools_script(settings)
    return f"""#!/usr/bin/env bash
set -euo pipefail
{jump_prep}
{nested_cmd} <<'ALI_TARGET_SCRIPT'
cat >/tmp/ali-forward.rules <<'ALI_RULES'
{rules_text}ALI_RULES
{iptables_install_script()}
ALI_TARGET_SCRIPT
"""


def realm_jump_script(settings: dict, target_host: str, config_text: str) -> str:
    nested_cmd = build_jump_ssh_command(settings, target_host)
    jump_prep = ensure_jump_host_tools_script(settings)
    remote_cfg = settings.get("ssh", {}).get("remote_config_path", "/etc/realm/config.toml")
    github_proxy_prefix = settings.get("github_proxy_prefix", "")
    return f"""#!/usr/bin/env bash
set -euo pipefail
{jump_prep}
{nested_cmd} <<'ALI_TARGET_SCRIPT'
cat >/tmp/realm-config.toml <<'ALI_REALM_CFG'
{config_text}ALI_REALM_CFG
{realm_install_script(remote_cfg, github_proxy_prefix)}
ALI_TARGET_SCRIPT
"""


def legacy_cleanup_script() -> str:
    return r"""#!/usr/bin/env bash
set -euo pipefail

systemctl disable --now ali-forward.service >/dev/null 2>&1 || true
systemctl disable --now realm.service >/dev/null 2>&1 || true
systemctl disable --now realm >/dev/null 2>&1 || true

if command -v iptables-restore >/dev/null 2>&1; then
cat <<'EOF' | iptables-restore
*nat
:PREROUTING ACCEPT [0:0]
:INPUT ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
COMMIT
*filter
:INPUT ACCEPT [0:0]
:FORWARD ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
COMMIT
EOF
fi

rm -f /etc/ali-forward.rules
rm -f /etc/systemd/system/ali-forward.service
rm -f /etc/sysctl.d/99-ali-forward.conf

rm -f /usr/local/bin/realm
rm -f /etc/systemd/system/realm.service
rm -rf /etc/realm

sysctl -w net.ipv4.ip_forward=0 >/dev/null || true
systemctl daemon-reload || true
"""


def provision_forwarding(settings: dict, host: str, config_path: pathlib.Path = REALM_CONFIG_PATH) -> None:
    if not config_path.exists():
        raise RuntimeError(f"missing forwarding config: {config_path}")
    rules = parse_forward_rules_from_path(config_path, allow_empty=True)
    provision_forwarding_rules(settings, host, rules, source_label=config_path.name)


def provision_forwarding_rules(settings: dict, host: str, rules: list[dict], source_label: str = "frontend domain") -> None:
    rules_text = build_iptables_rules_text(rules)
    rules_tmp = BASE_DIR / ".ali-forward.rules.tmp"
    try:
        rules_tmp.write_text(rules_text, encoding="utf-8")
        print(f"[+] provisioning iptables forwarding on {host} from {source_label}")
        scp_upload(settings, host, rules_tmp, "/tmp/ali-forward.rules")
        print(f"[*] installing forwarding rules on {host}, streaming progress...")
        ssh_run_stream(settings, host, iptables_install_script(), timeout_sec=900)
        print(f"[+] iptables forwarding ready on {host}")
    finally:
        if rules_tmp.exists():
            rules_tmp.unlink()


def provision_forwarding_via_jump(
    settings: dict,
    jump_host: str,
    target_host: str,
    config_path: pathlib.Path,
) -> None:
    config_text = config_path.read_text(encoding="utf-8")
    print(
        f"[+] provisioning realm on IEPL target {target_host} via jump host {jump_host} from {config_path.name}"
    )
    ssh_run_stream(settings, jump_host, realm_jump_script(settings, target_host, config_text), timeout_sec=900)
    print(f"[+] realm ready on IEPL target {target_host}")


def cleanup_legacy_forwarding(settings: dict, host: str) -> None:
    print(f"[+] cleaning legacy forwarding on {host}")
    ssh_run_stream(settings, host, legacy_cleanup_script(), timeout_sec=300)
    print(f"[+] legacy forwarding cleaned on {host}")


def cleanup_legacy_forwarding_via_jump(settings: dict, jump_host: str, target_host: str) -> None:
    print(f"[+] cleaning legacy forwarding on IEPL target {target_host} via jump host {jump_host}")
    nested_cmd = build_jump_ssh_command(settings, target_host)
    jump_prep = ensure_jump_host_tools_script(settings)
    script = f"""#!/usr/bin/env bash
set -euo pipefail
{jump_prep}
{nested_cmd} <<'ALI_TARGET_SCRIPT'
{legacy_cleanup_script()}
ALI_TARGET_SCRIPT
"""
    ssh_run_stream(settings, jump_host, script, timeout_sec=300)
    print(f"[+] legacy forwarding cleaned on IEPL target {target_host}")


def realm_restart_script() -> str:
    return r"""#!/usr/bin/env bash
set -euo pipefail

systemctl restart realm
systemctl --no-pager --full status realm || true
"""


def restart_realm_via_jump(settings: dict, jump_host: str, target_host: str) -> None:
    print(f"[+] restarting realm on IEPL target {target_host} via jump host {jump_host}")
    nested_cmd = build_jump_ssh_command(settings, target_host)
    jump_prep = ensure_jump_host_tools_script(settings)
    script = f"""#!/usr/bin/env bash
set -euo pipefail
{jump_prep}
{nested_cmd} <<'ALI_TARGET_SCRIPT'
{realm_restart_script()}
ALI_TARGET_SCRIPT
"""
    ssh_run_stream(settings, jump_host, script, timeout_sec=300)
    print(f"[+] realm restarted on IEPL target {target_host}")


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_valid_ipv4(value: str | None) -> bool:
    if not value:
        return False
    try:
        return ipaddress.ip_address(value).version == 4
    except ValueError:
        return False


def calc_traffic_bytes(status: dict) -> int:
    return int(max(float(status.get("net_total_up", 0) or 0), float(status.get("net_total_down", 0) or 0)))


def over_limit_detail(
    status: dict,
    traffic_limit_bytes: int = TRAFFIC_LIMIT_BYTES,
    uptime_limit_days: int = UPTIME_LIMIT_DAYS,
) -> tuple[bool, str]:
    uptime_sec = float(status.get("uptime", 0) or 0)
    traffic_bytes = calc_traffic_bytes(status)
    uptime_days = uptime_sec / 86400.0
    traffic_gb = traffic_bytes / (1024.0 * 1024.0 * 1024.0)
    traffic_limit_gb = traffic_limit_bytes / BYTES_PER_GB
    uptime_hit = uptime_days > uptime_limit_days
    traffic_hit = traffic_bytes > traffic_limit_bytes
    detail = (
        f"uptime={uptime_days:.2f}d (limit {uptime_limit_days}d), "
        f"max_net_total={traffic_gb:.2f}GB (limit {traffic_limit_gb:.0f}GB)"
    )
    return (uptime_hit or traffic_hit), detail


def over_limit_detail_for_node(settings: dict, item: dict) -> tuple[bool, str]:
    traffic_limit_gb, _source = get_frontend_node_traffic_limit_info(settings, item)
    return over_limit_detail(
        item["status"],
        traffic_limit_bytes=traffic_limit_gb_to_bytes(traffic_limit_gb),
        uptime_limit_days=UPTIME_LIMIT_DAYS,
    )


def make_fingerprint(node: dict) -> dict:
    return {
        "uuid": node.get("uuid", ""),
        "name": node.get("name", ""),
        "cpu_name": node.get("cpu_name", ""),
        "os": node.get("os", ""),
        "arch": node.get("arch", ""),
        "created_at": node.get("created_at", ""),
    }


def node_matches_candidate(settings: dict, node: dict) -> bool:
    komari = settings["komari"]
    group_name = komari.get("group_name", "IX")
    keywords = komari.get("exclude_name_keywords", ["IEPL"])
    if node.get("group") != group_name:
        return False
    name = (node.get("name") or "").lower()
    return not any(keyword.lower() in name for keyword in keywords)


def node_matches_iepl(settings: dict, node: dict) -> bool:
    komari = settings["komari"]
    group_name = komari.get("group_name", "IX")
    keywords = komari.get("exclude_name_keywords", ["IEPL"])
    if node.get("group") != group_name:
        return False
    name = (node.get("name") or "").lower()
    return any(keyword.lower() in name for keyword in keywords)


def parse_tags(tags: str | None) -> list[str]:
    if not tags:
        return []
    return [item.strip() for item in str(tags).split(";") if item.strip()]


def load_tag_cache() -> dict[str, str]:
    data = load_json(TAG_CACHE_PATH, {})
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    return {}


def save_tag_cache(data: dict[str, str]) -> None:
    save_json(TAG_CACHE_PATH, data)


def build_role_tags(existing_tags: str | None, role_tag: str) -> str:
    tags = [tag for tag in parse_tags(existing_tags) if tag not in {"使用中", "备用"}]
    tags.append(role_tag)
    return ";".join(dict.fromkeys(tags))


def sync_role_tags(settings: dict, snapshot: dict, primary_uuid: str) -> None:
    komari_cfg = settings["komari"]
    client = KomariAdminClient(
        komari_cfg["base_url"],
        komari_cfg["api_key"],
        timeout_sec=int(komari_cfg.get("request_timeout", 20)),
    )
    tag_cache = load_tag_cache()
    cache_changed = False

    for item in snapshot["nodes"]:
        role_tag = "使用中" if item["uuid"] == primary_uuid else "备用"
        current_tags = item["node"].get("tags")
        if current_tags is None or (current_tags == "" and item["uuid"] in tag_cache):
            current_tags = tag_cache.get(item["uuid"], "")
        desired_tags = build_role_tags(current_tags, role_tag)
        if desired_tags == (current_tags or ""):
            tag_cache[item["uuid"]] = desired_tags
            cache_changed = True
            continue
        client.edit_client(item["uuid"], {"tags": desired_tags})
        item["node"]["tags"] = desired_tags
        tag_cache[item["uuid"]] = desired_tags
        cache_changed = True
        print(f"[*] updated Komari tags for {item['name']}: {desired_tags}")
    if cache_changed:
        save_tag_cache(tag_cache)


def fetch_snapshot(settings: dict) -> dict:
    komari_cfg = settings["komari"]
    client = KomariRpcClient(
        komari_cfg["base_url"],
        komari_cfg["api_key"],
        timeout_sec=int(komari_cfg.get("request_timeout", 20)),
    )
    all_nodes = client.get_nodes()
    candidate_nodes = {uuid: node for uuid, node in all_nodes.items() if node_matches_candidate(settings, node)}
    statuses = client.get_latest_statuses(list(candidate_nodes.keys()))
    stale_after_sec = int(komari_cfg.get("stale_after_sec", 180))
    now_dt = dt.datetime.now(dt.timezone.utc)
    merged = []
    for uuid, node in candidate_nodes.items():
        status = statuses.get(uuid, {}) or {}
        status_time = parse_time(status.get("time"))
        age_sec = None
        stale = True
        if status_time is not None:
            age_sec = max(0.0, (now_dt - status_time).total_seconds())
            stale = age_sec > stale_after_sec
        ipv4 = node.get("ipv4") or ""
        merged.append(
            {
                "uuid": uuid,
                "node": node,
                "status": status,
                "name": node.get("name") or uuid,
                "ipv4": ipv4 if is_valid_ipv4(ipv4) else "",
                "online": bool(status.get("online", False)),
                "stale": stale,
                "status_age_sec": age_sec,
                "traffic_bytes": calc_traffic_bytes(status),
                "fingerprint": make_fingerprint(node),
            }
        )
    return {"fetched_at": now_iso(), "nodes": merged}


def fetch_iepl_snapshot(settings: dict) -> dict:
    komari_cfg = settings["komari"]
    client = KomariRpcClient(
        komari_cfg["base_url"],
        komari_cfg["api_key"],
        timeout_sec=int(komari_cfg.get("request_timeout", 20)),
    )
    all_nodes = client.get_nodes()
    iepl_nodes = {uuid: node for uuid, node in all_nodes.items() if node_matches_iepl(settings, node)}
    statuses = client.get_latest_statuses(list(iepl_nodes.keys()))
    stale_after_sec = int(komari_cfg.get("stale_after_sec", 180))
    now_dt = dt.datetime.now(dt.timezone.utc)
    merged = []
    for uuid, node in iepl_nodes.items():
        status = statuses.get(uuid, {}) or {}
        status_time = parse_time(status.get("time"))
        age_sec = None
        stale = True
        if status_time is not None:
            age_sec = max(0.0, (now_dt - status_time).total_seconds())
            stale = age_sec > stale_after_sec
        ipv4 = node.get("ipv4") or ""
        merged.append(
            {
                "uuid": uuid,
                "node": node,
                "status": status,
                "name": node.get("name") or uuid,
                "ipv4": ipv4 if is_valid_ipv4(ipv4) else "",
                "online": bool(status.get("online", False)),
                "stale": stale,
                "status_age_sec": age_sec,
                "traffic_bytes": calc_traffic_bytes(status),
                "fingerprint": make_fingerprint(node),
            }
        )
    return {"fetched_at": now_iso(), "nodes": merged}


def choose_jump_host_item(snapshot: dict, preferred_uuid: str | None = None) -> dict | None:
    if preferred_uuid:
        preferred = next((item for item in snapshot["nodes"] if item["uuid"] == preferred_uuid), None)
        if preferred and fallback_eligible(preferred):
            return preferred
    fallback = [item for item in snapshot["nodes"] if fallback_eligible(item)]
    if not fallback:
        return None
    return sorted(fallback, key=candidate_sort_key)[0]


def resolve_iepl_target_ip(settings: dict, item: dict) -> str:
    mapping = settings.get("iepl_target_ips", {}) or {}
    raw = mapping.get(item["uuid"])
    if raw is None:
        raw = mapping.get(item["name"])
    if not raw:
        raise RuntimeError(f"missing iepl target ip for {item['name']} ({item['uuid']}) in settings.json")
    ip = ipaddress.ip_address(str(raw))
    if ip.version != 4:
        raise RuntimeError(f"iepl target ip must be IPv4 for {item['name']} ({item['uuid']})")
    return str(ip)


def candidate_sort_key(item: dict) -> tuple:
    return (
        -float(item["status"].get("uptime", 0) or 0),
        -float(item.get("traffic_bytes", 0) or 0),
        item["name"],
        item["uuid"],
    )


def eligible_for_promotion(settings: dict, item: dict) -> bool:
    if not item.get("online"):
        return False
    if item.get("stale"):
        return False
    if not item.get("ipv4"):
        return False
    hit, _ = over_limit_detail_for_node(settings, item)
    return not hit


def fallback_eligible(item: dict) -> bool:
    return bool(item.get("online") and not item.get("stale") and item.get("ipv4"))


def choose_best(settings: dict, nodes: list[dict], require_healthy: bool = True) -> dict | None:
    filtered = [item for item in nodes if eligible_for_promotion(settings, item)]
    if filtered:
        return sorted(filtered, key=candidate_sort_key)[0]
    if require_healthy:
        return None
    fallback = [item for item in nodes if fallback_eligible(item)]
    if not fallback:
        return None
    return sorted(fallback, key=candidate_sort_key)[0]


def select_frontend_domain_initial_primary(settings: dict, snapshot: dict, record_name: str) -> dict:
    ordered_items = frontend_domain_member_items(settings, snapshot, record_name)
    for item in ordered_items:
        if eligible_for_promotion(settings, item):
            return item
    for item in ordered_items:
        if fallback_eligible(item):
            return item
    raise RuntimeError(f"frontend domain {record_name} has no candidate with fresh status and IPv4 available")


def split_frontend_domain_primary_and_backups(
    settings: dict,
    snapshot: dict,
    record_name: str,
    current_primary_uuid: str | None,
) -> tuple[dict | None, list[dict]]:
    items = frontend_domain_member_items(settings, snapshot, record_name)
    primary = next((item for item in items if item["uuid"] == current_primary_uuid), None)
    backups = [item for item in items if item["uuid"] != current_primary_uuid]
    return primary, backups


def choose_frontend_domain_failover_backup(
    settings: dict,
    snapshot: dict,
    record_name: str,
    current_primary_uuid: str | None,
) -> dict | None:
    for item in frontend_domain_member_items(settings, snapshot, record_name):
        if item["uuid"] == current_primary_uuid:
            continue
        if eligible_for_promotion(settings, item):
            return item
    return None


def load_installed_records() -> dict[str, dict]:
    data = load_json(FORWARD_INSTALLED_PATH, {})
    if isinstance(data, dict):
        return data
    return {}


def save_installed_records(data: dict[str, dict]) -> None:
    save_json(FORWARD_INSTALLED_PATH, data)


def make_forward_apply_result(profile: str, config_ref, total_nodes: int) -> dict:
    config_file = config_ref.name if isinstance(config_ref, pathlib.Path) else str(config_ref)
    return {
        "profile": profile,
        "config_file": config_file,
        "total_nodes": int(total_nodes),
        "applied_nodes": 0,
        "skipped_nodes": 0,
        "failed_nodes": 0,
        "errors": [],
    }


def merge_forward_apply_result(target: dict, source: dict) -> dict:
    target["total_nodes"] += int(source.get("total_nodes", 0))
    target["applied_nodes"] += int(source.get("applied_nodes", 0))
    target["skipped_nodes"] += int(source.get("skipped_nodes", 0))
    target["failed_nodes"] += int(source.get("failed_nodes", 0))
    target.setdefault("errors", []).extend(source.get("errors", []))
    return target


def format_forward_apply_summary(result: dict) -> str:
    return (
        f"updated={result['applied_nodes']}, "
        f"unchanged={result['skipped_nodes']}, "
        f"failed={result['failed_nodes']}, "
        f"targets={result['total_nodes']}"
    )


def raise_for_failed_forward_apply(result: dict, label: str) -> None:
    if int(result.get("failed_nodes", 0)) <= 0:
        return
    errors = result.get("errors", [])
    details = "; ".join(
        f"{item.get('name') or item.get('uuid') or '-'} ({item.get('target_ip') or item.get('ipv4') or '-'})"
        f": {item.get('error') or 'unknown error'}"
        for item in errors[:3]
    )
    if len(errors) > 3:
        details = f"{details}; +{len(errors) - 3} more"
    raise RuntimeError(f"{label} not fully applied ({format_forward_apply_summary(result)}): {details}")


def ensure_named_forwards(
    settings: dict,
    nodes: list[dict],
    profile: str,
    config_path: pathlib.Path,
    jump_host: str | None = None,
) -> dict:
    installed = load_installed_records()
    changed = False
    config_hash = file_sha256(config_path)
    desired_backend = "realm" if profile == "iepl" else "iptables"
    result = make_forward_apply_result(profile, config_path, len(nodes))
    for item in nodes:
        uuid = item["uuid"]
        record = installed.get(uuid)
        target_ip = ""
        try:
            if profile == "iepl":
                if not jump_host:
                    raise RuntimeError("no jump host available for IEPL provisioning")
                target_ip = resolve_iepl_target_ip(settings, item)
            else:
                target_ip = item.get("ipv4")
                if not target_ip:
                    raise RuntimeError("missing IPv4")

            needs_install = (
                record is None
                or record.get("fingerprint") != item["fingerprint"]
                or record.get("profile") != profile
                or record.get("backend") != desired_backend
                or record.get("config_file") != config_path.name
                or record.get("config_hash") != config_hash
                or record.get("target_ip") != target_ip
            )
            if not needs_install:
                result["skipped_nodes"] += 1
                continue

            if profile == "iepl":
                provision_forwarding_via_jump(settings, jump_host, target_ip, config_path=config_path)
            else:
                provision_forwarding(settings, target_ip, config_path=config_path)
            installed[uuid] = {
                "uuid": uuid,
                "name": item["name"],
                "last_ip": target_ip,
                "installed_at": now_iso(),
                "fingerprint": item["fingerprint"],
                "profile": profile,
                "backend": desired_backend,
                "config_file": config_path.name,
                "config_hash": config_hash,
                "target_ip": target_ip,
            }
            changed = True
            result["applied_nodes"] += 1
        except Exception as e:
            result["failed_nodes"] += 1
            result["errors"].append(
                {
                    "uuid": uuid,
                    "name": item["name"],
                    "ipv4": item.get("ipv4") or "",
                    "target_ip": target_ip or item.get("ipv4") or "",
                    "error": str(e),
                }
            )
            print(f"[~] forwarding provisioning failed for {item['name']} {item['ipv4']}: {e}")
    if changed:
        save_installed_records(installed)
    return result


def ensure_frontend_domain_forwards(settings: dict, snapshot: dict, record_name: str) -> dict:
    domain = get_frontend_domain(settings, record_name)
    installed = load_installed_records()
    changed = False
    rules = list(domain.get("forward_rules", []))
    config_hash = make_frontend_rules_hash(rules)
    items = frontend_domain_member_items(settings, snapshot, record_name)
    result = make_forward_apply_result("default", f"frontend_domains[{record_name}]", len(items))
    for item in items:
        uuid = item["uuid"]
        record = installed.get(uuid)
        target_ip = item.get("ipv4") or ""
        try:
            if not target_ip:
                raise RuntimeError("missing IPv4")
            needs_install = (
                record is None
                or record.get("fingerprint") != item["fingerprint"]
                or record.get("profile") != "default"
                or record.get("backend") != "iptables"
                or record.get("domain_name") != record_name
                or record.get("config_hash") != config_hash
                or record.get("target_ip") != target_ip
            )
            if not needs_install:
                result["skipped_nodes"] += 1
                continue
            provision_forwarding_rules(settings, target_ip, rules, source_label=f"{record_name} frontend rules")
            installed[uuid] = {
                "uuid": uuid,
                "name": item["name"],
                "last_ip": target_ip,
                "installed_at": now_iso(),
                "fingerprint": item["fingerprint"],
                "profile": "default",
                "backend": "iptables",
                "config_file": f"frontend_domains[{record_name}]",
                "config_hash": config_hash,
                "target_ip": target_ip,
                "domain_name": record_name,
            }
            changed = True
            result["applied_nodes"] += 1
        except Exception as e:
            result["failed_nodes"] += 1
            result["errors"].append(
                {
                    "uuid": uuid,
                    "name": item["name"],
                    "ipv4": item.get("ipv4") or "",
                    "target_ip": target_ip or "",
                    "error": str(e),
                }
            )
            print(f"[~] frontend forwarding provisioning failed for {record_name} -> {item['name']} {item.get('ipv4')}: {e}")
    if changed:
        save_installed_records(installed)
    return result


def cleanup_unassigned_frontend_nodes(settings: dict, snapshot: dict, assigned_uuids: set[str]) -> dict:
    installed = load_installed_records()
    cleanup_nodes = [
        item
        for item in snapshot["nodes"]
        if item["uuid"] not in assigned_uuids and installed.get(item["uuid"], {}).get("backend") == "iptables"
    ]
    result = make_forward_apply_result("default", "frontend_cleanup", len(cleanup_nodes))
    if not cleanup_nodes:
        return result
    changed = False
    for item in cleanup_nodes:
        uuid = item["uuid"]
        record = installed.get(uuid, {})
        target_ip = item.get("ipv4") or str(record.get("target_ip") or "")
        try:
            if not target_ip:
                raise RuntimeError("missing IPv4")
            provision_forwarding_rules(settings, target_ip, [], source_label="frontend cleanup")
            installed.pop(uuid, None)
            changed = True
            result["applied_nodes"] += 1
        except Exception as e:
            result["failed_nodes"] += 1
            result["errors"].append(
                {
                    "uuid": uuid,
                    "name": item["name"],
                    "ipv4": item.get("ipv4") or "",
                    "target_ip": target_ip or "",
                    "error": str(e),
                }
            )
            print(f"[~] frontend cleanup failed for {item['name']} {item.get('ipv4')}: {e}")
    if changed:
        save_installed_records(installed)
    return result


def ensure_backup_forwards(settings: dict, backups: list[dict], strict: bool = False) -> dict:
    result = ensure_named_forwards(settings, backups, profile="default", config_path=REALM_CONFIG_PATH)
    if strict:
        raise_for_failed_forward_apply(result, "frontend forwarding")
    return result


def ensure_iepl_forwards(settings: dict, iepl_nodes: list[dict], jump_host: str | None, strict: bool = False) -> dict:
    result = make_forward_apply_result("iepl", IEPL_CONFIG_PATH, len(iepl_nodes))
    if not iepl_nodes:
        return result
    if not IEPL_CONFIG_PATH.exists():
        msg = f"missing forwarding config: {IEPL_CONFIG_PATH}"
        print(f"[~] skip IEPL forwarding install: missing {IEPL_CONFIG_PATH.name}")
        result["failed_nodes"] = len(iepl_nodes)
        result["errors"] = [
            {
                "uuid": item["uuid"],
                "name": item["name"],
                "ipv4": item.get("ipv4") or "",
                "target_ip": "",
                "error": msg,
            }
            for item in iepl_nodes
        ]
        if strict:
            raise RuntimeError(msg)
        return result
    if not jump_host:
        msg = "no available non-IEPL jump host for IEPL provisioning"
        print("[~] skip IEPL forwarding install: no available non-IEPL jump host")
        result["failed_nodes"] = len(iepl_nodes)
        result["errors"] = [
            {
                "uuid": item["uuid"],
                "name": item["name"],
                "ipv4": item.get("ipv4") or "",
                "target_ip": "",
                "error": msg,
            }
            for item in iepl_nodes
        ]
        if strict:
            raise RuntimeError(msg)
        return result
    result = ensure_named_forwards(
        settings,
        iepl_nodes,
        profile="iepl",
        config_path=IEPL_CONFIG_PATH,
        jump_host=jump_host,
    )
    if strict:
        raise_for_failed_forward_apply(result, "IEPL forwarding")
    return result


def apply_frontend_domain_forwards_now(
    settings: dict,
    record_name: str,
    strict: bool = False,
    snapshot: dict | None = None,
) -> dict:
    snapshot = snapshot or fetch_snapshot(settings)
    record_name = resolve_frontend_domain_name(settings, record_name)
    result = ensure_frontend_domain_forwards(settings, snapshot, record_name)
    if strict:
        raise_for_failed_forward_apply(result, f"frontend forwarding for {record_name}")
    return result


def apply_frontend_forwards_now(settings: dict, strict: bool = False, snapshot: dict | None = None) -> dict:
    snapshot = snapshot or fetch_snapshot(settings)
    result = make_forward_apply_result("default", "frontend_domains", 0)
    assigned_uuids: set[str] = set()
    for record_name in list_frontend_domain_names(settings, enabled_only=True):
        merge_forward_apply_result(result, ensure_frontend_domain_forwards(settings, snapshot, record_name))
        assigned_uuids.update(frontend_domain_member_uuids(settings, record_name, snapshot))
    merge_forward_apply_result(result, cleanup_unassigned_frontend_nodes(settings, snapshot, assigned_uuids))
    if strict:
        raise_for_failed_forward_apply(result, "frontend forwarding")
    return result


def apply_iepl_forwards_now(
    settings: dict,
    strict: bool = False,
    snapshot: dict | None = None,
    iepl_snapshot: dict | None = None,
) -> dict:
    snapshot = snapshot or fetch_snapshot(settings)
    iepl_snapshot = iepl_snapshot or fetch_iepl_snapshot(settings)
    state = load_runtime_state(settings)
    jump_host_item = choose_jump_host_item(snapshot, primary_uuid_for_jump_host(settings, state))
    return ensure_iepl_forwards(
        settings,
        iepl_snapshot["nodes"],
        jump_host_item["ipv4"] if jump_host_item else None,
        strict=strict,
    )


def cleanup_frontend_legacy_now(settings: dict) -> int:
    snapshot = fetch_snapshot(settings)
    count = 0
    for item in snapshot["nodes"]:
        if not item.get("ipv4"):
            continue
        cleanup_legacy_forwarding(settings, item["ipv4"])
        count += 1
    return count


def cleanup_iepl_legacy_now(settings: dict, uuid: str) -> str:
    snapshot = fetch_snapshot(settings)
    iepl_snapshot = fetch_iepl_snapshot(settings)
    item = next((node for node in iepl_snapshot["nodes"] if node["uuid"] == uuid), None)
    if item is None:
        raise RuntimeError(f"IEPL node not found: {uuid}")
    target_ip = resolve_iepl_target_ip(settings, item)
    state = load_runtime_state(settings)
    jump_host_item = choose_jump_host_item(snapshot, primary_uuid_for_jump_host(settings, state))
    if not jump_host_item:
        raise RuntimeError("no available non-IEPL jump host for IEPL cleanup")
    cleanup_legacy_forwarding_via_jump(settings, jump_host_item["ipv4"], target_ip)
    return target_ip


def restart_iepl_realm_now(settings: dict, uuid: str) -> str:
    snapshot = fetch_snapshot(settings)
    iepl_snapshot = fetch_iepl_snapshot(settings)
    item = next((node for node in iepl_snapshot["nodes"] if node["uuid"] == uuid), None)
    if item is None:
        raise RuntimeError(f"IEPL node not found: {uuid}")
    target_ip = resolve_iepl_target_ip(settings, item)
    state = load_runtime_state(settings)
    jump_host_item = choose_jump_host_item(snapshot, primary_uuid_for_jump_host(settings, state))
    if not jump_host_item:
        raise RuntimeError("no available non-IEPL jump host for IEPL realm restart")
    restart_realm_via_jump(settings, jump_host_item["ipv4"], target_ip)
    return target_ip


def sync_dns(settings: dict, record_name: str, state: dict, ip: str, reason: str, primary_item: dict) -> dict:
    cf_cfg = settings["cloudflare"]
    cf = CloudflareClient(cf_cfg["api_token"], cf_cfg["zone_id"])
    record = cf.get_a_record(record_name)
    current_ip = record.get("content")
    ttl = int(record.get("ttl", cf_cfg.get("record_ttl", 120)))
    proxied = bool(record.get("proxied", cf_cfg.get("proxied", False)))
    if current_ip == ip:
        print(f"[=] DNS already points to {record_name} -> {ip}")
    else:
        cf.update_record_content(record["id"], record_name, ip, ttl, proxied)
        print(f"[!] switched {record_name} {current_ip} -> {ip}, reason={reason}")

    domain_state = get_domain_runtime_state(state, record_name)
    domain_state["current_primary_uuid"] = primary_item["uuid"]
    domain_state["current_primary_name"] = primary_item["name"]
    domain_state["current_primary_ip"] = ip
    domain_state["last_switch_reason"] = reason
    domain_state["last_switch_at"] = now_iso()
    domain_state["last_switch_ts"] = int(time.time())
    domain_state["offline_fail_count"] = 0
    save_runtime_state(state)
    return state


def describe_item(item: dict) -> str:
    uptime_days = float(item["status"].get("uptime", 0) or 0) / 86400.0
    traffic_gb = float(item.get("traffic_bytes", 0) or 0) / (1024.0 * 1024.0 * 1024.0)
    freshness = "stale" if item.get("stale") else "fresh"
    return (
        f"{item['name']} uuid={item['uuid']} ip={item.get('ipv4') or '-'} "
        f"online={item.get('online')} status={freshness} uptime={uptime_days:.2f}d traffic={traffic_gb:.2f}GB"
    )


def format_item_lines(item: dict, role: str, realm_state: str | None = None) -> list[str]:
    uptime_days = float(item["status"].get("uptime", 0) or 0) / 86400.0
    traffic_gb = float(item.get("traffic_bytes", 0) or 0) / (1024.0 * 1024.0 * 1024.0)
    freshness = "stale" if item.get("stale") else "fresh"
    lines = [
        f"[{role}] {item['name']}",
        f"  uuid:    {item['uuid']}",
        f"  ip:      {item.get('ipv4') or '-'}",
        f"  online:  {item.get('online')} ({freshness})",
        f"  uptime:  {uptime_days:.2f}d",
        f"  traffic: {traffic_gb:.2f}GB",
    ]
    if realm_state is not None:
        lines.append(f"  forward: {realm_state}")
    return lines


def maybe_sync_role_tags(settings: dict, snapshot: dict, state: dict, record_name: str) -> None:
    enabled_domains = list_frontend_domain_names(settings, enabled_only=True)
    if len(enabled_domains) != 1 or record_name not in enabled_domains:
        return
    primary_uuid = get_domain_runtime_state(state, record_name).get("current_primary_uuid")
    if not primary_uuid:
        return
    sync_role_tags(settings, snapshot, primary_uuid)


def handle_frontend_domain_sync(
    settings: dict,
    state: dict,
    snapshot: dict,
    record_name: str,
    startup_select: bool = False,
    manual_switch: bool = False,
) -> dict:
    domain_state = get_domain_runtime_state(state, record_name)
    domain = get_frontend_domain(settings, record_name)
    if not domain.get("enabled", True):
        return state
    nodes = frontend_domain_member_items(settings, snapshot, record_name)
    if not nodes:
        raise RuntimeError(f"{record_name}: no frontend candidates found for this domain")

    if startup_select or not domain_state.get("current_primary_uuid"):
        initial = select_frontend_domain_initial_primary(settings, snapshot, record_name)
        domain_state["current_primary_uuid"] = initial["uuid"]
        domain_state["current_primary_name"] = initial["name"]
        domain_state["current_primary_ip"] = initial.get("ipv4") or ""
        domain_state["offline_fail_count"] = 0
        save_runtime_state(state)
        print(f"[*] [{record_name}] selected initial primary: {describe_item(initial)}")

    primary, backups = split_frontend_domain_primary_and_backups(
        settings,
        snapshot,
        record_name,
        domain_state.get("current_primary_uuid"),
    )

    if manual_switch:
        target = choose_frontend_domain_failover_backup(settings, snapshot, record_name, domain_state.get("current_primary_uuid"))
        if target is None:
            raise RuntimeError(f"{record_name}: no healthy backup candidate available for manual switch")
        state = sync_dns(settings, record_name, state, target["ipv4"], "manual switch-now", target)
        try:
            maybe_sync_role_tags(settings, snapshot, state, record_name)
        except Exception as e:
            print(f"[~] [{record_name}] tag sync failed after manual switch: {e}")
        return load_runtime_state(settings)

    if primary is None:
        replacement = choose_frontend_domain_failover_backup(settings, snapshot, record_name, None)
        if replacement is None:
            raise RuntimeError(f"{record_name}: current primary disappeared and no healthy backup is available")
        state = sync_dns(settings, record_name, state, replacement["ipv4"], "primary missing from domain candidates", replacement)
        try:
            maybe_sync_role_tags(settings, snapshot, state, record_name)
        except Exception as e:
            print(f"[~] [{record_name}] tag sync failed after primary replacement: {e}")
        return load_runtime_state(settings)

    if primary.get("stale"):
        age = primary.get("status_age_sec")
        age_text = f"{age:.0f}s" if age is not None else "unknown"
        print(f"[~] [{record_name}] primary status stale for {primary['name']} ({age_text}), skip switch this round")
        return state

    if not primary.get("online"):
        domain_state["offline_fail_count"] = int(domain_state.get("offline_fail_count", 0)) + 1
        print(
            f"[-] [{record_name}] primary offline "
            f"{domain_state['offline_fail_count']}/{int(settings.get('fail_threshold', 3))}: {describe_item(primary)}"
        )
        if domain_state["offline_fail_count"] >= int(settings.get("fail_threshold", 3)):
            replacement = choose_frontend_domain_failover_backup(settings, snapshot, record_name, primary["uuid"])
            if replacement is None:
                print(f"[~] [{record_name}] no healthy backup candidate available, keep current DNS")
            else:
                state = sync_dns(settings, record_name, state, replacement["ipv4"], f"primary offline: {primary['name']}", replacement)
                try:
                    maybe_sync_role_tags(settings, snapshot, state, record_name)
                except Exception as e:
                    print(f"[~] [{record_name}] tag sync failed after offline failover: {e}")
                return load_runtime_state(settings)
        save_runtime_state(state)
        return state

    domain_state["offline_fail_count"] = 0
    hit, detail = over_limit_detail_for_node(settings, primary)
    print(f"[*] [{record_name}] primary {describe_item(primary)}")
    if hit:
        replacement = choose_frontend_domain_failover_backup(settings, snapshot, record_name, primary["uuid"])
        if replacement is None:
            print(f"[~] [{record_name}] primary over threshold but no healthy backup available: {detail}")
        else:
            state = sync_dns(settings, record_name, state, replacement["ipv4"], f"primary threshold reached: {detail}", replacement)
            try:
                maybe_sync_role_tags(settings, snapshot, state, record_name)
            except Exception as e:
                print(f"[~] [{record_name}] tag sync failed after threshold failover: {e}")
            return load_runtime_state(settings)

    if primary.get("ipv4"):
        state = sync_dns(settings, record_name, state, primary["ipv4"], "ensure DNS follows locked primary", primary)
        try:
            maybe_sync_role_tags(settings, snapshot, state, record_name)
        except Exception as e:
            print(f"[~] [{record_name}] tag sync failed during steady-state sync: {e}")
    else:
        print(f"[~] [{record_name}] primary {primary['name']} missing usable IPv4, keep current DNS")

    save_runtime_state(state)
    return state


def handle_sync(
    settings: dict,
    state: dict,
    startup_select: bool = False,
    manual_switch: bool = False,
    record_name: str | None = None,
) -> dict:
    state = normalize_runtime_state(state, settings)
    snapshot = fetch_snapshot(settings)
    iepl_snapshot = fetch_iepl_snapshot(settings)
    nodes = snapshot["nodes"]
    if not nodes:
        raise RuntimeError("no IX candidates found after filtering IEPL nodes")

    apply_frontend_forwards_now(settings, snapshot=snapshot)
    jump_host_item = choose_jump_host_item(snapshot, primary_uuid_for_jump_host(settings, state))
    ensure_iepl_forwards(settings, iepl_snapshot["nodes"], jump_host_item["ipv4"] if jump_host_item else None)

    if record_name:
        domain_names = [resolve_frontend_domain_name(settings, record_name)]
    elif manual_switch:
        domain_names = [resolve_frontend_domain_name(settings, None)]
    else:
        domain_names = list_frontend_domain_names(settings, enabled_only=True)

    for current_record_name in domain_names:
        domain_state = get_domain_runtime_state(state, current_record_name)
        domain_startup_select = startup_select and not bool(domain_state.get("current_primary_uuid"))
        state = handle_frontend_domain_sync(
            settings,
            state,
            snapshot,
            current_record_name,
            startup_select=domain_startup_select,
            manual_switch=manual_switch,
        )

    save_runtime_state(state)
    return state


def cmd_init() -> None:
    if SETTINGS_PATH.exists():
        print(f"[=] {SETTINGS_PATH} already exists")
        return
    template_path = SETTINGS_TEMPLATE_PATH if SETTINGS_TEMPLATE_PATH.exists() else LEGACY_SETTINGS_TEMPLATE_PATH
    if not template_path.exists():
        raise RuntimeError(f"template missing: {SETTINGS_TEMPLATE_PATH} or {LEGACY_SETTINGS_TEMPLATE_PATH}")
    SETTINGS_PATH.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[+] created {SETTINGS_PATH}, fill Komari and Cloudflare credentials first")


def max_runtime_switch_ts(state: dict) -> int:
    return max((int(entry.get("last_switch_ts", 0) or 0) for entry in state.get("domains", {}).values()), default=0)


def cmd_status(settings: dict, record_name: str | None = None) -> None:
    state = load_runtime_state(settings)
    snapshot = fetch_snapshot(settings)
    installed = load_installed_records()
    print(f"[*] fetched_at={snapshot['fetched_at']}")
    domain_names = (
        [resolve_frontend_domain_name(settings, record_name)]
        if record_name
        else list_frontend_domain_names(settings, enabled_only=False)
    )
    for current_record_name in domain_names:
        domain = get_frontend_domain(settings, current_record_name)
        domain_state = get_domain_runtime_state(state, current_record_name)
        primary, backups = split_frontend_domain_primary_and_backups(
            settings,
            snapshot,
            current_record_name,
            domain_state.get("current_primary_uuid"),
        )
        print(f"[domain] {current_record_name} enabled={domain.get('enabled', True)}")
        print(f"  preferred: {domain.get('preferred_primary_uuid') or '-'}")
        print(f"  backups:   {', '.join(domain.get('backup_uuids', [])) or '(empty)'}")
        print(f"  last:      {domain_state.get('last_switch_reason') or '-'}")
        if primary:
            for line in format_item_lines(primary, "primary"):
                print(line)
        else:
            print("[primary] (none)")
        if not backups:
            print("[backups] (empty)")
        for item in backups:
            marker = "installed" if item["uuid"] in installed else "pending-install"
            for line in format_item_lines(item, "backup", realm_state=marker):
                print(line)


def cmd_sync(settings: dict, startup_select: bool | None = None, record_name: str | None = None) -> None:
    state = load_runtime_state(settings)
    if startup_select is None:
        startup_select = True
    handle_sync(settings, state, startup_select=startup_select, manual_switch=False, record_name=record_name)


def cmd_list_forwards(settings: dict, record_name: str | None = None) -> None:
    domain_names = (
        [resolve_frontend_domain_name(settings, record_name)]
        if record_name
        else list_frontend_domain_names(settings, enabled_only=False)
    )
    for current_record_name in domain_names:
        rules = get_frontend_domain(settings, current_record_name).get("forward_rules", [])
        print(f"[domain] {current_record_name}")
        if not rules:
            print("(empty)")
            continue
        for rule in rules:
            print(f"- listen=0.0.0.0:{rule['listen_port']} -> {rule['remote_host']}:{rule['remote_port']}")


def cmd_add_forward(settings: dict, record_name: str, listen_port: int, remote_host: str, remote_port: int) -> dict:
    added, result = add_frontend_domain_forward_rule_transactionally(settings, record_name, listen_port, remote_host, remote_port)
    resolved_record_name = resolve_frontend_domain_name(settings, record_name)
    print(
        f"[+] added forward rule for {resolved_record_name}: "
        f"0.0.0.0:{added['listen_port']} -> {added['remote_host']}:{added['remote_port']}"
    )
    print(f"[+] frontend forwarding reapplied immediately: {format_forward_apply_summary(result)}")
    return result


def cmd_remove_forward(settings: dict, record_name: str, listen_port: int) -> dict:
    resolved_record_name = resolve_frontend_domain_name(settings, record_name)
    removed, result = remove_frontend_domain_forward_rule_transactionally(settings, resolved_record_name, listen_port)
    print(
        f"[+] removed forward rule for {resolved_record_name}: "
        f"0.0.0.0:{removed['listen_port']} -> {removed['remote_host']}:{removed['remote_port']}"
    )
    print(f"[+] frontend forwarding reapplied immediately: {format_forward_apply_summary(result)}")
    return result


def cmd_update_forward(
    settings: dict,
    record_name: str,
    old_listen_port: int,
    listen_port: int,
    remote_host: str,
    remote_port: int,
) -> dict:
    resolved_record_name = resolve_frontend_domain_name(settings, record_name)
    updated, result = update_frontend_domain_forward_rule_transactionally(
        settings,
        resolved_record_name,
        old_listen_port,
        listen_port,
        remote_host,
        remote_port,
    )
    before = updated["before"]
    after = updated["after"]
    print(
        f"[+] updated forward rule for {resolved_record_name}: "
        f"0.0.0.0:{before['listen_port']} -> {before['remote_host']}:{before['remote_port']} "
        f"=> 0.0.0.0:{after['listen_port']} -> {after['remote_host']}:{after['remote_port']}"
    )
    print(f"[+] frontend forwarding reapplied immediately: {format_forward_apply_summary(result)}")
    return result


def cmd_switch_now(settings: dict, record_name: str | None = None) -> None:
    state = load_runtime_state(settings)
    handle_sync(settings, state, startup_select=True, manual_switch=True, record_name=record_name)


def cmd_reinstall_forward(settings: dict, uuid: str | None = None, record_name: str | None = None) -> dict:
    if record_name:
        resolved_record_name = resolve_frontend_domain_name(settings, record_name)
        result = apply_frontend_domain_forwards_now(settings, resolved_record_name, strict=True)
        print(f"[+] frontend forwarding reinstalled for domain {resolved_record_name}: {format_forward_apply_summary(result)}")
        return result
    if not uuid:
        raise RuntimeError("uuid or domain is required")
    snapshot = fetch_snapshot(settings)
    iepl_snapshot = fetch_iepl_snapshot(settings)
    state = load_runtime_state(settings)
    item = next((node for node in snapshot["nodes"] if node["uuid"] == uuid), None)
    profile = "default"
    jump_host = None
    domain_name = None
    if item is None:
        item = next((node for node in iepl_snapshot["nodes"] if node["uuid"] == uuid), None)
        profile = "iepl"
    if item is None:
        raise RuntimeError(f"node not found in IX group: {uuid}")
    if profile == "default" and not item.get("ipv4"):
        raise RuntimeError(f"node {uuid} has no usable IPv4")
    if profile == "iepl":
        if not IEPL_CONFIG_PATH.exists():
            raise RuntimeError(f"missing forwarding config: {IEPL_CONFIG_PATH}")
        config_hash = file_sha256(IEPL_CONFIG_PATH)
        jump_host_item = choose_jump_host_item(snapshot, primary_uuid_for_jump_host(settings, state))
        if not jump_host_item:
            raise RuntimeError("no available non-IEPL jump host for IEPL provisioning")
        jump_host = jump_host_item["ipv4"]
        target_ip = resolve_iepl_target_ip(settings, item)
        provision_forwarding_via_jump(settings, jump_host, target_ip, config_path=IEPL_CONFIG_PATH)
        config_file = IEPL_CONFIG_PATH.name
    else:
        domain_name = frontend_domain_owner_map(settings, snapshot).get(uuid)
        if not domain_name:
            raise RuntimeError(f"frontend node {uuid} is not assigned to any frontend domain")
        domain = get_frontend_domain(settings, domain_name)
        config_hash = make_frontend_rules_hash(domain.get("forward_rules", []))
        target_ip = item["ipv4"]
        provision_forwarding_rules(settings, target_ip, domain.get("forward_rules", []), source_label=f"{domain_name} frontend rules")
        config_file = f"frontend_domains[{domain_name}]"
    installed = load_installed_records()
    backend = "realm" if profile == "iepl" else "iptables"
    installed[uuid] = {
        "uuid": uuid,
        "name": item["name"],
        "last_ip": target_ip,
        "installed_at": now_iso(),
        "fingerprint": item["fingerprint"],
        "profile": profile,
        "backend": backend,
        "config_file": config_file,
        "config_hash": config_hash,
        "target_ip": target_ip,
        **({"domain_name": domain_name} if domain_name else {}),
    }
    save_installed_records(installed)
    if profile == "iepl":
        print(f"[+] IEPL forwarding reinstalled for {item['name']} ({uuid})")
    else:
        print(f"[+] frontend forwarding reinstalled for {item['name']} ({uuid}) in domain {domain_name}")
    return {"ok": True, "uuid": uuid, "profile": profile, "domain_name": domain_name}


def monitor_loop(settings: dict) -> None:
    interval = int(settings.get("check_interval", settings.get("ping_interval", 10)))
    cooldown = int(settings.get("switch_cooldown", 60))
    print(f"[*] monitoring Komari group={settings['komari'].get('group_name', 'IX')} every {interval}s")
    while True:
        try:
            settings = load_settings()
            interval = int(settings.get("check_interval", settings.get("ping_interval", 10)))
            cooldown = int(settings.get("switch_cooldown", 60))
            state = load_runtime_state(settings)
            previous_switch_ts = max_runtime_switch_ts(state)
            state = handle_sync(
                settings,
                state,
                startup_select=True,
                manual_switch=False,
            )
            current_switch_ts = max_runtime_switch_ts(state)
            if current_switch_ts > previous_switch_ts and cooldown > 0:
                print(f"[~] cooldown sleep {cooldown}s after switch")
                time.sleep(cooldown)
                continue
        except Exception as e:
            print(f"[~] sync failed: {e}")
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Komari IX auto failover + Cloudflare DNS + iptables(frontend)/realm(IEPL) provisioning"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create settings.json from Komari template")
    sub.add_parser("run", help="run monitor loop")
    p_sync = sub.add_parser("sync", help="run one discovery and sync round")
    p_sync.add_argument("--domain", help="frontend domain record_name")
    p_status = sub.add_parser("status", help="show current primary and backups")
    p_status.add_argument("--domain", help="frontend domain record_name")
    p_switch_now = sub.add_parser("switch-now", help="force switch to best healthy backup immediately")
    p_switch_now.add_argument("--domain", help="frontend domain record_name")
    p_list_forwards = sub.add_parser("list-forwards", help="list forwarding rules for frontend domains")
    p_list_forwards.add_argument("--domain", help="frontend domain record_name")
    p_add_forward = sub.add_parser("add-forward", help="add forwarding rule to a frontend domain")
    p_add_forward.add_argument("--domain", required=True, help="frontend domain record_name")
    p_add_forward.add_argument("--listen-port", type=int, required=True, help="local listen port")
    p_add_forward.add_argument("--remote-host", required=True, help="remote IPv4 address")
    p_add_forward.add_argument("--remote-port", type=int, required=True, help="remote target port")
    p_update_forward = sub.add_parser("update-forward", help="update forwarding rule in a frontend domain")
    p_update_forward.add_argument("--domain", required=True, help="frontend domain record_name")
    p_update_forward.add_argument("--old-listen-port", type=int, required=True, help="current local listen port")
    p_update_forward.add_argument("--listen-port", type=int, required=True, help="new local listen port")
    p_update_forward.add_argument("--remote-host", required=True, help="new remote IPv4 address")
    p_update_forward.add_argument("--remote-port", type=int, required=True, help="new remote target port")
    p_remove_forward = sub.add_parser("remove-forward", help="remove forwarding rule from a frontend domain")
    p_remove_forward.add_argument("--domain", required=True, help="frontend domain record_name")
    p_remove_forward.add_argument("--listen-port", type=int, required=True, help="local listen port")
    p_reinstall = sub.add_parser("reinstall-forward", help="force reinstall forwarding on a domain or node")
    reinstall_target = p_reinstall.add_mutually_exclusive_group(required=True)
    reinstall_target.add_argument("--uuid", help="candidate node uuid")
    reinstall_target.add_argument("--domain", help="frontend domain record_name")

    args = parser.parse_args()

    try:
        if args.command == "init":
            cmd_init()
            return 0

        settings = load_settings()

        if args.command == "run":
            monitor_loop(settings)
        elif args.command == "sync":
            cmd_sync(settings, record_name=getattr(args, "domain", None))
        elif args.command == "status":
            cmd_status(settings, record_name=getattr(args, "domain", None))
        elif args.command == "switch-now":
            cmd_switch_now(settings, record_name=getattr(args, "domain", None))
        elif args.command == "list-forwards":
            cmd_list_forwards(settings, record_name=getattr(args, "domain", None))
        elif args.command == "add-forward":
            cmd_add_forward(settings, args.domain, args.listen_port, args.remote_host, args.remote_port)
        elif args.command == "update-forward":
            cmd_update_forward(
                settings,
                args.domain,
                args.old_listen_port,
                args.listen_port,
                args.remote_host,
                args.remote_port,
            )
        elif args.command == "remove-forward":
            cmd_remove_forward(settings, args.domain, args.listen_port)
        elif args.command == "reinstall-forward":
            cmd_reinstall_forward(settings, uuid=getattr(args, "uuid", None), record_name=getattr(args, "domain", None))
        else:
            parser.print_help()
            return 1
    except Exception as e:
        print(f"[x] {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
