# Deployment

## First install

1. Copy the whole project directory to the server.
2. Enter the project directory.
3. Run:

```bash
bash run.sh
```

`run.sh` will:

- re-run itself with `sudo` when needed
- sync the project to `/opt/aliMonitor`
- preserve an existing `settings.json`, forwarding config, and runtime state files
- start `aliMonitor-webui.service`
- create `/etc/aliMonitor-webui.env` with a random WebUI login password when it does not already exist
- start `aliMonitor.service` only when `settings.json` exists and passes validation

If `settings.json` is missing or invalid, open the WebUI and complete initialization there.
After saving valid settings in the WebUI, it will try to start `aliMonitor.service` automatically.

## Upgrade

Copy the new project version to the server and run:

```bash
bash upgrade.sh
```

`upgrade.sh` keeps these files from the existing `/opt/aliMonitor` install:

- `settings.json`
- `komari_state.json`
- `forward_installed.json`
- `tag_cache.json`
- `config.toml`
- `iepl_config.toml`

It also looks for runtime files in the old legacy layout `/opt/aliMonitor/deploy/aliMonitor` and migrates them into the new root layout automatically.
If both layouts exist, the root `/opt/aliMonitor/config.toml` and `/opt/aliMonitor/iepl_config.toml` files are treated as authoritative; the legacy nested copies are only used when the root files are missing.

After syncing the new code, it reinstalls the systemd units, restarts the WebUI service, and restarts `aliMonitor.service` only when the migrated `settings.json` is valid.

## Services

- `aliMonitor.service`: `python3 /opt/aliMonitor/failover_realm.py run`
- `aliMonitor-webui.service`: `python3 /opt/aliMonitor/failover_webui.py --host 0.0.0.0 --port 8080`

The WebUI listens on `0.0.0.0:8080` and requires `ALIMONITOR_WEBUI_PASSWORD`.
The install script stores it in `/etc/aliMonitor-webui.env` with mode `600`.
Read the generated password with:

```bash
sudo cat /etc/aliMonitor-webui.env
```

For production internet-facing access, put the WebUI behind HTTPS reverse proxy authentication or an SSH tunnel.

## Requirements

- Linux
- Python 3
- systemd
- `ssh`
- `scp`
- `sshpass`
- root or sudo access

## Useful commands

```bash
systemctl status aliMonitor.service --no-pager
systemctl status aliMonitor-webui.service --no-pager

journalctl -u aliMonitor.service -n 100 --no-pager
journalctl -u aliMonitor-webui.service -n 100 --no-pager
```

## Notes

- `settings.multi-domain.example.json` is the primary template.
- `settings.komari.example.json` and `config.toml` are legacy compatibility files.
- `settings.json` is intentionally not tracked in git.
- `frontend/` is the React + Vite + TypeScript source; `webui_assets/` is the committed build output that Python serves directly.
