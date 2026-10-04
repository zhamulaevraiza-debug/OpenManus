# Deploying OpenManus Web

This step-by-step guide gets OpenManus running on your own server so you can use it from a
browser on your computer and phone. No administration experience is needed: every command is
given in full, ready to copy.

> Русская версия (основная): [DEPLOY.md](DEPLOY.md). How the system works: [ARCHITECTURE.md](ARCHITECTURE.md).

**What you get:** a chat website where a team of AI agents (the general-purpose Manus, a browser
agent, a researcher, a coder, a data analyst and a writer) works on your tasks: they search the
web, write and run code, operate websites, draw charts and produce documents. Every user has
their own conversations and files, and the site installs on a phone like an app.

## Contents

1. [What you need](#1-what-you-need)
2. [Rent a server (VPS)](#2-rent-a-server-vps)
3. [Connect to the server](#3-connect-to-the-server)
4. [Prepare the server and install Docker](#4-prepare-the-server-and-install-docker)
5. [Download and start OpenManus](#5-download-and-start-openmanus)
6. [First login](#6-first-login)
7. [Connect a language model](#7-connect-a-language-model)
8. [Domain and HTTPS](#8-domain-and-https)
9. [Install on phone and desktop](#9-install-on-phone-and-desktop)
10. [Users](#10-users)
11. [Backups](#11-backups)
12. [Updating](#12-updating)
13. [Troubleshooting](#13-troubleshooting)
14. [Security](#14-security)
15. [Settings reference](#15-settings-reference)
16. [Running without Docker (developers)](#16-running-without-docker-developers)

---

## 1. What you need

| What | Why | Minimum | Recommended |
|---|---|---|---|
| A Linux server (VPS) | runs OpenManus | 2 cores, 4 GB RAM, 30 GB disk | 4 cores, 8 GB RAM, 50 GB SSD |
| A language model API key | the agents' "brain" | OpenAI, Anthropic, OpenRouter, DeepSeek, Google Gemini, or your own model via Ollama | — |
| A domain name | HTTPS and installing on phones | optional for a trial | yes, for everyday use |

- **Ubuntu 24.04 / 22.04** or **Debian 12**; the commands below are written for them.
- The browser agents run Chromium, so more memory is needed than for a typical website. With
  2 GB RAM, building and running only work with a swap file (step 4).
- The model is called **from the server**. OpenAI, Anthropic and Google do not serve every
  country, so what matters is the server's country, not yours. If your provider is not available
  there, pick a VPS in another region or a provider that works there.

## 2. Rent a server (VPS)

A VPS is a virtual computer in a data center that runs around the clock. Any provider works:
Hetzner, DigitalOcean, Vultr, Linode, OVH and others; prices usually start at 5–10 USD/month.

When ordering, choose:

1. **Operating system:** Ubuntu 24.04 LTS.
2. **Size:** at least 2 vCPU and 4 GB RAM (8 GB is better), 30 GB disk or more.
3. **Region:** one where your model provider is available (see above).
4. **Login method:** root password or SSH key. If you don't know what an SSH key is, choose a password.

The provider then shows the server's **IP address** (for example `203.0.113.10`) and password.
Below, replace `203.0.113.10` with your IP.

> If the provider has a "cloud firewall" or "security groups", allow inbound ports **22** (SSH),
> **80** and **443** (website), and **8000** while you try it out without a domain.

## 3. Connect to the server

All commands are run in a terminal on the server over SSH.

- **Windows 10/11:** open Terminal or PowerShell and run `ssh root@203.0.113.10`
- **macOS / Linux:** the same in the Terminal app.
- **Android:** Termux or JuiceSSH.

Answer `yes` to the host fingerprint question on the first connection and type the password
(nothing is shown while typing — that is normal).

## 4. Prepare the server and install Docker

Update the system:

```bash
apt update && apt upgrade -y
```

**With less than 4 GB of RAM**, add a 4 GB swap file (otherwise the build may run out of memory):

```bash
fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

Install Docker with the official script and check it:

```bash
curl -fsSL https://get.docker.com | sh
docker --version
docker compose version
```

**Firewall (recommended):**

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

> **Note:** ports published by Docker bypass `ufw` rules. Port 8000 stays reachable from the
> internet while `.env` has `BIND_ADDRESS=0.0.0.0` (the default). Close it with
> `BIND_ADDRESS=127.0.0.1` once HTTPS is set up (step 8), not with `ufw`.

## 5. Download and start OpenManus

Download the code (use your fork's address if you use a different fork):

```bash
cd ~
git clone https://github.com/zhamulaevraiza-debug/OpenManus.git
cd OpenManus
cp .env.example .env
```

Nothing in `.env` has to be changed for a first start. Edit it later with `nano .env`
(save: `Ctrl+O`, `Enter`; exit: `Ctrl+X`); every setting is commented, and section 15 lists them all.

Build and start:

```bash
docker compose up -d --build
```

The first build takes **10–20 minutes** (Python libraries, Chromium and the web UI); later
starts take seconds. Check the state with `docker compose ps` — `STATUS` should show
`Up … (healthy)` — and follow the log with `docker compose logs -f openmanus` (`Ctrl+C` stops
following; the server keeps running).

The site is now available at **`http://203.0.113.10:8000`**.

## 6. First login

1. Open `http://203.0.113.10:8000`.
2. User name: **`admin`**.
3. The password was generated on the first start. Show it on the server:
   ```bash
   docker compose exec openmanus cat /data/initial_admin_password.txt
   ```
4. Sign in and **change the password right away**: Settings → Account → Change password.
5. Delete the file with the initial password:
   ```bash
   docker compose exec openmanus rm /data/initial_admin_password.txt
   ```

You can choose the admin password in advance with `OPENMANUS_ADMIN_PASSWORD=…` in `.env`
**before the first start**; it is only used while the database has no users.

> While the site is served over `http://`, passwords travel unencrypted. Set up HTTPS (step 8)
> for everyday use — it takes about 10 minutes.

**Forgot the password?** Reset it on the server:

```bash
docker compose exec openmanus python deploy/reset_password.py admin
```

It asks for the new password twice (or add `--generate` for a random one), re-enables the
account and signs out all of its sessions.

## 7. Connect a language model

Until a model is configured, a warning banner is shown. Open **Settings → Model** (admins only):

1. Click a provider under "Provider presets" — API type, base URL and model are filled in.
2. Paste the **API key**. It is stored only on the server and never shown again (only its last
   characters). To replace it, paste a new one; an empty field keeps the stored key.
3. Click **Save & test**. "Connected · … ms" means everything works.

| Provider | API type | Base URL | Model (example) | Get a key at |
|---|---|---|---|---|
| OpenAI | OpenAI-compatible | `https://api.openai.com/v1` | `gpt-4o` | platform.openai.com → API keys |
| Anthropic (Claude) | OpenAI-compatible | `https://api.anthropic.com/v1/` | `claude-sonnet-4-6` | console.anthropic.com → API Keys |
| OpenRouter | OpenAI-compatible | `https://openrouter.ai/api/v1` | `openai/gpt-4o` | openrouter.ai → Keys |
| DeepSeek | OpenAI-compatible | `https://api.deepseek.com/v1` | `deepseek-chat` | platform.deepseek.com → API keys |
| Google Gemini | OpenAI-compatible | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.5-flash` | aistudio.google.com → Get API key |
| Ollama (self-hosted) | Ollama | `http://host.docker.internal:11434/v1` | `qwen2.5:14b` | no key (enter `ollama`) |

Choosing a model:

- Agents call tools all the time, so the model **must support tool (function) calling**. Small
  models struggle with complex tasks; GPT-4o, Claude Sonnet, Gemini Pro or DeepSeek-V3 class
  models work well.
- Model names change — check the provider's current list; any name the API accepts can be entered.
- **Anthropic:** OpenManus sends `temperature` and forced tool selection (`tool_choice`). If the
  test or tasks fail with a 400 error mentioning these parameters, choose a model that accepts
  them, such as `claude-sonnet-4-6`.
- **OpenRouter** offers hundreds of models with one key; model names include the vendor
  (`openai/gpt-4o`, `deepseek/deepseek-chat`, …). Pick models that support Tools.
- **Image support** tells OpenManus whether the model can see images (browser screenshots,
  photos from the chat). It is guessed from the model name by default; set it explicitly if the
  guess is wrong, or configure a separate "vision model".
- **Costs:** an agent makes many model calls per task. Set a monthly spending limit with the provider.

### Ollama: a model on your own server

1. Install Ollama on the same server: `curl -fsSL https://ollama.com/install.sh | sh`
2. Pull a model that supports tools: `ollama pull qwen2.5:14b`
3. Let Ollama accept connections from the container: run `systemctl edit ollama`, add
   ```ini
   [Service]
   Environment="OLLAMA_HOST=0.0.0.0"
   ```
   then `systemctl restart ollama`.
4. Keep Ollama's port closed to the internet: `ufw deny 11434/tcp`.
5. In the model settings choose "Ollama", base URL `http://host.docker.internal:11434/v1`,
   model `qwen2.5:14b`, key `ollama`.

### Configuring the model in `.env` (optional)

The model can also be set with `OPENMANUS_LLM_*` variables in `.env`; those fields then become
read-only in the UI:

```dotenv
OPENMANUS_LLM_BASE_URL=https://api.openai.com/v1
OPENMANUS_LLM_MODEL=gpt-4o
OPENMANUS_LLM_API_KEY=sk-...
```

Apply changes to `.env` with `docker compose up -d`.

## 8. Domain and HTTPS

HTTPS encrypts passwords and conversations and is **required to install the app on phones**,
for notifications and for voice input. The bundled **Caddy** web server obtains and renews a free
Let's Encrypt certificate by itself.

1. **Buy a domain** from any registrar (Namecheap, Cloudflare, Porkbun, …) or use a subdomain of
   one you have, e.g. `chat.example.com`.
2. **Point it at the server:** in the registrar's DNS panel add an `A` record — name `chat`
   (or `@` for the bare domain), value = the server's IP. Add an `AAAA` record too if the server
   has IPv6. DNS changes take minutes to a few hours; `getent hosts chat.example.com` must print
   the server's IP. On Cloudflare, keep the record "DNS only" (grey cloud) while the certificate is issued.
3. **Edit `.env`:**
   ```dotenv
   DOMAIN=chat.example.com
   ACME_EMAIL=you@example.com
   BIND_ADDRESS=127.0.0.1
   COMPOSE_PROFILES=https
   OPENMANUS_COOKIE_SECURE=true
   ```
   `BIND_ADDRESS=127.0.0.1` closes direct access on port 8000, `COMPOSE_PROFILES=https` includes
   Caddy in every `docker compose` command, and `OPENMANUS_COOKIE_SECURE=true` keeps the login
   cookie off unencrypted connections.
4. **Apply:** `docker compose up -d`, then open `https://chat.example.com` after a minute.
   Certificate progress is in `docker compose logs -f caddy`. Ports **80** and **443** must be
   reachable from the whole internet for the certificate to be issued.

## 9. Install on phone and desktop

OpenManus is a progressive web app (PWA): once installed it opens from its own icon without the
address bar. HTTPS is required (step 8).

- **Android (Chrome):** open the site → menu **⋮** → **Install app** (or "Add to Home screen").
- **iPhone / iPad (Safari):** open the site → **Share** → **Add to Home Screen** → Add.
- **Desktop (Chrome, Edge):** the install icon at the right of the address bar.

The same hints are in Settings → About. **Notifications** (task finished, agent needs your
answer) are enabled in Settings → Appearance; on iPhone/iPad they only work for the app added to
the Home Screen (iOS 16.4+). The composer has microphone (dictation) and, on phones, camera buttons;
the browser asks for permission on first use.

## 10. Users

Admins manage accounts in **Settings → Users**: create users, grant admin rights, disable,
reset passwords and delete users (with their conversations and files). Users only see their own
conversations and files; regular users cannot access model settings or user management.

**Self-registration** is off by default. It can be enabled there ("Allow self-registration") or
with `OPENMANUS_ALLOW_REGISTRATION=true`. Only enable it when the site is reachable by people you
trust: agents run code on your server and spend your model budget (see section 14).

Load limits: at most `OPENMANUS_MAX_CONCURRENT_RUNS` (4) tasks at once on the server and
`OPENMANUS_MAX_RUNS_PER_USER` (2) per user.

## 11. Backups

All data lives in the Docker volume `openmanus_openmanus-data` (`/data` in the container):
the database (`openmanus.db`: users, conversations, task history), model settings including API
keys (`config/`), conversation files (`workspaces/`), screenshots from task history
(`artifacts/`), the session signing key (`secret.key`) and logs (`logs/`).

**Create a backup** (the server stops for about a minute so the database is captured consistently):

```bash
cd ~/OpenManus
mkdir -p ~/backups
docker compose stop openmanus
docker compose run --rm --no-deps -T --entrypoint tar openmanus -czf - -C /data . \
  > ~/backups/openmanus-$(date +%F).tar.gz
docker compose start openmanus
```

**Keep copies off the server**, e.g. on your computer: `scp root@203.0.113.10:~/backups/openmanus-2026-10-04.tar.gz .`
A backup contains API keys and password hashes — protect it like a password.

**Daily backups** at 04:00, keeping 14 days: run `crontab -e` and add (one line):

```cron
0 4 * * * cd ~/OpenManus && docker compose stop openmanus && docker compose run --rm --no-deps -T --entrypoint tar openmanus -czf - -C /data . > ~/backups/openmanus-$(date +\%F).tar.gz; docker compose start openmanus; find ~/backups -name 'openmanus-*.tar.gz' -mtime +14 -delete
```

**Restore** (replaces the current data):

```bash
cd ~/OpenManus
docker compose stop openmanus
docker compose run --rm --no-deps -T --entrypoint sh openmanus \
  -c 'find /data -mindepth 1 -delete && tar -xzf - -C /data' < ~/backups/openmanus-2026-10-04.tar.gz
docker compose start openmanus
```

File permissions are repaired automatically when the container starts. To **move to another
server**, install OpenManus there (steps 4–5), copy `.env` and a backup, and restore it.

## 12. Updating

Make a backup first (section 11), then:

```bash
cd ~/OpenManus
git pull
docker compose up -d --build
docker image prune -f
```

The new version is built while the old one keeps running; switching takes a few seconds. Tasks
running at that moment are interrupted (they end as failed with "Server restarted"). The database
schema is updated automatically. To go back, `git checkout <previous commit>` (see
`git log --oneline`) and run `docker compose up -d --build` again.

## 13. Troubleshooting

Start with `docker compose ps` and `docker compose logs --tail 200 openmanus`.

| Problem | What to check |
|---|---|
| `http://IP:8000` does not open | Is the container `healthy`? Is port 8000 open in the provider's cloud firewall? Is `BIND_ADDRESS=127.0.0.1` set in `.env`? |
| Build stops with `Killed` / exit code 137 | Out of memory: add swap (step 4). |
| Build fails downloading packages | Temporary network/mirror problem — rerun `docker compose build`. Behind a TLS-intercepting proxy: `docker build --secret id=extra_ca,src=/path/to/ca.crt -t openmanus-web:latest .` |
| Container keeps restarting | Read the log. A common cause is an invalid `.env` value (e.g. `OPENMANUS_SESSION_DAYS=abc`); `Invalid configuration` names the variable. |
| "Model not configured" banner | Configure the model (step 7); the placeholder `YOUR_API_KEY` does not count. |
| Model test fails | Key, base URL and model name; provider balance; provider availability in the server's country. For Ollama: `host.docker.internal` instead of `localhost`, and `OLLAMA_HOST=0.0.0.0`. |
| HTTP 429 when signing in | 10 failed attempts in 10 minutes — wait 10 minutes. Password reset: step 6. |
| Cannot sign in over `http://` | `OPENMANUS_COOKIE_SECURE=true` only works with HTTPS. |
| Certificate is not issued | `docker compose logs caddy`. Does the domain resolve to the server (`getent hosts domain`)? Are ports 80 and 443 open? Are `DOMAIN` and `ACME_EMAIL` set? Let's Encrypt rate-limits failures — wait an hour after several. |
| Task progress does not update live | Behind your own nginx, disable buffering for `/api/`: `proxy_buffering off;` and `proxy_read_timeout 1h;`. The bundled Caddy is already configured. |
| Upload fails with 413 | The file exceeds `OPENMANUS_MAX_UPLOAD_MB` (50 MB). Behind your own nginx also raise `client_max_body_size`. |
| Browser agent crashes or hangs | Not enough memory: lower `OPENMANUS_MAX_BROWSERS` or add RAM. |
| Disk is filling up | `docker system df`; `docker image prune -f` and `docker builder prune -f`. Conversation files are in the volume — delete conversations you no longer need. |
| A `.env` value containing `$` is cut off | Compose interpolates variables: wrap the value in single quotes (`'pa$$word'`). |
| Application logs | `docker compose logs openmanus`; detailed files: `docker compose exec openmanus ls /data/logs`. More detail: `OPENMANUS_LOG_LEVEL=DEBUG`. |

## 14. Security

**Agents execute code.** They write and run Python programs and shell commands, open websites
and download files. Therefore:

- Give access **only to people you trust**. Registration is off by default — keep it off on a
  public server.
- Any user can spend your model budget — set spending limits with the provider and, if needed,
  lower `OPENMANUS_MAX_RUNS_PER_USER` and `OPENMANUS_RUN_TIMEOUT`.

**How isolation works:**

- The server runs in a container with a minimal set of Linux capabilities (`cap_drop: ALL` plus
  six required ones), `no-new-privileges` and a process limit.
- Agent-written code runs as a separate unprivileged user `agent` (uid 10001), not as the server.
  It cannot read the database, settings, API keys, the session key or the logs — those are
  private to the server.
- Agent code gets a scrubbed environment: variables holding keys, tokens and passwords, and all
  `OPENMANUS_*` settings, are never passed to it.
- Each conversation works in its own randomly named workspace; the file tools are confined to it.
- The browser tools refuse internal addresses (`localhost`, `192.168.x.x`, `10.x.x.x`, cloud
  metadata) to prevent SSRF; `OPENMANUS_ALLOW_PRIVATE_NETWORK=true` lifts this.
- The image disables browser-use's anonymous usage reporting (`ANONYMIZED_TELEMETRY=false`).

**Limitations to be aware of:**

- Code from all conversations runs as the same `agent` user. Conversations are separated by
  random, unguessable paths rather than operating-system permissions — a safeguard against
  mistakes, not against a malicious user. Another reason to admit trusted people only.
- Agent code (Python, bash) has network access, including to addresses the server itself can
  reach. Do not deploy OpenManus where sensitive internal services are reachable, or on cloud
  machines with privileged service (IAM) roles.
- Never mount the Docker socket (`/var/run/docker.sock`) into the container — that equals root
  on the host. The CLI's `[sandbox] use_sandbox` mode is not used in the container; for an
  isolated cloud computer use the "Sandbox" agent (Daytona).

**Recommendations:** use HTTPS (step 8) with `OPENMANUS_COOKIE_SECURE=true`; change the initial
admin password and delete its file (step 6); keep the OS (`apt update && apt upgrade -y`) and
OpenManus (section 12) up to date; keep off-site backups (section 11); use SSH keys instead of passwords.

## 15. Settings reference

All settings go into `.env` (commented template: [`.env.example`](../.env.example)); apply them
with `docker compose up -d`.

**Docker Compose**

| Variable | Default | Description |
|---|---|---|
| `OPENMANUS_PORT` | `8000` | Host port of the site (without Docker: the application port) |
| `BIND_ADDRESS` | `0.0.0.0` | Address the port is published on; `127.0.0.1` = only via the HTTPS proxy |
| `DOMAIN` | — | Domain for the `https` profile |
| `ACME_EMAIL` | — | E-mail for Let's Encrypt |
| `COMPOSE_PROFILES` | — | `https` to always start Caddy |

**Accounts and sessions**

| Variable | Default | Description |
|---|---|---|
| `OPENMANUS_ADMIN_USERNAME` | `admin` | Name of the first administrator |
| `OPENMANUS_ADMIN_PASSWORD` | random | Password of the first administrator (used while there are no users) |
| `OPENMANUS_ALLOW_REGISTRATION` | `false` | Self-registration (also switchable in the UI) |
| `OPENMANUS_SECRET_KEY` | from `/data/secret.key` | Session signing key; changing it signs everyone out |
| `OPENMANUS_SESSION_DAYS` | `14` | Session lifetime in days |
| `OPENMANUS_COOKIE_SECURE` | `auto` | Secure flag of the cookie: `auto`, `true`, `false` |

**Limits**

| Variable | Default | Description |
|---|---|---|
| `OPENMANUS_MAX_CONCURRENT_RUNS` | `4` | Tasks running at once on the server |
| `OPENMANUS_MAX_RUNS_PER_USER` | `2` | Tasks running at once per user |
| `OPENMANUS_MAX_QUEUED_RUNS` | `16` | Queued tasks before new ones are refused |
| `OPENMANUS_RUN_TIMEOUT` | `3600` | Maximum task duration, seconds |
| `OPENMANUS_HUMAN_INPUT_TIMEOUT` | `1800` | How long an agent waits for your answer, seconds |
| `OPENMANUS_MAX_UPLOAD_MB` | `50` | Maximum size of an uploaded file, MB |
| `OPENMANUS_MAX_BROWSERS` | `3` | Chromium instances at once |
| `OPENMANUS_LLM_MAX_CONCURRENCY` | `8` | Concurrent requests to one model provider |

**Model** (normally set in the UI; fields set here are locked in the UI)

| Variable | Description |
|---|---|
| `OPENMANUS_LLM_API_TYPE` | empty = any OpenAI-compatible API; `azure`, `aws` (Bedrock), `ollama` |
| `OPENMANUS_LLM_BASE_URL`, `OPENMANUS_LLM_MODEL`, `OPENMANUS_LLM_API_KEY` | API address, model, key |
| `OPENMANUS_LLM_API_VERSION` | API version (Azure) |
| `OPENMANUS_LLM_MAX_TOKENS`, `OPENMANUS_LLM_TEMPERATURE` | response length and temperature |
| `OPENMANUS_LLM_SUPPORTS_IMAGES` | `true`, `false` or `auto` |
| `OPENMANUS_LLM_VISION_*` | the same for a separate vision model |

**Agent sandbox, proxy and logging**

| Variable | Default | Description |
|---|---|---|
| `OPENMANUS_ALLOW_PRIVATE_NETWORK` | `false` | Let the browser tools open internal addresses |
| `OPENMANUS_EXEC_ENV_ALLOWLIST` | — | Extra environment variables for agent code (comma-separated; secrets are never passed) |
| `OPENMANUS_TRUST_PROXY` | `true` | Trust `X-Forwarded-*` headers from proxies on private networks |
| `FORWARDED_ALLOW_IPS` | private networks | Proxy addresses to trust (uvicorn setting) |
| `OPENMANUS_CORS_ORIGINS` | — | Other web origins allowed to call the API |
| `OPENMANUS_LOG_LEVEL` | `INFO` | Console log level |
| `OPENMANUS_LOG_FILE_LEVEL` | `DEBUG` | Log file level |

**Paths and runtime** — set by the Docker image; change only when running without Docker:
`OPENMANUS_DATA_DIR` (`/data`), `OPENMANUS_CONFIG_DIR` (`/data/config`), `OPENMANUS_LOG_DIR`
(`/data/logs`; empty disables log files), `OPENMANUS_DATABASE_URL` (SQLite in the data directory),
`OPENMANUS_STATIC_DIR` (built web UI), `OPENMANUS_HOST` (`0.0.0.0`), `OPENMANUS_BROWSER_HEADLESS`
(`true`), `OPENMANUS_EXEC_USER` (`agent`), `OPENMANUS_WORKSPACE_ROOT` (workspace of the CLI scripts).

Search, browser, team and MCP server settings are changed in the UI (Settings → Search & Browser,
Agents & Team, MCP servers) and stored in `/data/config/config.toml` and `/data/config/mcp.json`.

## 16. Running without Docker (developers)

Requires Python 3.12 and Node.js 22.

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium

# web UI
(cd web && npm ci && npm run build)

# chart renderer (data analyst agent)
(cd app/tool/chart_visualization && PUPPETEER_SKIP_DOWNLOAD=1 npm ci)

python web_main.py
```

The site runs at `http://localhost:8000`; data is kept in `./data` and the initial password in
`./data/initial_admin_password.txt`. For UI development with hot reload run `cd web && npm run dev`
in a second terminal and open `http://localhost:5173`. The CLI modes (`python main.py`,
`python run_flow.py`, `python run_mcp.py`) work as before.
