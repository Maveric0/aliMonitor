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
- preserve an existing `settings.json` and runtime state files
- create `/opt/aliMonitor/settings.json` from `settings.multi-domain.example.json` if it is missing
- run `scripts/check.sh`
- install and enable the two systemd services

If `settings.json` had to be created from the template, the script will stop after creating it.
Edit the file and run `bash run.sh` again.

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

After syncing the new code, it runs `scripts/check.sh`, reinstalls the systemd units, and restarts both services.

## Services

- `aliMonitor.service`: `python3 /opt/aliMonitor/failover_realm.py run`
- `aliMonitor-webui.service`: `python3 /opt/aliMonitor/failover_webui.py --host 0.0.0.0 --port 8080`

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
