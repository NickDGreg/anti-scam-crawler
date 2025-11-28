"""Offline scanner that applies regex extraction to archived HTML files."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .data_extractor import extract_from_html
from .io_utils import write_json


@dataclass(slots=True)
class ArchiveScanInputs:
    archive_dir: Path  # directory containing mapping.json and archived HTML
    logger: logging.Logger


@dataclass(slots=True)
class ArchiveScanResult:
    archive_dir: str
    run_id: str | None
    findings_path: str
    findings: List[Dict[str, object]]
    status: str
    notes: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "archive_dir": self.archive_dir,
            "run_id": self.run_id,
            "findings_path": self.findings_path,
            "findings": self.findings,
            "status": self.status,
            "notes": self.notes,
        }


def _resolve_html_path(raw: str, archive_dir: Path) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    if not candidate.exists():
        alt = archive_dir / candidate.name
        if alt.exists():
            return alt
    return candidate


def run_archive_scan(inputs: ArchiveScanInputs) -> ArchiveScanResult:
    log = inputs.logger
    archive_dir = inputs.archive_dir
    mapping_path = archive_dir / "mapping.json"
    findings: List[Dict[str, object]] = []
    notes: List[str] = []
    status = "complete"
    run_id: str | None = None

    if not mapping_path.exists():
        msg = f"mapping.json not found in {archive_dir}"
        log.error(msg)
        notes.append(msg)
        status = "error"
        findings_path = str(archive_dir / "extraction_results.json")
        return ArchiveScanResult(
            archive_dir=str(archive_dir),
            run_id=None,
            findings_path=findings_path,
            findings=[],
            status=status,
            notes=" | ".join(notes),
        )

    log.debug("Loading mapping.json from %s", mapping_path)
    mapping = json.loads(mapping_path.read_text())
    run_id = mapping.get("run_id")
    pages = mapping.get("pages") or []

    for page in pages:
        html_rel = page.get("content_path")
        if not html_rel:
            continue
        html_path = _resolve_html_path(str(html_rel), archive_dir)
        if not html_path.exists():
            log.debug("Skipping missing HTML artifact: %s", html_path)
            continue
        try:
            html = html_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            log.debug("Unable to read %s: %s", html_path, exc)
            continue

        matches = extract_from_html(html)
        if not matches:
            continue
        for match in matches:
            findings.append(
                {
                    "type": match.type,
                    "value": match.value,
                    "context": match.context,
                    "page_path": str(html_path),
                    "source_url": page.get("url"),
                }
            )

    if not findings:
        status = "no_matches"

    findings_path = str(write_json(archive_dir / "extraction_results.json", findings))
    log.info(
        "Archived extraction results to %s (%d findings)", findings_path, len(findings)
    )

    return ArchiveScanResult(
        archive_dir=str(archive_dir),
        run_id=run_id,
        findings_path=findings_path,
        findings=findings,
        status=status,
        notes=" | ".join(notes) if notes else "",
    )
