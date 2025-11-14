"""Command-line interface for the anti-scam toolkit."""

from __future__ import annotations

import argparse
import json

from .extract import ExtractInputs, run_extraction
from .io_utils import generate_run_id, prepare_run_directories, write_json
from .logging_utils import build_logger
from .register import DEFAULT_PASSWORD, RegisterInputs, run_registration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automate registration and deposit extraction flows"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run-id", dest="run_id", help="Optional run identifier")
    common.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    register_parser = subparsers.add_parser(
        "register", help="Submit a registration form", parents=[common]
    )
    _add_common_arguments(register_parser)
    register_parser.add_argument(
        "--password", help="Password to use", default=DEFAULT_PASSWORD
    )

    extract_parser = subparsers.add_parser(
        "extract", help="Log in and extract deposit info", parents=[common]
    )
    _add_common_arguments(extract_parser)
    extract_parser.add_argument(
        "--secret", required=True, help="Password or token for login"
    )
    extract_parser.add_argument(
        "--max-steps", type=int, default=5, help="Max exploration steps after login"
    )

    return parser


def _add_common_arguments(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--url", required=True, help="Target URL (landing or portal)"
    )
    subparser.add_argument("--email", required=True, help="Email address to use")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    run_id = args.run_id or generate_run_id()
    run_paths = prepare_run_directories(run_id, args.command)
    logger = build_logger(run_paths, verbose=args.verbose)

    if args.command == "register":
        inputs = RegisterInputs(
            url=args.url,
            email=args.email,
            password=args.password,
            run_paths=run_paths,
            logger=logger,
        )
        result = run_registration(inputs)
    else:
        inputs = ExtractInputs(
            url=args.url,
            email=args.email,
            secret=args.secret,
            run_paths=run_paths,
            logger=logger,
            max_steps=args.max_steps,
        )
        result = run_extraction(inputs)

    summary_path = run_paths.base_dir / f"{args.command}.json"
    write_json(summary_path, result)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
