# aliMonitor

Multi-domain frontend failover and forwarding for Komari.

This root directory is the only project that needs to be copied to a server.
For a fresh install, copy the whole directory to any location on the server and run:

```bash
bash run.sh
```

For an in-place upgrade on a server that is already running `aliMonitor`, copy the new project version to the server and run:

```bash
bash upgrade.sh
```

## Main entry points

- `failover_realm.py`: monitor loop, failover logic, forwarding provisioning, CLI
- `failover_webui.py`: WebUI launcher
- `failover_webui_app.py`: WebUI HTTP backend
- `frontend/`: React + Vite + TypeScript WebUI source
- `webui_assets/`: committed WebUI build output served by Python

## Config files

- `settings.json`: live runtime config, intentionally gitignored
- `settings.multi-domain.example.json`: primary template used by `init`
- `settings.komari.example.json`: legacy single-domain example
- `config.toml`: legacy single-domain frontend forwarding file
- `iepl_config.toml`: IEPL forwarding template

## Deployment assets

- `run.sh`: one-click first install for Linux servers, starts WebUI even before `settings.json` exists
- `upgrade.sh`: one-click code upgrade that preserves runtime state and migrates legacy install layout
- `scripts/install.sh`: install and enable systemd services
- `scripts/check.sh`: non-destructive package checks
- `systemd/`: `aliMonitor.service` and `aliMonitor-webui.service`
- `docs/DEPLOY.md`: deployment notes

## Frontend build

The WebUI source lives under `frontend/`.
Rebuild the committed static assets with:

```bash
cd frontend
npm install
npm run build
```

The build output is written back into `webui_assets/`, which is what the Python WebUI serves at runtime.

When both `/opt/aliMonitor` and `/opt/aliMonitor/deploy/aliMonitor` exist during migration, the root-level `config.toml` and `iepl_config.toml` are treated as authoritative. The legacy nested copies are only used as a fallback when the root files are missing.

## WebUI setup flow

On a fresh install, `run.sh` starts the WebUI first.
If `settings.json` is missing or invalid, `aliMonitor.service` stays stopped and the WebUI exposes an initialization editor for `settings.json`.
After saving valid settings in the WebUI, it will try to start `aliMonitor.service` automatically.

## Runtime-generated files

These are created automatically after startup and should not be committed:

- `komari_state.json`
- `forward_installed.json`
- `tag_cache.json`

## Compatibility

The current mainline config format is `frontend_domains`.

The following files are kept only for compatibility with older single-domain setups:

- `settings.komari.example.json`
- `config.toml`
