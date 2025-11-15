"""Capture relevant network responses during registration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from playwright.sync_api import Page

DEFAULT_KEYWORDS = (
    "register",
    "signup",
    "sign-up",
    "create-account",
    "createaccount",
    "sign_up",
)


@dataclass(slots=True)
class NetworkRecord:
    url: str
    status: int
    method: str
    resource_type: str
    body: Optional[str]


@dataclass
class NetworkCapture:
    page: Page
    keywords: Iterable[str] = DEFAULT_KEYWORDS
    max_body_chars: int = 4000
    records: List[NetworkRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._handler = None

    def __enter__(self) -> "NetworkCapture":
        def handler(response):
            url_lower = response.url.lower()
            if not any(keyword in url_lower for keyword in self.keywords):
                return
            try:
                body = response.text()
            except Exception:  # noqa: BLE001
                body = None
            if body and len(body) > self.max_body_chars:
                body = body[: self.max_body_chars] + "…"
            record = NetworkRecord(
                url=response.url,
                status=response.status,
                method=response.request.method,
                resource_type=response.request.resource_type,
                body=body,
            )
            self.records.append(record)

        self._handler = handler
        self.page.on("response", handler)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handler:
            removed = False
            for method_name in ("off", "remove_listener"):
                remover = getattr(self.page, method_name, None)
                if remover:
                    remover("response", self._handler)
                    removed = True
                    break
            if not removed:
                # Fallback: detach via context if available
                context = getattr(self.page, "context", lambda: None)()
                if context:
                    remover = getattr(context, "off", None) or getattr(
                        context, "remove_listener", None
                    )
                    if remover:
                        remover("response", self._handler)
            self._handler = None

    def dump(self, path: Path) -> Optional[Path]:
        if not self.records:
            return None
        from dataclasses import asdict

        payload = [asdict(record) for record in self.records]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
