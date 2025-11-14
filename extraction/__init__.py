"""Core package for the anti-scam tooling."""

from importlib import metadata

try:
    __version__ = metadata.version("anti-scam")
except metadata.PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["__version__"]
