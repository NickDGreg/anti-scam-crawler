"""Logging helpers."""

from __future__ import annotations

import logging
from pathlib import Path

from .io_utils import RunPaths


def build_logger(run_paths: RunPaths, verbose: bool = False) -> logging.Logger:
    logger_name = f"anti_scam.{run_paths.run_id}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        log_file = run_paths.base_dir / "anti_scam.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
