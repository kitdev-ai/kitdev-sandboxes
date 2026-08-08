#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly COMPOSE_ROOT="$KITDEV_OPT_ROOT/compose/control-plane"
readonly COMPOSE_FILE="$COMPOSE_ROOT/compose.yaml"
if [[ "$SCRIPT_DIR" == "$KITDEV_OPT_ROOT/libexec/control-plane" ]]; then
  readonly SOURCE_ROOT="$COMPOSE_ROOT"
else
  readonly SOURCE_ROOT="$REPO_ROOT/compose/control-plane"
fi

compose() {
  docker compose --project-name kitdev-control-plane \
    --env-file "$KITDEV_PRIVATE_ENV" --file "$COMPOSE_FILE" "$@"
}

install_assets() {
  ensure_directory "$KITDEV_OPT_ROOT/compose" root root 755
  ensure_directory "$COMPOSE_ROOT" root root 755
  ensure_directory "$COMPOSE_ROOT/clickhouse" root root 755
  ensure_directory "$COMPOSE_ROOT/loki" root root 755
  ensure_directory "$COMPOSE_ROOT/postgres" root root 755
  ensure_directory "$COMPOSE_ROOT/postgres/initdb" root root 755
  update_exact_file "$SOURCE_ROOT/compose.yaml" "$COMPOSE_FILE" root root 644
  update_exact_file "$SOURCE_ROOT/images.lock.json" "$COMPOSE_ROOT/images.lock.json" root root 644
  update_exact_file "$SOURCE_ROOT/clickhouse/cluster.xml" \
    "$COMPOSE_ROOT/clickhouse/cluster.xml" root root 644
  update_exact_file "$SOURCE_ROOT/loki/config.yaml" \
    "$COMPOSE_ROOT/loki/config.yaml" root root 644
  update_exact_file "$SOURCE_ROOT/postgres/initdb/00-upstream-roles.sql" \
    "$COMPOSE_ROOT/postgres/initdb/00-upstream-roles.sql" root root 644
}

validate_config() (
  local rendered
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" verify >/dev/null
  "$SCRIPT_DIR/bootstrap-network.sh" verify >/dev/null
  require_exact_file "$COMPOSE_FILE" "$SOURCE_ROOT/compose.yaml" root root 644
  require_exact_file "$COMPOSE_ROOT/images.lock.json" "$SOURCE_ROOT/images.lock.json" root root 644
  require_exact_file "$COMPOSE_ROOT/clickhouse/cluster.xml" \
    "$SOURCE_ROOT/clickhouse/cluster.xml" root root 644
  require_exact_file "$COMPOSE_ROOT/loki/config.yaml" "$SOURCE_ROOT/loki/config.yaml" root root 644
  require_exact_file "$COMPOSE_ROOT/postgres/initdb/00-upstream-roles.sql" \
    "$SOURCE_ROOT/postgres/initdb/00-upstream-roles.sql" root root 644
  rendered="$(mktemp /tmp/kitdev-compose-config.XXXXXXXX)"
  trap 'rm -f -- "$rendered"' EXIT
  chmod 0600 -- "$rendered"
  compose config --format json >"$rendered"
  /usr/bin/python3 -I -B -S - "$rendered" <<'PY_COMPOSE_CONTRACT'
import json
import re
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
services = document.get("services", {})
expected_services = {
    "postgres", "redis", "clickhouse", "loki", "postgres-migrator",
    "clickhouse-migrator", "api", "client-proxy",
}
if set(services) != expected_services:
    raise SystemExit(1)
observed = {
    (name, port.get("host_ip"), int(port["published"]), int(port["target"]), port.get("protocol", "tcp"))
    for name, service in services.items()
    for port in service.get("ports", [])
}
expected = {
    ("postgres", "127.0.0.1", 5432, 5432, "tcp"),
    ("clickhouse", "127.0.0.1", 8123, 8123, "tcp"),
    ("clickhouse", "127.0.0.1", 9000, 9000, "tcp"),
    ("api", "127.0.0.1", 3000, 3000, "tcp"),
    ("client-proxy", "127.0.0.1", 3002, 3002, "tcp"),
    ("client-proxy", "127.0.0.1", 3003, 3003, "tcp"),
}
if observed != expected:
    raise SystemExit(1)
source_services = ("api", "postgres-migrator", "clickhouse-migrator", "client-proxy")
source_refs = [services[name].get("image", "") for name in source_services]
if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in source_refs) or len(set(source_refs)) != 4:
    raise SystemExit(1)
for name in ("postgres", "redis", "clickhouse", "loki"):
    if not re.fullmatch(r"[^@]+@sha256:[0-9a-f]{64}", services[name].get("image", "")):
        raise SystemExit(1)
for name in ("api", "client-proxy"):
    extra_hosts = services[name].get("extra_hosts", {})
    if isinstance(extra_hosts, list):
        mappings = dict(item.replace("=", ":", 1).split(":", 1) for item in extra_hosts)
    else:
        mappings = extra_hosts
    if set(mappings) != {"host.docker.internal"} or mappings["host.docker.internal"] == "host-gateway":
        raise SystemExit(1)
networks = document.get("networks", {})
core = networks.get("core", {})
if core.get("name") != "kitdev-core" or core.get("external") is not True:
    raise SystemExit(1)
PY_COMPOSE_CONTRACT
)

verify_local_images() {
  local references reference
  references="$(/usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" get-images)" ||
    control_plane_die local_image_refs_invalid 65
  mapfile -t ids <<<"$references"
  [[ "${#ids[@]}" == 4 ]] || control_plane_die local_image_refs_invalid 65
  for reference in "${ids[@]}"; do
    [[ "$reference" =~ ^sha256:[0-9a-f]{64}$ ]] || control_plane_die local_image_ref_invalid 65
    [[ "$(docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' -- "$reference")" == "$reference linux/amd64" ]] ||
      control_plane_die local_image_missing 65
  done
}

verify_health() {
  local endpoint code
  "$SCRIPT_DIR/configure-firewall.sh" verify >/dev/null
  for endpoint in \
    http://127.0.0.1:5008/health \
    http://127.0.0.1:3000/health \
    http://127.0.0.1:3003/health; do
    code="$(curl --config /dev/null --silent --show-error --output /dev/null \
      --write-out '%{http_code}' --max-time 5 -- "$endpoint")" ||
      control_plane_die control_plane_health_unreachable 65
    [[ "$code" == 200 ]] || control_plane_die control_plane_health_failed 65
  done
}

verify_migrations() {
  local service container state
  for service in postgres-migrator clickhouse-migrator; do
    container="$(compose ps --all --quiet "$service")" ||
      control_plane_die migration_container_unreadable 65
    [[ "$container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die migration_container_invalid 65
    state="$(docker inspect --format '{{.State.Status}} {{.State.ExitCode}}' -- "$container")" ||
      control_plane_die migration_state_unreadable 65
    [[ "$state" == 'exited 0' ]] || control_plane_die migration_failed 65
  done
}

verify_runtime_contract() (
  local rendered
  rendered="$(mktemp /tmp/kitdev-compose-runtime.XXXXXXXX)"
  trap 'rm -f -- "$rendered"' EXIT
  chmod 0600 -- "$rendered"
  compose config --format json >"$rendered"
  /usr/bin/python3 -I -B -S - "$rendered" <<'PY_RUNTIME_CONTRACT'
import json
import subprocess
import sys

expected = json.load(open(sys.argv[1], encoding="utf-8"))["services"]
ids = subprocess.check_output([
    "docker", "ps", "--all", "--quiet", "--filter",
    "label=com.docker.compose.project=kitdev-control-plane",
], text=True).split()
if len(ids) != len(expected):
    raise SystemExit(1)
containers = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True))
by_service = {}
image_configs = {}
for container in containers:
    labels = container["Config"].get("Labels") or {}
    service = labels.get("com.docker.compose.service")
    if (
        labels.get("com.docker.compose.project") != "kitdev-control-plane"
        or str(labels.get("com.docker.compose.oneoff", "")).lower() != "false"
        or service in by_service
    ):
        raise SystemExit(1)
    by_service[service] = container
if set(by_service) != set(expected):
    raise SystemExit(1)


def image_config(identifier):
    if identifier not in image_configs:
        image_configs[identifier] = json.loads(subprocess.check_output(
            ["docker", "image", "inspect", identifier], text=True,
        ))[0]["Config"]
    return image_configs[identifier]


def environment_map(entries):
    values = {}
    for entry in entries or []:
        if "=" not in entry:
            raise SystemExit(1)
        key, value = entry.split("=", 1)
        if key in values:
            raise SystemExit(1)
        values[key] = value
    return values


def tmpfs_map(entries):
    values = {}
    for entry in entries or []:
        target, separator, options = entry.partition(":")
        if not target or target in values:
            raise SystemExit(1)
        values[target] = frozenset(options.split(",")) if separator else frozenset()
    return values


def extra_hosts_map(entries):
    if isinstance(entries, dict):
        return {key: str(value) for key, value in entries.items()}
    values = {}
    for entry in entries or []:
        separator = "=" if "=" in entry else ":"
        key, found, value = entry.partition(separator)
        if not found or not key or key in values:
            raise SystemExit(1)
        values[key] = value
    return values


for service, contract in expected.items():
    container = by_service[service]
    state = container["State"]
    host = container["HostConfig"]
    config = container["Config"]
    source_image = image_config(container["Image"])
    if container["Config"].get("Image") != contract.get("image"):
        raise SystemExit(1)
    if set(container["NetworkSettings"].get("Networks") or {}) != {"kitdev-core"}:
        raise SystemExit(1)
    if (
        host.get("Privileged") is not False
        or host.get("PublishAllPorts") is not False
        or host.get("ReadonlyRootfs") is not bool(contract.get("read_only", False))
        or sorted(host.get("CapAdd") or []) != sorted(contract.get("cap_add") or [])
        or sorted(host.get("CapDrop") or []) != sorted(contract.get("cap_drop") or [])
        or sorted(host.get("SecurityOpt") or []) != sorted(contract.get("security_opt") or [])
        or bool(host.get("Devices"))
    ):
        raise SystemExit(1)
    expected_user = contract.get("user", source_image.get("User") or "")
    expected_command = contract.get("command", source_image.get("Cmd"))
    expected_workdir = contract.get("working_dir", source_image.get("WorkingDir") or "")
    if (
        config.get("User", "") != expected_user
        or config.get("Cmd") != expected_command
        or config.get("Entrypoint") != source_image.get("Entrypoint")
        or config.get("WorkingDir", "") != expected_workdir
    ):
        raise SystemExit(1)
    expected_environment = environment_map(source_image.get("Env"))
    expected_environment.update(contract.get("environment") or {})
    if environment_map(config.get("Env")) != expected_environment:
        raise SystemExit(1)
    expected_ports = {}
    for port in contract.get("ports", []):
        key = f'{port["target"]}/{port.get("protocol", "tcp")}'
        expected_ports.setdefault(key, []).append({
            "HostIp": port["host_ip"],
            "HostPort": str(port["published"]),
        })
    observed_ports = host.get("PortBindings") or {}
    if observed_ports != expected_ports:
        raise SystemExit(1)
    effective_ports = {
        key: value
        for key, value in (container["NetworkSettings"].get("Ports") or {}).items()
        if value is not None
    }
    if effective_ports != expected_ports:
        raise SystemExit(1)
    if extra_hosts_map(host.get("ExtraHosts")) != extra_hosts_map(contract.get("extra_hosts")):
        raise SystemExit(1)
    expected_mounts = sorted(
        (
            volume["type"], volume["source"], volume["target"],
            not bool(volume.get("read_only", False)),
        )
        for volume in contract.get("volumes", [])
    )
    observed_mounts = sorted(
        (mount["Type"], mount["Source"], mount["Destination"], mount["RW"])
        for mount in container.get("Mounts", [])
    )
    if observed_mounts != expected_mounts:
        raise SystemExit(1)
    expected_tmpfs = tmpfs_map(contract.get("tmpfs"))
    observed_tmpfs = {
        target: frozenset(options.split(",")) if options else frozenset()
        for target, options in (host.get("Tmpfs") or {}).items()
    }
    if observed_tmpfs != expected_tmpfs:
        raise SystemExit(1)
    if service in {"postgres-migrator", "clickhouse-migrator"}:
        if state.get("Status") != "exited" or state.get("ExitCode") != 0:
            raise SystemExit(1)
    elif state.get("Status") != "running" or state.get("Health", {}).get("Status") != "healthy":
        raise SystemExit(1)
PY_RUNTIME_CONTRACT
)

main() {
  local mode="${1:-}"
  case "$mode" in install|validate|pull|up|quiesce|down|verify) ;;
    *) control_plane_die invalid_operation 64 ;;
  esac
  require_root
  require_lifecycle_platform
  require_command docker
  require_command curl
  require_command timeout
  if [[ "$mode" == install ]]; then
    install_assets
    printf 'status=pass operation=install-compose-assets\n'
    return 0
  fi
  validate_config || control_plane_die compose_contract_invalid 65
  verify_local_images
  case "$mode" in
    validate) ;;
    pull) compose pull postgres redis clickhouse loki ;;
    up)
      compose up --detach
      timeout 300 docker compose --project-name kitdev-control-plane \
        --env-file "$KITDEV_PRIVATE_ENV" --file "$COMPOSE_FILE" \
        wait postgres-migrator clickhouse-migrator
      verify_migrations
      compose up --detach --wait --wait-timeout 300 api client-proxy
      verify_runtime_contract || control_plane_die compose_runtime_contract_invalid 65
      ;;
    quiesce)
      compose stop --timeout 60 api client-proxy
      [[ -z "$(compose ps --status running --quiet api client-proxy)" ]] ||
        control_plane_die control_plane_quiesce_failed 65
      ;;
    down)
      compose stop --timeout 60
      [[ -z "$(compose ps --status running --quiet)" ]] ||
        control_plane_die control_plane_stop_failed 65
      ;;
    verify)
      verify_runtime_contract || control_plane_die compose_runtime_contract_invalid 65
      verify_health
      ;;
  esac
  printf 'status=pass operation=%s-control-plane\n' "$mode"
}

main "$@"
