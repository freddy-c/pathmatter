"""Command line access to read-only vault queries."""

import argparse
import json
import sys
from pathlib import Path

from .query import QueryError, query_documents


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pathmatter")
    commands = parser.add_subparsers(dest="command", required=True)
    query_parser = commands.add_parser(
        "query", help="query Markdown documents in a vault"
    )
    query_parser.add_argument("vault", type=Path)
    source = query_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--query", help="JSON query object")
    source.add_argument("--query-file", help="JSON file, or - for standard input")
    args = parser.parse_args(argv)
    try:
        if args.query is not None:
            raw = args.query
        elif args.query_file == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(args.query_file).read_text(encoding="utf-8")
        result = query_documents(args.vault, json.loads(raw))
    except (OSError, UnicodeError, json.JSONDecodeError, QueryError) as error:
        parser.error(str(error))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
