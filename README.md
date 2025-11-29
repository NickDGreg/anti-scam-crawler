# Anti-Scam Toolkit

Small CLI utility for driving a headless browser against suspicious investment portals.
The tool exposes several subcommands:

| Command      | Purpose                                                                 |
|--------------|-------------------------------------------------------------------------|
| `register`   | Find a registration form, populate it, and submit it.                   |
| `extract`    | Log in with existing credentials and run the legacy deep-dive probe.    |
| `map`        | Log in, then run the ArchivalCrawler to snapshot pages and record a site map. |
| `scan-archive` | Run offline regex extraction against archived HTML from a mapping run. |
| `debug-login`| Launch a headed browser with Playwright’s inspector for manual testing. |

## Runtime prerequisites

- Python ≥ 3.10 (project created with `uv`).
- Playwright browsers (`playwright install chromium`).

No Docker/LXD artefacts are included; run the tool directly on the host/container.

## Usage

All commands can be launched with `python -m extraction <command> ...`. If you are using
`uv`, the recommended pattern is `uv run python -m extraction <command> ...` so that your
virtual environment is hydrated automatically.

### Register

```bash
uv run python -m extraction register \
  --url https://example.com \
  --email user@example.com \
  --password Sup3rSafe!
```

### Extract (Deep-Dive Strategist)

```bash
uv run python -m extraction extract \
  --url https://example.com/login \
  --email user@example.com \
  --secret Sup3rSafe! \
  --max-steps 5 \
  --verbose
```

This runs the legacy deep-dive strategist: it logs in with the supplied credentials,
explores deposit pages (including the new deposit-form submission flow), and writes all
artifacts to `data/<run_id>/extract/`.
### Map (ArchivalCrawler)

```bash
uv run python -m extraction map \
  --url https://example.com/dashboard \
  --email user@example.com \
  --secret Sup3rSafe! \
  --max-pages 100 \
  --max-depth 3 \
  --allow-external \
  --verbose
```

Use `--allow-external` if you want to follow outbound links; omit it to stay on the starting origin. As with other commands, `--run-id` can be supplied to write into an existing `data/<run_id>` directory.

### Scan Archive (offline regex extraction)

```bash
uv run python -m extraction scan-archive \
  --archive-dir data/20251128-130108-7f5b/map \
  --verbose
```

This reads `mapping.json` in the specified directory, scans each archived HTML with the regex extractor, and writes `extraction_results.json` alongside the artifacts. No browser or Playwright is needed for this step.

### Debug Login

```bash
uv run python -m extraction debug-login --url https://example.com/login
```

Opens Chromium with Playwright’s inspector enabled so you can manually explore or record selectors before running automation.

### Output & artefacts

- Structured JSON is printed to stdout and saved to `data/<run_id>/<command>.json`.
- Screenshots, HTML, and logs are written under `data/<run_id>/<command>/`.
- Provide `--run-id` to reuse a directory; otherwise one is generated (`YYYYMMDD-HHMMSS-xxxx`).

Logging writes both to stdout and to `data/<run_id>/anti_scam.log`.

#### Notes
A KPI for versions of this would be how many sites we visit which we successfully extract crypto addresses from. And intermediate steps of register, log in and so on.
