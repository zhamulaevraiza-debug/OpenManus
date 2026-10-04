#!/usr/bin/env bash
# Entrypoint of the OpenManus web image: prepares the data volume, then runs the
# command (default: the web server).
#
# * The data directory is traversable but not listable, and everything in it except
#   the workspaces (database, secret key, initial admin password, configuration,
#   logs, artifacts) is private to the server.
# * config.toml is created from config.example.toml (headless browser) when missing;
#   the model is then configured in the web UI (Settings -> Model).
# * Every conversation workspace is handed to OPENMANUS_EXEC_USER, the account that
#   runs agent-written code (fixes ownership after a restore or a uid change).
#   Symlinks are never followed and hard-linked files are left alone.
set -euo pipefail

readonly APP_DIR=/app
readonly EXAMPLE_CONFIG="$APP_DIR/config/config.example.toml"

log() { printf 'openmanus-entrypoint: %s\n' "$*" >&2; }
die() {
    log "error: $*"
    exit 1
}

is_root() { [ "$(id -u)" -eq 0 ]; }

# Absolute, normalised form of a directory setting; the file system root is refused
# because permissions below it are changed.
directory_setting() {
    local name=$1 value=$2 path
    path="$(realpath -m -- "$value")"
    [ "$path" != / ] || die "$name must not be the file system root"
    printf '%s' "$path"
}

# Same defaults as the server (app/web/settings.py, app/config.py, app/logger.py).
DATA_DIR="$(directory_setting OPENMANUS_DATA_DIR "${OPENMANUS_DATA_DIR:-$APP_DIR/data}")"
CONFIG_DIR="$(directory_setting OPENMANUS_CONFIG_DIR "${OPENMANUS_CONFIG_DIR:-$APP_DIR/config}")"
LOG_DIR="${OPENMANUS_LOG_DIR-$APP_DIR/logs}" # an empty value disables file logging
if [ -n "${LOG_DIR// /}" ]; then
    LOG_DIR="$(directory_setting OPENMANUS_LOG_DIR "$LOG_DIR")"
else
    LOG_DIR=""
fi
EXEC_USER="${OPENMANUS_EXEC_USER:-}"
EXEC_USER="${EXEC_USER// /}"
WORKSPACES_DIR="$DATA_DIR/workspaces"

prepare_directories() {
    mkdir -p -- "$DATA_DIR" "$WORKSPACES_DIR" "$CONFIG_DIR"
    if [ -n "$LOG_DIR" ]; then
        mkdir -p -- "$LOG_DIR"
    fi
    is_root || return 0

    chown 0:0 -- "$DATA_DIR" "$WORKSPACES_DIR"
    # Remove group/other access below the data directory, except in the workspaces.
    find "$DATA_DIR" -mindepth 1 -path "$WORKSPACES_DIR" -prune \
        -o ! -type l -perm /077 -exec chmod go-rwx {} +
    chmod 0711 -- "$DATA_DIR" "$WORKSPACES_DIR"
    chmod 0700 -- "$CONFIG_DIR"
    if [ -n "$LOG_DIR" ]; then
        chmod 0700 -- "$LOG_DIR"
    fi
}

seed_config() {
    local target="$CONFIG_DIR/config.toml" tmp
    if [ -e "$target" ]; then
        return 0
    fi
    if [ ! -f "$EXAMPLE_CONFIG" ]; then
        log "warning: $EXAMPLE_CONFIG not found; $target was not created"
        return 0
    fi
    tmp="$(mktemp "$CONFIG_DIR/.config.toml.XXXXXX")" # mode 600
    cat -- "$EXAMPLE_CONFIG" >"$tmp"
    if ! grep -Eq '^[[:space:]]*\[browser\]' "$tmp"; then
        printf '\n# Added by the container entrypoint: there is no display in the container.\n[browser]\nheadless = true\n' >>"$tmp"
    fi
    mv -f -- "$tmp" "$target"
    log "created $target from config.example.toml; configure the model in the web UI (Settings -> Model)"
}

# Per-user directories stay root-owned and traversable only; conversation workspaces
# and their contents belong to the exec user.
fix_workspace_ownership() {
    local uid gid
    uid="$(id -u "$EXEC_USER")"
    gid="$(id -g "$EXEC_USER")"
    find "$WORKSPACES_DIR" -mindepth 1 -maxdepth 1 -type d \
        -exec chown -h 0:0 {} + -exec chmod 0711 {} +
    find "$WORKSPACES_DIR" -mindepth 2 \( -type d -o -links 1 \) \
        \( ! -user "$uid" -o ! -group "$gid" \) -exec chown -h "$uid:$gid" {} +
}

main() {
    prepare_directories
    seed_config

    if ! is_root; then
        log "running as uid $(id -u): agent code runs as this user (OPENMANUS_EXEC_USER needs root)"
    elif [ -z "$EXEC_USER" ]; then
        log "warning: OPENMANUS_EXEC_USER is empty, agent code will run as root"
    elif ! id "$EXEC_USER" >/dev/null 2>&1; then
        die "OPENMANUS_EXEC_USER='$EXEC_USER' does not exist in the image"
    else
        fix_workspace_ownership
    fi

    exec "$@"
}

main "$@"
