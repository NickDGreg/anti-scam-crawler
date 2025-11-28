"""Command-line interface for the anti-scam toolkit."""

from __future__ import annotations

import argparse
import json

from .archival_crawler import MappingInputs, run_mapping
from .extract import ExtractInputs, run_extraction
from .io_utils import generate_run_id, prepare_run_directories, write_json
from .logging_utils import build_logger
from .register import DEFAULT_PASSWORD, RegisterInputs, run_registration
from .test_login import launch_login_inspector


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

    map_parser = subparsers.add_parser(
        "map",
        help="Run the archival crawler to snapshot the site after login.",
        parents=[common],
    )
    _add_common_arguments(map_parser)
    map_parser.add_argument(
        "--max-pages", type=int, default=100, help="Maximum number of pages to archive"
    )
    map_parser.add_argument(
        "--max-depth", type=int, default=3, help="Maximum BFS depth to traverse"
    )
    map_parser.add_argument(
        "--allow-external",
        action="store_true",
        help="Allow navigation outside the starting origin",
    )
    map_parser.add_argument(
        "--secret", required=True, help="Password or token for login"
    )

    debug_parser = subparsers.add_parser(
        "debug-login",
        help="Open a headed browser with Playwright pause for manual login testing",
    )
    debug_parser.add_argument("--url", required=True, help="Login page URL to open")

    return parser


def _add_common_arguments(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--url", required=True, help="Target URL (landing or portal)"
    )
    subparser.add_argument("--email", required=True, help="Email address to use")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "debug-login":
        launch_login_inspector(args.url)
        return

    run_id = getattr(args, "run_id", None) or generate_run_id()
    run_paths = prepare_run_directories(run_id, args.command)
    logger = build_logger(run_paths, verbose=getattr(args, "verbose", False))

    if args.command == "register":
        inputs = RegisterInputs(
            url=args.url,
            email=args.email,
            password=args.password,
            run_paths=run_paths,
            logger=logger,
        )
        result = run_registration(inputs)
    elif args.command == "extract":
        inputs = ExtractInputs(
            url=args.url,
            email=args.email,
            secret=args.secret,
            run_paths=run_paths,
            logger=logger,
            max_steps=args.max_steps,
        )
        result = run_extraction(inputs)
    elif args.command == "map":
        inputs = MappingInputs(
            start_url=args.url,
            email=args.email,
            secret=args.secret,
            run_paths=run_paths,
            logger=logger,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            same_origin_only=not args.allow_external,
        )
        mapping_result = run_mapping(inputs)
        result = mapping_result.to_dict()
    else:
        parser.error(f"Unknown command: {args.command}")

    summary_path = run_paths.base_dir / f"{args.command}.json"
    write_json(summary_path, result)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
