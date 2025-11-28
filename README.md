# Anti-Scam Toolkit

Small CLI utility for driving a headless browser against suspicious investment portals.
The tool exposes several subcommands:

| Command      | Purpose                                                                 |
|--------------|-------------------------------------------------------------------------|
| `register`   | Find a registration form, populate it, and submit it.                   |
| `extract`    | Log in with existing credentials and run the legacy deep-dive probe.    |
| `map`        | Log in, then run the ArchivalCrawler to snapshot pages and record a site map. |
| `debug-login`| Launch a headed browser with Playwright’s inspector for manual testing. |

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

### Map (ArchivalCrawler)

```bash
python -m anti_scam map \
  --url https://example.com/dashboard \
  --email user@example.com \
  --secret Sup3rSafe! \
  --max-pages 100 \
  --max-depth 3 \
  --allow-external \
  --verbose
```

Use `--allow-external` if you want to follow outbound links; omit it to stay on the starting origin. As with other commands, `--run-id` can be supplied to write into an existing `data/<run_id>` directory.

### Debug Login

```bash
python -m anti_scam debug-login --url https://example.com/login
```

Opens Chromium with Playwright’s inspector enabled so you can manually explore or record selectors before running automation.

### Output & artefacts

- Structured JSON is printed to stdout and saved to `data/<run_id>/<command>.json`.
- Screenshots, HTML, and logs are written under `data/<run_id>/<command>/`.
- Provide `--run-id` to reuse a directory; otherwise one is generated (`YYYYMMDD-HHMMSS-xxxx`).

Logging writes both to stdout and to `data/<run_id>/anti_scam.log`.
