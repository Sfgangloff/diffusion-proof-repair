"""Command-line entry point for proofrepair.

Phase 0 ships `compile` and `verify` as fully functional. `corrupt`
and `sweep` are intentionally stubbed — they belong to phases 2 and 6
respectively — but the surface is wired so the docstring help reflects
the planned commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from proofrepair.lean import compile as lean_compile
from proofrepair.lean.cache import CompileCache


def _add_compile_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "compile",
        help="Compile a single Lean file and print a structured result.",
    )
    p.add_argument("file", help="Path to a .lean file")
    p.add_argument(
        "--timeout", type=float, default=30.0, help="Per-file timeout in seconds"
    )
    p.add_argument(
        "--lake",
        action="store_true",
        help="Invoke `lake env lean` instead of bare `lean` (use when the file imports project deps)",
    )
    p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the CompileResult as JSON",
    )
    p.add_argument(
        "--cache",
        action="store_true",
        help="Read and write through the on-disk compile cache",
    )
    p.set_defaults(func=_cmd_compile)


def _add_verify_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "verify",
        help="Verify a Lean file compiles. Exit 0 on success, 1 on errors.",
    )
    p.add_argument("file", help="Path to a .lean file")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--lake", action="store_true")
    p.set_defaults(func=_cmd_verify)


def _add_corrupt_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "corrupt",
        help="(Phase 2) Apply a typed corruption operator to a Lean file.",
    )
    p.add_argument("file")
    p.add_argument("--rho", type=float, required=True)
    p.add_argument("--op", required=True, help="One of token_mask|token_replace|line_delete|tactic_replace|identifier_rename")
    p.add_argument("--locality", choices=["scattered", "clustered"], default="scattered")
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=_cmd_corrupt)


def _add_sweep_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "sweep",
        help="(Phase 6) Run a configured ρ-sweep across files / models / corruptions.",
    )
    p.add_argument("config", help="Path to a YAML sweep config")
    p.set_defaults(func=_cmd_sweep)


def _cmd_compile(args: argparse.Namespace) -> int:
    path = Path(args.file)
    cache = CompileCache() if args.cache else None
    result = None
    if cache is not None:
        result = cache.lookup_path(path)
    if result is None:
        result = lean_compile(path, timeout=args.timeout, use_lake=args.lake)
        if cache is not None:
            cache.store_path(path, result)

    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        status = "ok" if result.ok else "FAIL"
        print(f"[{status}] {path}  ({result.compile_time_s:.2f}s, {len(result.errors)} diag)")
        for e in result.errors:
            print(f"  {e.severity:7s} {e.file}:{e.line}:{e.column}: {e.message.splitlines()[0]}")
    return 0 if result.ok else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    result = lean_compile(args.file, timeout=args.timeout, use_lake=args.lake)
    if result.ok:
        print(f"ok  {args.file}  ({result.compile_time_s:.2f}s)")
        return 0
    print(f"FAIL {args.file}  ({result.compile_time_s:.2f}s, {len(result.errors)} errors)", file=sys.stderr)
    for e in result.errors[:5]:
        print(f"  {e.severity}: {e.file}:{e.line}:{e.column}: {e.message.splitlines()[0]}", file=sys.stderr)
    return 1


def _cmd_corrupt(args: argparse.Namespace) -> int:
    print(
        "corrupt: not yet implemented (Phase 2). "
        f"Args were: file={args.file} op={args.op} rho={args.rho} "
        f"locality={args.locality} seed={args.seed}",
        file=sys.stderr,
    )
    return 2


def _cmd_sweep(args: argparse.Namespace) -> int:
    print(
        f"sweep: not yet implemented (Phase 6). Config={args.config}",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proofrepair",
        description="Diffusion / EBM proof repair on Lean 4 — CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_compile_parser(sub)
    _add_verify_parser(sub)
    _add_corrupt_parser(sub)
    _add_sweep_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
