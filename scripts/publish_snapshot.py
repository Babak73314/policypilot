#!/usr/bin/env python3
"""Export a clean public snapshot of a commit (standard library only).

The snapshot contains the files tracked at COMMIT, minus the paths in the exclude list, written
to TARGET. It never runs git commands that change anything and never pushes. Publishing is a
separate, manual step by the owner.

The export fails (exit code 1) when:
- an excluded path would be exported;
- a file matches a secret pattern (cloud keys, API keys, private keys, tokens);
- a file contains a private marker from the exclude list;
- a Markdown link points to a file that is not in the export (for example an excluded document);
- an exported text file mentions an excluded path, outside docs/reviews/ (historical records).

Usage:
    python scripts/publish_snapshot.py --dry-run                  # check HEAD, print the file list
    python scripts/publish_snapshot.py --commit <sha> --target ../policypilot-public
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[1]
DEFAULT_EXCLUDE = REPO / "scripts" / "publish_exclude.txt"
TEXT_SUFFIXES = {
    ".md", ".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".txt", ".html", ".css", ".sh", ".conf", ".sql", ".mako", ".example",
    ".gitignore", ".gitattributes", ".dockerignore", "",
}  # fmt: skip
SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "openai_key": re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "policypilot_token": re.compile(r"\bpp_[A-Za-z0-9_-]{30,}"),
    "meta_token": re.compile(r"\bEAA[A-Za-z0-9]{40,}"),
}
MD_LINK = re.compile(r"\]\(([^)\s#]+)(#[^)]*)?\)")
HISTORICAL_PREFIXES = ("docs/reviews/",)


@dataclass
class Rules:
    include_overrides: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)

    def excluded(self, path: str) -> bool:
        if any(fnmatch.fnmatch(path, p) for p in self.include_overrides):
            return False
        return any(
            fnmatch.fnmatch(path, pattern) or PurePosixPath(path).match(pattern)
            for pattern in self.excludes
        )


def load_rules(path: Path) -> Rules:
    rules = Rules()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("marker:"):
            rules.markers.append(line.split(":", 1)[1].strip().lower())
        elif line.startswith("!"):
            rules.include_overrides.append(line[1:])
        else:
            rules.excludes.append(line)
    return rules


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True).stdout  # noqa: S603, S607


def tracked_files(commit: str) -> list[str]:
    out = git("ls-tree", "-r", "--name-only", "-z", commit).decode("utf-8")
    return sorted(p for p in out.split("\0") if p)


def is_text(path: str) -> bool:
    suffix = PurePosixPath(path).suffix.lower()
    name = PurePosixPath(path).name
    return suffix in TEXT_SUFFIXES or name in {"Dockerfile", "Makefile", "LICENSE"}


def check(commit: str, rules: Rules) -> tuple[list[str], list[str]]:
    files = tracked_files(commit)
    exported = [f for f in files if not rules.excluded(f)]
    exported_set = set(exported)
    excluded = [f for f in files if rules.excluded(f)]
    problems: list[str] = []

    for path in exported:
        if rules.excluded(path):  # defence in depth: the filter above must hold
            problems.append(f"excluded path in export: {path}")
        if not is_text(path):
            continue
        text = git("show", f"{commit}:{path}").decode("utf-8", errors="replace")
        lowered = text.lower()
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"secret pattern '{name}' in {path}")
        for marker in rules.markers:
            if marker in lowered:
                problems.append(f"private marker '{marker}' in {path}")
        if path.endswith(".md"):
            base = PurePosixPath(path).parent
            for match in MD_LINK.finditer(text):
                target = match.group(1)
                if re.match(r"^[a-z]+:", target):
                    continue
                resolved = (base / target).as_posix()
                parts: list[str] = []
                for part in resolved.split("/"):
                    if part == "..":
                        if parts:
                            parts.pop()
                    elif part not in (".", ""):
                        parts.append(part)
                normalised = "/".join(parts)
                is_dir = any(f.startswith(normalised + "/") for f in exported_set)
                if normalised not in exported_set and not is_dir:
                    problems.append(f"broken link in {path}: {target}")
        if not path.startswith(HISTORICAL_PREFIXES):
            for hidden in excluded:
                if hidden in text or (
                    PurePosixPath(hidden).name in text and hidden.endswith(".md")
                ):
                    problems.append(f"{path} mentions excluded file {hidden}")
    return exported, problems


def export(commit: str, exported: list[str], target: Path) -> None:
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"target {target} is not empty")
    for path in exported:
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(git("show", f"{commit}:{path}"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--exclude-file", type=Path, default=DEFAULT_EXCLUDE)
    parser.add_argument("--dry-run", action="store_true", help="check and list, write nothing")
    args = parser.parse_args(argv)

    commit = git("rev-parse", args.commit).decode().strip()
    rules = load_rules(args.exclude_file)
    exported, problems = check(commit, rules)

    print(f"commit {commit[:12]}: {len(exported)} files to export")
    if args.dry_run or args.target is None:
        for path in exported:
            print(f"  {path}")
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s); nothing exported", file=sys.stderr)
        return 1
    if args.dry_run or args.target is None:
        print("dry run: checks passed; nothing written")
        return 0
    export(commit, exported, args.target)
    print(f"exported to {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
