#!/usr/bin/env python3
"""Generate Nix expressions from packages.json."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
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


@dataclass
class CloneResult:
    """Result of cloning and analyzing a git repository."""

    hash_value: str | None
    version: str | None
    dependencies: list[str]  # Package names from Package-Requires


def expand_files_pattern(files: JsonValue) -> list[str]:
    """Expand files pattern to list of glob patterns.

    Args:
        files: Files specification from packages.json (can be list, dict, or None)

    Returns:
        List of glob patterns to match against
    """
    # MELPA :defaults includes both root-level and lisp/ subdirectory .el files
    defaults = ["*.el", "lisp/*.el"]

    if files is None:
        return defaults

    if isinstance(files, list):
        patterns = []
        for item in files:
            if item == ":defaults":
                patterns.extend(defaults)
            elif isinstance(item, str) and not item.startswith(":"):
                patterns.append(item)
        return patterns if patterns else defaults

    # For other cases, default to MELPA defaults
    return defaults


def find_package_files(repo_path: Path, files_spec: JsonValue) -> list[Path]:
    """Find Emacs Lisp files in repository matching the files specification.

    Args:
        repo_path: Path to cloned repository
        files_spec: Files specification from packages.json

    Returns:
        List of .el file paths that match the specification
    """
    patterns = expand_files_pattern(files_spec)
    found_files = []

    for pattern in patterns:
        if "*" in pattern:
            # Glob pattern
            found_files.extend(repo_path.glob(pattern))
        else:
            # Direct file path
            file_path = repo_path / pattern
            if file_path.exists() and file_path.suffix == ".el":
                found_files.append(file_path)

    return found_files


def parse_package_requires(el_file: Path) -> list[str]:
    """Parse Package-Requires header from an Emacs Lisp file.

    Args:
        el_file: Path to .el file

    Returns:
        List of package names (excluding 'emacs')
    """
    try:
        with el_file.open("r", encoding="utf-8", errors="ignore") as f:
            content = f.read(4096)  # Read first 4KB where headers should be

        # Look for Package-Requires header
        # Format: ;; Package-Requires: ((emacs "24.4") (dash "2.12.0") (s "1.10.0"))
        # or multi-line:
        # ;; Package-Requires: (
        # ;;     (emacs "28.1")
        # ;;     (cond-let "0.2"))

        # First, strip all ;; prefixes from comment lines to get clean content
        lines = content.split('\n')
        clean_lines = []
        for line in lines:
            stripped = re.sub(r'^\s*;;+\s*', '', line)
            clean_lines.append(stripped)
        clean_content = '\n'.join(clean_lines)

        # Now search for Package-Requires in the cleaned content
        match = re.search(
            r'Package-Requires\s*:\s*(\((?:[^()]|\([^()]*\))*\))',
            clean_content,
            re.IGNORECASE | re.DOTALL
        )

        if not match:
            print(f"  Debug: No Package-Requires found in {el_file.name}", file=sys.stderr)
            return []

        requires_str = match.group(1)
        print(f"  Debug: Found Package-Requires in {el_file.name}: {requires_str[:100]}", file=sys.stderr)

        # Parse S-expressions: (package-name "version")
        # Use character class to avoid matching parentheses in package names
        package_pattern = r'\(([a-zA-Z0-9_-]+)\s+"[^"]*"\)'
        matches = re.findall(package_pattern, requires_str)

        # Filter out 'emacs' and 'cl-lib' (built-in)
        packages = [
            pkg for pkg in matches
            if pkg.lower() not in ("emacs", "cl-lib", "cl")
        ]

        if packages:
            print(f"  Debug: Extracted dependencies from {el_file.name}: {packages}", file=sys.stderr)

        return packages

    except Exception as e:
        print(f"Warning: Failed to parse {el_file}: {e}", file=sys.stderr)
        return []


def clone_and_analyze(
    url: str, rev: str, files_spec: JsonValue, name: str
) -> CloneResult:
    """Clone repository, calculate hash, and extract dependencies.

    Args:
        url: Git repository URL
        rev: Git revision to clone
        files_spec: Files specification from packages.json
        name: Package name (for logging)

    Returns:
        CloneResult with hash, version, and dependencies
    """
    temp_dir = None
    try:
        # Create temporary directory for clone
        temp_dir = tempfile.mkdtemp(prefix=f"doom-pkg-{name}-")
        repo_path = Path(temp_dir)

        # Clone the repository
        print(f"  Cloning {url}...", file=sys.stderr)
        subprocess.run(
            ["git", "clone", "--quiet", url, str(repo_path)],
            capture_output=True,
            check=True,
            timeout=180,
        )

        # Checkout specific revision
        print(f"  Checking out {rev[:8]}...", file=sys.stderr)
        subprocess.run(
            ["git", "-C", str(repo_path), "checkout", "--quiet", rev],
            capture_output=True,
            check=True,
            timeout=60,
        )

        # Get commit date for version
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%cI"],
            capture_output=True,
            text=True,
            check=True,
        )
        date_str = result.stdout.strip()

        version = None
        if date_str:
            dt = datetime.fromisoformat(date_str.replace("+00:00", "+0000"))
            date_part = dt.strftime("%Y%m%d")
            time_part = str(int(dt.strftime("%H%M")))
            version = f"{date_part}.{time_part}"

        # Remove .git directory to get clean source for hashing
        git_dir = repo_path / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir)

        # Calculate hash using nix-hash with SRI format
        print(f"  Calculating hash...", file=sys.stderr)
        result = subprocess.run(
            ["nix-hash", "--type", "sha256", "--sri", str(repo_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        hash_value = result.stdout.strip()

        # Find and parse .el files to extract dependencies
        print(f"  Parsing dependencies...", file=sys.stderr)
        el_files = find_package_files(repo_path, files_spec)
        print(f"  Debug: Found {len(el_files)} .el files: {[f.name for f in el_files]}", file=sys.stderr)

        all_deps = set()

        for el_file in el_files:
            deps = parse_package_requires(el_file)
            all_deps.update(deps)

        print(f"  Debug: Total unique dependencies: {sorted(all_deps)}", file=sys.stderr)

        return CloneResult(
            hash_value=hash_value,
            version=version,
            dependencies=sorted(all_deps),
        )

    except subprocess.TimeoutExpired as e:
        print(f"Warning: Timeout while processing {url}@{rev}: {e}", file=sys.stderr)
        return CloneResult(None, None, [])
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to process {url}@{rev}: {e.stderr if e.stderr else e}", file=sys.stderr)
        return CloneResult(None, None, [])
    except Exception as e:
        print(f"Warning: Unexpected error processing {url}@{rev}: {e}", file=sys.stderr)
        return CloneResult(None, None, [])
    finally:
        # Clean up temporary directory
        if temp_dir and Path(temp_dir).exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


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


def sanitize_nix_attr(name: str) -> str:
    """Sanitize package name to be a valid Nix attribute name.

    Args:
        name: Package name

    Returns:
        Sanitized name (quoted if necessary)
    """
    # Nix attribute names can contain alphanumerics, underscore, dash, and dot
    if name.replace("-", "").replace("_", "").replace(".", "").isalnum():
        return name
    else:
        # Quote if it contains special characters
        return f'"{name}"'


def resolve_dependencies(
    deps: list[str], all_packages: dict[str, PackageSpec], current_package: str
) -> list[str]:
    """Resolve dependencies and return list of Nix attribute references.

    Args:
        deps: List of package names from Package-Requires
        all_packages: Dictionary of all available packages
        current_package: Name of the current package (to avoid self-reference)

    Returns:
        List of Nix attribute references (e.g., ["self.dash", "emacsPackages.s"])
    """
    resolved = []
    for dep in deps:
        # Skip self-reference to avoid infinite loops
        if dep == current_package:
            print(f"  Debug: Skipping self-reference: {dep}", file=sys.stderr)
            continue

        if dep in all_packages:
            # Use self.package-name for packages in our set
            attr_name = sanitize_nix_attr(dep)
            resolved.append(f"self.{attr_name}")
        else:
            # Use emacsPackages.package-name for external packages
            attr_name = sanitize_nix_attr(dep)
            resolved.append(f"emacsPackages.{attr_name}")
            print(f"  Info: Using emacsPackages.{attr_name} for dependency '{dep}'", file=sys.stderr)

    return resolved


def generate_package_expr(
    pkg: PackageSpec, rev_lookup: dict[str, str], all_packages: dict[str, PackageSpec]
) -> str:
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

    # Clone and analyze repository
    print(f"[{name}]", file=sys.stderr)
    clone_result = clone_and_analyze(repo_url, rev, files, name)

    hash_value = clone_result.hash_value
    melpa_version = clone_result.version

    # Always use melpaBuild for git repositories
    # elpaBuild expects tarballs from ELPA, not git clones
    builder = "melpaBuild"

    # Generate package expression
    fetcher_expr = generate_fetcher_expr(
        fetcher, owner, repo, rev, repo_url, domain, hash_value
    )

    # melpaBuild needs recipe
    recipe_expr = generate_recipe_expr(name, files, repo_url)
    recipe_section = f"\n{recipe_expr}\n"

    # Use MELPA version format (yyyymmdd.HHMM), fallback to commit hash
    if melpa_version:
        version = melpa_version
    else:
        # Fallback to commit hash if version is not available
        version = f"unstable-{rev[:7]}" if rev else "unstable"

    # Resolve dependencies (excluding self-reference)
    dep_refs = resolve_dependencies(clone_result.dependencies, all_packages, name)

    # Sanitize attribute name for Nix
    attr_name = sanitize_nix_attr(name)

    # Format dependencies
    if dep_refs:
        deps_str = " ".join(dep_refs)
        package_requires = f"[ {deps_str} ]"
    else:
        package_requires = "[ ]"

    return f'''  {attr_name} = {builder} rec {{
    pname = "{name}";
    version = "{version}";
    commit = "{rev}";

{fetcher_expr}
{recipe_section}
    packageRequires = {package_requires};
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

    # Build package name -> PackageSpec lookup
    all_packages = {pkg.name: pkg for pkg in packages}

    # Generate Nix expressions
    print(f"Generating Nix expressions for {len(packages)} packages...")
    print("Cloning repositories and extracting dependencies (this may take a while)...", file=sys.stderr)

    package_exprs = []
    for i, pkg in enumerate(packages):
        print(f"\n[{i+1}/{len(packages)}] Processing {pkg.name}...", file=sys.stderr)
        expr = generate_package_expr(pkg, rev_lookup, all_packages)
        package_exprs.append(expr)

    # Write output
    output = f"""# Generated by scripts/generate.py - DO NOT EDIT MANUALLY
{{ lib, newScope, fetchFromGitHub, fetchFromGitLab, fetchFromGitea, fetchgit, melpaBuild, elpaBuild, writeText, emacs, trivialBuild, emacsPackages }}:

lib.makeScope newScope (self: {{
  # Inherit emacs and build functions from the input
  inherit emacs trivialBuild emacsPackages;

{chr(10).join(package_exprs)}
}})
"""

    with output_file.open("w", encoding="utf-8") as f:
        f.write(output)

    print(f"Generated {output_file}")
    print("All hashes have been calculated successfully.")


if __name__ == "__main__":
    main()
