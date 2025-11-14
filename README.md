# Anti-Scam Toolkit

Small CLI utility for driving a headless browser against suspicious investment portals.
The tool exposes two subcommands:

| Command   | Purpose                                                     |
|-----------|-------------------------------------------------------------|
| `register` | Find a registration form, populate it, and submit it.       |
| `extract`  | Log in with existing credentials and hunt for deposit data. |

## Runtime prerequisites

- Python ≥ 3.10 (project created with `uv`).
- Playwright browsers (`playwright install chromium`).

No Docker/LXD artefacts are included; run the tool directly on the host/container.

## Usage

Run via `python -m anti_scam <command> [options]`.

### Register

```bash
python -m anti_scam register \
  --url https://example.com \
  --email user@example.com \
  --password Sup3rSafe!
```

### Extract

```bash
python -m anti_scam extract \
  --url https://example.com/login \
  --email user@example.com \
  --secret Sup3rSafe! \
  --max-steps 5
```

### Output & artefacts

- Structured JSON is printed to stdout and saved to `data/<run_id>/<command>.json`.
- Screenshots, HTML, and logs are written under `data/<run_id>/<command>/`.
- Provide `--run-id` to reuse a directory; otherwise one is generated (`YYYYMMDD-HHMMSS-xxxx`).

Logging writes both to stdout and to `data/<run_id>/anti_scam.log`.
