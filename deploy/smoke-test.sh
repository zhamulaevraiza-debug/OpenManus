#!/usr/bin/env bash
# Smoke test of a built OpenManus web image. Starts a container the way
# docker-compose.yml does (dropped capabilities, no-new-privileges) on a throw-away
# volume and checks the server, the first-run setup, the privilege separation of agent
# code and the bundled tools.
#
# Usage: deploy/smoke-test.sh [--skip-browsers] [image]
#   image            image to test (default: openmanus-web:latest)
#   --skip-browsers  skip the Chromium checks (Playwright and Puppeteer)
# Needs: docker, curl, python3.
set -euo pipefail

skip_browsers=false
if [ "${1:-}" = "--skip-browsers" ]; then
    skip_browsers=true
    shift
fi
image="${1:-openmanus-web:latest}"

readonly EXEC_UID=10001
readonly FAKE_API_KEY="sk-smoke-test-not-a-real-key"
name="openmanus-smoke-$$"
volume="$name-data"
tmp_dir="$(mktemp -d)"
cookies="$tmp_dir/cookies.txt"
base_url=""

cleanup() {
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker volume rm -f "$volume" >/dev/null 2>&1 || true
    rm -rf "$tmp_dir"
}
trap cleanup EXIT

step() { printf '==> %s\n' "$*"; }
fail() {
    printf 'FAIL: %s\n' "$*" >&2
    docker logs --tail 100 "$name" >&2 2>&1 || true
    exit 1
}
in_container() { docker exec "$name" "$@"; }
as_agent() { docker exec -u agent -e HOME=/tmp "$name" "$@"; }
json_field() { python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

expect_equal() {
    local what=$1 expected=$2 actual=$3
    [ "$actual" = "$expected" ] || fail "$what: expected '$expected', got '$actual'"
}

expect_mode() {
    expect_equal "mode of $1" "$2" "$(in_container stat -c '%a' "$1")"
}

wait_until_healthy() {
    local status
    for _ in $(seq 1 120); do
        if [ "$(docker inspect -f '{{.State.Running}}' "$name")" != true ]; then
            fail "container stopped"
        fi
        status="$(docker inspect -f '{{.State.Health.Status}}' "$name")"
        if [ "$status" = healthy ]; then
            base_url="http://$(docker port "$name" 8000/tcp | head -n 1)"
            return 0
        fi
        sleep 2
    done
    fail "container not healthy after 240 s (last status: $status)"
}

step "start $image"
docker run -d --name "$name" \
    --cap-drop ALL \
    --cap-add SETUID --cap-add SETGID --cap-add CHOWN \
    --cap-add FOWNER --cap-add DAC_OVERRIDE --cap-add KILL \
    --security-opt no-new-privileges:true \
    --shm-size 1g --pids-limit 4096 \
    -e OPENMANUS_LLM_API_KEY="$FAKE_API_KEY" \
    -e OPENMANUS_LLM_MODEL=gpt-4o \
    -e OPENMANUS_LLM_BASE_URL=https://api.openai.com/v1 \
    -v "$volume:/data" -p 127.0.0.1::8000 \
    "$image" >/dev/null
wait_until_healthy

step "health endpoint and web UI"
expect_equal "health" ok "$(curl -fsS "$base_url/api/health" | json_field status)"
curl -fsS "$base_url/" | grep -q '<div id="root">' || fail "web UI index.html not served"

step "first-run setup and data permissions"
password="$(in_container cat /data/initial_admin_password.txt)"
[ -n "$password" ] || fail "initial admin password file is empty"
expect_mode /data 711
expect_mode /data/workspaces 711
expect_mode /data/config 700
expect_mode /data/config/config.toml 600
expect_mode /data/logs 700
expect_mode /data/initial_admin_password.txt 600
expect_mode /data/secret.key 600
in_container grep -q '^headless = true' /data/config/config.toml ||
    fail "seeded config.toml does not enable the headless browser"

step "login and status"
curl -fsS -c "$cookies" -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json, sys; print(json.dumps({"username": "admin", "password": sys.argv[1]}))' "$password")" \
    "$base_url/api/auth/login" >/dev/null || fail "login with the initial password failed"
expect_equal "llm_configured" True "$(curl -fsS -b "$cookies" "$base_url/api/status" | json_field llm_configured)"
user_id="$(curl -fsS -b "$cookies" "$base_url/api/auth/me" | json_field id)"

step "uploads land in a workspace owned by the exec user"
conversation_id="$(curl -fsS -b "$cookies" -H 'Content-Type: application/json' -d '{}' \
    "$base_url/api/conversations" | json_field id)"
printf 'hello\n' >"$tmp_dir/hello.txt"
curl -fsS -b "$cookies" -F "files=@$tmp_dir/hello.txt" \
    "$base_url/api/conversations/$conversation_id/files" >/dev/null || fail "upload failed"
workspace="/data/workspaces/$user_id/$conversation_id"
expect_equal "owner of the uploaded file" "$EXEC_UID" \
    "$(in_container stat -c '%u' "$workspace/uploads/hello.txt")"

step "agent code runs unprivileged with a scrubbed environment"
agent_probe='import asyncio
from app.utils.proc import run_process
result = asyncio.run(run_process("sh", "-c", "id -u; env", cwd="/tmp"))
print(result.stdout)'
probe_output="$(in_container python -c "$agent_probe")"
expect_equal "uid of agent code" "$EXEC_UID" "$(printf '%s\n' "$probe_output" | head -n 1)"
if printf '%s\n' "$probe_output" | grep -q -e "$FAKE_API_KEY" -e '^OPENMANUS_'; then
    fail "server settings leak into the environment of agent code"
fi
for private_path in /data/secret.key /data/openmanus.db /data/config/config.toml; do
    if as_agent cat "$private_path" >/dev/null 2>&1; then
        fail "the exec user can read $private_path"
    fi
done
if as_agent ls /data >/dev/null 2>&1 || as_agent ls /data/workspaces >/dev/null 2>&1; then
    fail "the exec user can list the data directory"
fi
as_agent cat "$workspace/uploads/hello.txt" >/dev/null || fail "the exec user cannot read its workspace"

step "restart: ownership repair, existing setup kept"
in_container touch "$workspace/created-by-root.txt"
in_container ln /data/secret.key "$workspace/hardlink-to-secret"
in_container sh -c 'echo "# smoke-test marker" >> /data/config/config.toml'
docker restart "$name" >/dev/null
wait_until_healthy
expect_equal "owner of a root-created workspace file" "$EXEC_UID" \
    "$(in_container stat -c '%u' "$workspace/created-by-root.txt")"
expect_equal "owner of a hard-linked file" 0 "$(in_container stat -c '%u' /data/secret.key)"
in_container rm "$workspace/hardlink-to-secret"
in_container grep -q '^# smoke-test marker' /data/config/config.toml || fail "config.toml was re-seeded"
expect_equal "initial password after restart" "$password" "$(in_container cat /data/initial_admin_password.txt)"
curl -fsS -b "$cookies" "$base_url/api/auth/me" >/dev/null || fail "session lost after restart"

step "tiktoken encodings available offline"
docker run --rm --network none --entrypoint python "$image" -c \
    'import tiktoken; [tiktoken.get_encoding(n) for n in ("cl100k_base", "o200k_base")]' ||
    fail "tiktoken encodings are not baked into the image"

if [ "$skip_browsers" = false ]; then
    step "Chromium via Playwright (browser agent)"
    in_container python -c '
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("<h1>smoke</h1>")
    assert page.inner_text("h1") == "smoke"
    browser.close()
' || fail "Playwright cannot launch Chromium"

    step "Chromium via Puppeteer as the exec user (chart renderer)"
    docker exec -u agent -e HOME=/tmp -w /app/app/tool/chart_visualization "$name" node -e '
const puppeteer = require("puppeteer");
puppeteer
  .launch({ executablePath: process.env.PUPPETEER_EXECUTABLE_PATH, args: ["--no-sandbox"] })
  .then(async (browser) => { console.log(await browser.version()); await browser.close(); })
  .catch((error) => { console.error(error); process.exit(1); });
' || fail "Puppeteer cannot launch Chromium"
fi

step "graceful stop"
docker stop -t 45 "$name" >/dev/null
# uvicorn re-raises SIGTERM after a clean shutdown (143); 137 would mean SIGKILL.
exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$name")"
case "$exit_code" in
0 | 143) ;;
*) fail "the server did not shut down gracefully (exit code $exit_code)" ;;
esac
docker logs "$name" 2>&1 | grep -q 'Application shutdown complete' ||
    fail "the server did not finish its shutdown sequence"

printf 'Smoke test passed: %s\n' "$image"
