#!/usr/bin/env python3
"""Generate Nix expressions from packages.json."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Type alias for JSON-serializable values
type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)


@dataclass
class PackageSpec:
    """Package specification from packages.json."""

    name: str
    rev: str | None
    repo: str | None
    branch: str | None
    files: JsonValue
    registry: str | None


def prefetch_git_hash(url: str, rev: str) -> tuple[str | None, str | None]:
    """Fetch git repository and calculate hash using nix-prefetch-git.

    Returns:
        A tuple of (hash, version) or (None, None) if the command fails.
        Version is in MELPA format: yyyymmdd.HHMM
    """
    try:
        result = subprocess.run(
            ["nix-prefetch-git", "--url", url, "--rev", rev, "--quiet"],
            capture_output=True,
            text=True,
            check=True,
            timeout=300,  # 5 minutes timeout
        )
        data = json.loads(result.stdout)
        hash_value = data.get("hash") or data.get("sha256")

        # Extract date and convert to MELPA format: yyyymmdd.HHMM
        date_str = data.get("date")
        version = None
        if date_str:
            # nix-prefetch-git returns date in ISO 8601 format: "2023-01-15T12:34:56+00:00"
            # Parse and convert to MELPA format: 20230115.1234
            # Note: Remove leading zeros from HHMM (e.g., 0949 -> 949)
            dt = datetime.fromisoformat(date_str.replace("+00:00", "+0000"))
            date_part = dt.strftime("%Y%m%d")
            time_part = str(int(dt.strftime("%H%M")))  # Remove leading zeros
            version = f"{date_part}.{time_part}"

        return (hash_value, version)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"Warning: Failed to prefetch {url}@{rev}: {e}", file=sys.stderr)
        return (None, None)


def parse_repo_url(repo_url: str) -> tuple[str, str, str, str] | None:
    """Parse repository URL into (fetcher, owner, repo, domain).

    Returns:
        Tuple of (fetcher, owner, repo, domain) or None if URL cannot be parsed.
        domain is used for fetchFromGitea and fetchgit.
    """
    parsed = urlparse(repo_url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")

    # Remove .git suffix if present
    if path.endswith(".git"):
        path = path[:-4]

    parts = path.split("/")

    if "github.com" in host:
        if len(parts) >= 2:
            return ("fetchFromGitHub", parts[0], parts[1], "")
    elif "gitlab.com" in host:
        if len(parts) >= 2:
            return ("fetchFromGitLab", parts[0], parts[1], "")
    elif "codeberg.org" in host:
        if len(parts) >= 2:
            return ("fetchFromGitea", parts[0], parts[1], "codeberg.org")
    elif "git.savannah.gnu.org" in host or "git.sv.gnu.org" in host:
        # Savannah uses fetchgit
        return ("fetchgit", "", path, "")
    elif "git.sr.ht" in host or "git.sourcehut.org" in host:
        # SourceHut uses fetchgit
        return ("fetchgit", "", path, "")
    elif "git.notmuchmail.org" in host:
        return ("fetchgit", "", path, "")
    else:
        # Generic git repository
        return ("fetchgit", "", path, "")

    return None


def format_files_spec(files: JsonValue) -> str:
    """Format files specification as Emacs Lisp."""
    if files is None:
        return "nil"

    def format_value(val: JsonValue) -> str:
        if val is None:
            return "nil"
        elif isinstance(val, bool):
            return "t" if val else "nil"
        elif isinstance(val, str):
            # Handle keywords (symbols starting with :)
            if val.startswith(":"):
                return val
            # Escape special characters
            escaped = val.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        elif isinstance(val, (int, float)):
            return str(val)
        elif isinstance(val, list):
            elements = " ".join(format_value(item) for item in val)
            return f"({elements})"
        elif isinstance(val, dict):
            # Property list format
            items = []
            for k, v in val.items():
                items.append(f":{k} {format_value(v)}")
            return f"({' '.join(items)})"
        else:
            return "nil"

    return format_value(files)


def generate_fetcher_expr(
    fetcher: str,
    owner: str,
    repo: str,
    rev: str,
    repo_url: str,
    domain: str = "",
    hash_value: str | None = None,
) -> str:
    """Generate Nix fetcher expression."""
    hash_str = f'"{hash_value}"' if hash_value else "lib.fakeHash"

    if fetcher == "fetchFromGitHub":
        return f"""    src = fetchFromGitHub {{
      owner = "{owner}";
      repo = "{repo}";
      rev = "{rev}";
      hash = {hash_str};
    }};"""
    elif fetcher == "fetchFromGitLab":
        return f"""    src = fetchFromGitLab {{
      owner = "{owner}";
      repo = "{repo}";
      rev = "{rev}";
      hash = {hash_str};
    }};"""
    elif fetcher == "fetchFromGitea":
        domain_str = domain or "codeberg.org"
        return f"""    src = fetchFromGitea {{
      domain = "{domain_str}";
      owner = "{owner}";
      repo = "{repo}";
      rev = "{rev}";
      hash = {hash_str};
    }};"""
    else:  # fetchgit
        return f"""    src = fetchgit {{
      url = "{repo_url}";
      rev = "{rev}";
      hash = {hash_str};
    }};"""


def generate_recipe_expr(name: str, files: JsonValue, repo_url: str | None) -> str:
    """Generate MELPA recipe expression."""
    # Determine fetcher for recipe
    fetcher = "github"  # default (symbol, not keyword)
    repo_spec = ""

    if repo_url:
        parsed = parse_repo_url(repo_url)
        if parsed:
            fetch_func, owner, repo, domain = parsed
            if fetch_func == "fetchFromGitHub":
                fetcher = "github"
                if owner and repo:
                    repo_spec = f' :repo "{owner}/{repo}"'
            elif fetch_func == "fetchFromGitLab":
                fetcher = "gitlab"
                if owner and repo:
                    repo_spec = f' :repo "{owner}/{repo}"'
            elif fetch_func == "fetchFromGitea":
                fetcher = "git"
                repo_spec = f' :url "{repo_url}"'
            else:
                fetcher = "git"
                repo_spec = f' :url "{repo_url}"'

    # Only include :files if files is not None
    files_spec = ""
    if files is not None:
        files_value = format_files_spec(files)
        files_spec = f' :files {files_value}'

    recipe_content = f'({name} :fetcher {fetcher}{repo_spec}{files_spec})'
    # Escape ${} in recipe content for Nix
    recipe_escaped = recipe_content.replace("${", "$\\{")

    return f"""    recipe = writeText "recipe" ''
      {recipe_escaped}
    '';"""


def generate_package_expr(pkg: PackageSpec, rev_lookup: dict[str, str]) -> str:
    """Generate Nix expression for a single package."""
    name = pkg.name
    rev = pkg.rev or rev_lookup.get(name)
    repo_url = pkg.repo
    files = pkg.files
    registry = pkg.registry

    if not repo_url:
        return f"  # {name}: No repository URL"

    if not rev:
        return f"  # {name}: No rev available"

    # Parse repository URL
    parsed = parse_repo_url(repo_url)
    if not parsed:
        return f"  # {name}: Cannot parse repository URL: {repo_url}"

    fetcher, owner, repo, domain = parsed

    # Prefetch hash and version
    print(f"Prefetching {name}...", file=sys.stderr)
    hash_value, melpa_version = prefetch_git_hash(repo_url, rev)

    # Determine builder based on registry
    # melpa -> melpaBuild, gnu-elpa-mirror/nongnu-elpa -> elpaBuild
    builder = "melpaBuild"  # default
    if registry in ("gnu-elpa-mirror", "nongnu-elpa"):
        builder = "elpaBuild"

    # Generate package expression
    fetcher_expr = generate_fetcher_expr(
        fetcher, owner, repo, rev, repo_url, domain, hash_value
    )

    # elpaBuild and trivialBuild don't need recipe, melpaBuild does
    if builder in ("elpaBuild", "trivialBuild"):
        recipe_section = ""
    else:
        recipe_expr = generate_recipe_expr(name, files, repo_url)
        recipe_section = f"\n{recipe_expr}\n"

    # Use MELPA version format (yyyymmdd.HHMM), fallback to commit hash
    if melpa_version:
        version = melpa_version
    else:
        # Fallback to commit hash if version is not available
        version = f"unstable-{rev[:7]}" if rev else "unstable"

    # Sanitize attribute name for Nix
    # Nix attribute names can contain alphanumerics, underscore, dash, and dot
    # Special characters like + need to be quoted
    attr_name = name
    if not name.replace("-", "").replace("_", "").replace(".", "").isalnum():
        attr_name = f'"{name}"'

    return f'''  {attr_name} = {builder} {{
    pname = "{name}";
    version = "{version}";
    commit = "{rev}";

{fetcher_expr}
{recipe_section}
    packageRequires = [ ];  # TODO: Add dependencies
  }};'''


def build_rev_lookup(packages: list[PackageSpec]) -> dict[str, str]:
    """Build a lookup table for revs by package name.

    Some packages reference the same repo with null revs, so we need
    to find the rev from another package with the same repo.
    """
    repo_to_rev: dict[str, str] = {}
    rev_lookup: dict[str, str] = {}

    # First pass: collect repo -> rev mappings
    for pkg in packages:
        if pkg.repo and pkg.rev:
            repo_to_rev[pkg.repo] = pkg.rev

    # Second pass: resolve null revs
    for pkg in packages:
        if pkg.rev:
            rev_lookup[pkg.name] = pkg.rev
        elif pkg.repo and pkg.repo in repo_to_rev:
            rev_lookup[pkg.name] = repo_to_rev[pkg.repo]

    return rev_lookup


def main() -> None:
    """Main entry point."""
    scripts_dir = Path(__file__).parent
    repo_root = scripts_dir.parent
    input_file = repo_root / "packages" / "doom-emacs-packages" / "packages.json"
    output_file = repo_root / "packages" / "doom-emacs-packages" / "generated.nix"

    # Read packages.json
    with input_file.open("r", encoding="utf-8") as f:
        packages_data: list[dict[str, Any]] = json.load(f)

    packages = [
        PackageSpec(
            name=pkg["name"],
            rev=pkg.get("rev"),
            repo=pkg.get("repo"),
            branch=pkg.get("branch"),
            files=pkg.get("files"),
            registry=pkg.get("registry"),
        )
        for pkg in packages_data
    ]

    # Build rev lookup table
    rev_lookup = build_rev_lookup(packages)

    # Generate Nix expressions
    print(f"Generating Nix expressions for {len(packages)} packages...")
    print("Calculating hashes with nix-prefetch-git (this may take a while)...", file=sys.stderr)

    package_exprs = []
    for i, pkg in enumerate(packages):
        print(f"[{i+1}/{len(packages)}] Processing {pkg.name}...", file=sys.stderr)
        expr = generate_package_expr(pkg, rev_lookup)
        package_exprs.append(expr)

    # Write output
    output = f"""# Generated by scripts/generate.py - DO NOT EDIT MANUALLY
{{ lib, newScope, fetchFromGitHub, fetchFromGitLab, fetchFromGitea, fetchgit, melpaBuild, elpaBuild, writeText, emacs, trivialBuild }}:

lib.makeScope newScope (self: {{
  # Inherit emacs and build functions from the input
  inherit emacs trivialBuild;

{chr(10).join(package_exprs)}
}})
"""

    with output_file.open("w", encoding="utf-8") as f:
        f.write(output)

    print(f"Generated {output_file}")
    print("All hashes have been calculated successfully.")


if __name__ == "__main__":
    main()
