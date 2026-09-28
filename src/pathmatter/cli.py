"""Command line access to vault queries and validated document writes."""

import argparse
import json
import sys
from pathlib import Path

from .query import QueryError, query_documents
from .validation import validate_vault
from .writes import (
    WriteError,
    WriteValidationError,
    create_document,
    delete_document,
    preview_document,
    update_document,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pathmatter")
    commands = parser.add_subparsers(dest="command", required=True)
    query_parser = commands.add_parser(
        "query", help="query Markdown documents by path and frontmatter"
    )
    query_parser.add_argument("vault", type=Path)
    source = query_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--query", help="JSON query object")
    source.add_argument("--query-file", help="JSON file, or - for standard input")
    validate_parser = commands.add_parser("validate", help="validate Markdown files in a vault")
    validate_parser.add_argument("vault", type=Path)
    for name in ("preview", "create", "update", "delete"):
        write_parser = commands.add_parser(name, help=f"{name} a Markdown document")
        write_parser.add_argument("vault", type=Path)
        write_source = write_parser.add_mutually_exclusive_group(required=True)
        write_source.add_argument("--input", help="JSON document request")
        write_source.add_argument("--input-file", help="JSON file, or - for standard input")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            result = validate_vault(args.vault)
        elif args.command == "query":
            if args.query is not None:
                raw = args.query
            elif args.query_file == "-":
                raw = sys.stdin.read()
            else:
                raw = Path(args.query_file).read_text(encoding="utf-8")
            result = query_documents(args.vault, json.loads(raw))
        else:
            if args.input is not None:
                raw = args.input
            elif args.input_file == "-":
                raw = sys.stdin.read()
            else:
                raw = Path(args.input_file).read_text(encoding="utf-8")
            request = json.loads(raw)
            if args.command == "preview":
                result = preview_document(args.vault, request)
            elif args.command == "create":
                result = create_document(args.vault, request)
            elif args.command == "delete":
                result = delete_document(args.vault, request)
            else:
                result = update_document(args.vault, request)
    except WriteValidationError as error:
        json.dump(error.preview, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 1
    except WriteError as error:
        json.dump(
            {"diagnostics": [{"code": "write_error", "message": str(error)}]},
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 2
    except (OSError, UnicodeError, json.JSONDecodeError, QueryError) as error:
        parser.error(str(error))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if args.command == "validate" and result else 0


if __name__ == "__main__":
    raise SystemExit(main())
