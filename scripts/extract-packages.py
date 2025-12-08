#!/usr/bin/env python3
"""Extract all Doom Emacs package information to JSON."""

from __future__ import annotations

import json
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Final
from urllib.request import urlopen

# Type alias for JSON-serializable values
type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)


class TokenType(Enum):
    """Token types for Emacs Lisp lexer."""

    LPAREN = auto()
    RPAREN = auto()
    STRING = auto()
    SYMBOL = auto()
    KEYWORD = auto()
    EOF = auto()


@dataclass
class Token:
    """A lexical token."""

    type: TokenType
    value: str
    position: int


class Lexer:
    """Lexer for Emacs Lisp."""

    text: str
    pos: int
    length: int

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.length = len(text)

    def _skip_whitespace(self) -> None:
        """Skip whitespace, comments, and quote characters."""
        while self.pos < self.length:
            # Skip whitespace
            if self.text[self.pos].isspace():
                self.pos += 1
                continue

            # Skip line comments
            if self.text[self.pos] == ";":
                while self.pos < self.length and self.text[self.pos] != "\n":
                    self.pos += 1
                continue

            # Skip quote characters (', `, #', etc.)
            if self.text[self.pos] in "'`#":
                self.pos += 1
                continue

            break

    def _read_string(self) -> str:
        """Read a string literal."""
        assert self.text[self.pos] == '"'
        start = self.pos
        self.pos += 1

        result: list[str] = []
        while self.pos < self.length:
            ch = self.text[self.pos]

            if ch == "\\" and self.pos + 1 < self.length:
                # Handle escape sequences
                self.pos += 1
                next_ch = self.text[self.pos]
                if next_ch == "n":
                    result.append("\n")
                elif next_ch == "t":
                    result.append("\t")
                elif next_ch == "r":
                    result.append("\r")
                elif next_ch == '"':
                    result.append('"')
                elif next_ch == "\\":
                    result.append("\\")
                else:
                    result.append(next_ch)
                self.pos += 1
                continue

            if ch == '"':
                self.pos += 1
                return "".join(result)

            result.append(ch)
            self.pos += 1

        raise ValueError(f"Unterminated string at position {start}")

    def _read_symbol(self) -> str:
        """Read a symbol or keyword."""
        start = self.pos
        while self.pos < self.length:
            ch = self.text[self.pos]
            if ch.isspace() or ch in "()';`#":
                break
            self.pos += 1
        return self.text[start : self.pos]

    def next_token(self) -> Token:
        """Get the next token."""
        self._skip_whitespace()

        if self.pos >= self.length:
            return Token(TokenType.EOF, "", self.pos)

        ch = self.text[self.pos]
        pos = self.pos

        if ch == "(":
            self.pos += 1
            return Token(TokenType.LPAREN, "(", pos)

        if ch == ")":
            self.pos += 1
            return Token(TokenType.RPAREN, ")", pos)

        if ch == '"':
            value = self._read_string()
            return Token(TokenType.STRING, value, pos)

        # Symbol or keyword
        value = self._read_symbol()
        if value.startswith(":"):
            return Token(TokenType.KEYWORD, value[1:], pos)
        else:
            return Token(TokenType.SYMBOL, value, pos)

    def tokenize(self) -> list[Token]:
        """Tokenize the entire input."""
        tokens: list[Token] = []
        while True:
            token = self.next_token()
            tokens.append(token)
            if token.type == TokenType.EOF:
                break
        return tokens


@dataclass
class SExp:
    """S-expression."""

    elements: list[str | SExp]  # Can be str or SExp


class Parser:
    """Parser for Emacs Lisp S-expressions."""

    tokens: list[Token]
    pos: int

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def current_token(self) -> Token:
        """Get current token."""
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return Token(TokenType.EOF, "", -1)

    def _consume(self) -> Token:
        """Consume and return current token."""
        token = self.current_token()
        self.pos += 1
        return token

    def _expect(self, token_type: TokenType) -> Token:
        """Expect a specific token type."""
        token = self.current_token()
        if token.type != token_type:
            raise ValueError(
                f"Expected {token_type}, got {token.type} at position {token.position}"
            )
        return self._consume()

    def parse_value(self) -> str | SExp:
        """Parse a single value."""
        token = self.current_token()

        if token.type == TokenType.LPAREN:
            return self.parse_list()
        elif token.type == TokenType.STRING:
            _ = self._consume()
            return token.value
        elif token.type == TokenType.SYMBOL:
            _ = self._consume()
            return token.value
        elif token.type == TokenType.KEYWORD:
            _ = self._consume()
            return f":{token.value}"
        else:
            raise ValueError(
                f"Unexpected token {token.type} at position {token.position}"
            )

    def parse_list(self) -> SExp:
        """Parse a list (S-expression)."""
        _ = self._expect(TokenType.LPAREN)

        elements: list[str | SExp] = []
        while self.current_token().type != TokenType.RPAREN:
            if self.current_token().type == TokenType.EOF:
                raise ValueError("Unexpected EOF while parsing list")
            elements.append(self.parse_value())

        _ = self._expect(TokenType.RPAREN)
        return SExp(elements)


@dataclass
class PackageInfo:
    """Package information."""

    name: str
    pin: str | None
    repo_url: str | None
    branch: str | None
    files: JsonValue
    registry: str | None  # Recipe registry source (melpa, gnu-elpa-mirror, etc.)


@dataclass
class RecipeInfo:
    """Recipe information from a recipe repository."""

    name: str
    repo_url: str | None
    branch: str | None
    files: JsonValue
    registry: str  # Recipe repository name


class RecipeRepository(ABC):
    """Abstract base class for recipe repositories."""

    @abstractmethod
    def load(self) -> None:
        """Load and cache recipe data if needed."""
        pass

    @abstractmethod
    def find_recipe(self, package_name: str) -> RecipeInfo | None:
        """Find recipe for the given package name.

        Returns RecipeInfo if found, None otherwise.
        """
        pass


class MelpaRecipeRepository(RecipeRepository):
    """MELPA recipe repository.

    Recipes are stored as individual files in recipes/ directory.
    """

    def __init__(self, repo_path: Path, registry_name: str = "melpa") -> None:
        self.repo_path = repo_path
        self.recipes_dir = repo_path / "recipes"
        self.registry_name = registry_name

    def load(self) -> None:
        """No pre-loading needed for individual recipe files."""
        pass

    @classmethod
    def initialize_into(cls, registry: "RecipeRegistry") -> None:
        """Initialize MELPA repository and add to registry."""
        try:
            repo_path = registry.clone_repo("melpa", "https://github.com/melpa/melpa")
            repo = cls(repo_path, "melpa")
            repo.load()
            registry.repositories.append(repo)
        except Exception as e:
            print(f"Warning: Could not initialize MELPA: {e}")

    def find_recipe(self, package_name: str) -> RecipeInfo | None:
        """Find MELPA recipe by package name."""
        recipe_file = self.recipes_dir / package_name
        if not recipe_file.exists():
            return None

        try:
            content = recipe_file.read_text(encoding="utf-8")
            lexer = Lexer(content)
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            sexp = parser.parse_value()

            if not isinstance(sexp, SExp) or len(sexp.elements) < 2:
                return None

            # Extract recipe properties
            recipe_plist = sexp.elements[1:]
            repo_url = None
            branch = None
            files = None

            i = 0
            while i < len(recipe_plist):
                if isinstance(recipe_plist[i], str) and recipe_plist[i].startswith(":"):
                    key = recipe_plist[i][1:]
                    if i + 1 < len(recipe_plist):
                        value = recipe_plist[i + 1]
                        if key == "repo" and isinstance(value, str):
                            # Construct full GitHub URL from :repo "owner/repo"
                            fetcher_idx = recipe_plist.index(":fetcher") if ":fetcher" in recipe_plist else -1
                            if fetcher_idx != -1 and fetcher_idx + 1 < len(recipe_plist):
                                fetcher = recipe_plist[fetcher_idx + 1]
                                if fetcher == "github":
                                    repo_url = f"https://github.com/{value}"
                                elif fetcher == "gitlab":
                                    repo_url = f"https://gitlab.com/{value}"
                        elif key == "url" and isinstance(value, str):
                            repo_url = value
                        elif key == "branch" and isinstance(value, str):
                            branch = value
                        elif key == "files":
                            files = value
                    i += 2
                else:
                    i += 1

            return RecipeInfo(
                name=package_name,
                repo_url=repo_url,
                branch=branch,
                files=files,
                registry=self.registry_name,
            )

        except Exception as e:
            print(f"Warning: Failed to parse MELPA recipe {recipe_file}: {e}")
            return None


class ElGetRecipeRepository(RecipeRepository):
    """el-get recipe repository.

    Recipes are stored as .rcp files in recipes/ directory.
    """

    def __init__(self, repo_path: Path, registry_name: str = "el-get") -> None:
        self.repo_path = repo_path
        self.recipes_dir = repo_path / "recipes"
        self.registry_name = registry_name

    def load(self) -> None:
        """No pre-loading needed for individual recipe files."""
        pass

    @classmethod
    def initialize_into(cls, registry: "RecipeRegistry") -> None:
        """Initialize el-get repository and add to registry."""
        try:
            repo_path = registry.clone_repo("el-get", "https://github.com/dimitri/el-get")
            repo = cls(repo_path, "el-get")
            repo.load()
            registry.repositories.append(repo)
        except Exception as e:
            print(f"Warning: Could not initialize el-get: {e}")

    def find_recipe(self, package_name: str) -> RecipeInfo | None:
        """Find el-get recipe by package name."""
        recipe_file = self.recipes_dir / f"{package_name}.rcp"
        if not recipe_file.exists():
            return None

        try:
            content = recipe_file.read_text(encoding="utf-8")
            lexer = Lexer(content)
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            sexp = parser.parse_value()

            if not isinstance(sexp, SExp):
                return None

            # Extract recipe properties from plist
            repo_url = None
            branch = None
            files = None

            elements = sexp.elements
            i = 0
            while i < len(elements):
                if isinstance(elements[i], str) and elements[i].startswith(":"):
                    key = elements[i][1:]
                    if i + 1 < len(elements):
                        value = elements[i + 1]
                        if key == "type" and isinstance(value, str):
                            # Determine repository URL from type
                            if value == "github" and i + 2 < len(elements):
                                # Look for :pkgname
                                pkgname_idx = next((j for j in range(i, len(elements)) if elements[j] == ":pkgname"), -1)
                                if pkgname_idx != -1 and pkgname_idx + 1 < len(elements):
                                    pkgname = elements[pkgname_idx + 1]
                                    if isinstance(pkgname, str):
                                        repo_url = f"https://github.com/{pkgname}"
                        elif key == "url" and isinstance(value, str):
                            repo_url = value
                        elif key == "branch" and isinstance(value, str):
                            branch = value
                    i += 2
                else:
                    i += 1

            return RecipeInfo(
                name=package_name,
                repo_url=repo_url,
                branch=branch,
                files=files,
                registry=self.registry_name,
            )

        except Exception as e:
            print(f"Warning: Failed to parse el-get recipe {recipe_file}: {e}")
            return None


class EmacsmirrorRecipeRepository(RecipeRepository):
    """Emacsmirror recipe repository.

    Recipes are listed in a 'mirror' file (one package name per line).
    Repository URLs follow the pattern: https://github.com/emacsmirror/{package_name}
    """

    def __init__(self, repo_path: Path, registry_name: str = "emacsmirror") -> None:
        self.repo_path = repo_path
        self.mirror_file = repo_path / "mirror"
        self.registry_name = registry_name
        self.packages: set[str] = set()

    def load(self) -> None:
        """Load package names from mirror file."""
        if not self.mirror_file.exists():
            print(f"Warning: {self.mirror_file} not found")
            return

        try:
            content = self.mirror_file.read_text(encoding="utf-8")
            self.packages = set(line.strip() for line in content.splitlines() if line.strip())
            print(f"Loaded {len(self.packages)} packages from {self.registry_name}")
        except Exception as e:
            print(f"Warning: Failed to load emacsmirror packages: {e}")

    @classmethod
    def initialize_into(cls, registry: "RecipeRegistry") -> None:
        """Initialize emacsmirror repository and add to registry."""
        try:
            repo_path = registry.clone_repo(
                "emacsmirror",
                "https://github.com/emacs-straight/emacsmirror-mirror"
            )
            repo = cls(repo_path, "emacsmirror")
            repo.load()
            registry.repositories.append(repo)
        except Exception as e:
            print(f"Warning: Could not initialize emacsmirror: {e}")

    def find_recipe(self, package_name: str) -> RecipeInfo | None:
        """Find emacsmirror recipe by package name."""
        if package_name not in self.packages:
            return None

        # Emacsmirror packages are hosted at github.com/emacsmirror/{package_name}
        repo_url = f"https://github.com/emacsmirror/{package_name}"

        return RecipeInfo(
            name=package_name,
            repo_url=repo_url,
            branch=None,
            files=None,
            registry=self.registry_name,
        )


class ArchiveContentsRecipeRepository(RecipeRepository):
    """Recipe repository that uses archive-contents format.

    Used by GNU ELPA and NonGNU ELPA. The archive-contents file contains
    all package metadata including repository URLs.
    """

    def __init__(self, archive_contents_url: str, registry_name: str) -> None:
        self.archive_contents_url = archive_contents_url
        self.registry_name = registry_name
        self.cache: dict[str, RecipeInfo] = {}

    def load(self) -> None:
        """Load and parse archive-contents file."""
        try:
            print(f"Downloading {self.registry_name} archive-contents...")
            with urlopen(self.archive_contents_url, timeout=30) as response:
                content = response.read().decode("utf-8")

            # Parse the archive-contents as S-expression
            lexer = Lexer(content)
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            sexp = parser.parse_value()

            if not isinstance(sexp, SExp) or len(sexp.elements) < 2:
                print(f"Warning: Invalid archive-contents format for {self.registry_name}")
                return

            # Skip version number (first element) and process packages
            for pkg_entry in sexp.elements[1:]:
                if not isinstance(pkg_entry, SExp) or len(pkg_entry.elements) < 2:
                    continue

                package_name = pkg_entry.elements[0]
                if not isinstance(package_name, str):
                    continue

                # Due to lexer parsing, dot notation becomes flat:
                # [name, ".", "[", version, deps, desc, type, plist, ...]
                # The plist is at index 7 (if it exists)
                if len(pkg_entry.elements) < 8:
                    continue

                props = pkg_entry.elements[7]
                if not isinstance(props, SExp):
                    continue

                # Extract :url and :commit from property list
                # Each element in props is a cons cell like (:url . "value")
                # which is parsed as [":url", ".", "value"]
                repo_url = None
                commit = None
                for prop in props.elements:
                    if isinstance(prop, SExp) and len(prop.elements) >= 3:
                        # Check if this is a cons cell with keyword at index 0
                        key = prop.elements[0]
                        if isinstance(key, str) and key.startswith(":"):
                            # Value is at index 2 (after ".")
                            value = prop.elements[2] if len(prop.elements) > 2 else None
                            if key == ":url" and isinstance(value, str):
                                repo_url = value
                            elif key == ":commit" and isinstance(value, str):
                                commit = value

                if repo_url:
                    self.cache[package_name] = RecipeInfo(
                        name=package_name,
                        repo_url=repo_url,
                        branch=commit,  # Store commit in branch field temporarily
                        files=None,
                        registry=self.registry_name,
                    )

            print(f"Loaded {len(self.cache)} packages from {self.registry_name}")

        except Exception as e:
            print(f"Warning: Failed to load archive-contents for {self.registry_name}: {e}")

    def find_recipe(self, package_name: str) -> RecipeInfo | None:
        """Find recipe from cached archive-contents."""
        return self.cache.get(package_name)

    @classmethod
    def initialize_nongnu_into(cls, registry: "RecipeRegistry") -> None:
        """Initialize NonGNU ELPA repository and add to registry."""
        try:
            repo = cls(
                "https://elpa.nongnu.org/nongnu/archive-contents",
                "nongnu-elpa"
            )
            repo.load()
            registry.repositories.append(repo)
        except Exception as e:
            print(f"Warning: Could not initialize NonGNU ELPA: {e}")

    @classmethod
    def initialize_gnu_into(cls, registry: "RecipeRegistry") -> None:
        """Initialize GNU ELPA repository and add to registry."""
        try:
            repo = cls(
                "https://elpa.gnu.org/packages/archive-contents",
                "gnu-elpa-mirror"
            )
            repo.load()
            registry.repositories.append(repo)
        except Exception as e:
            print(f"Warning: Could not initialize GNU ELPA: {e}")


class RecipeRegistry:
    """Manages recipe repositories using the RecipeRepository interface.

    Search order follows Doom Emacs priority:
    org-elpa → melpa → nongnu-elpa → gnu-elpa-mirror → el-get → emacsmirror
    """

    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = temp_dir
        self.repositories: list[RecipeRepository] = []

    def clone_repo(self, name: str, url: str, depth: int = 1) -> Path:
        """Clone a repository to the temporary directory."""
        repo_path = self.temp_dir / name
        if repo_path.exists():
            return repo_path

        print(f"Cloning {name} from {url}...")
        try:
            _ = subprocess.run(
                ["git", "clone", "--depth", str(depth), url, str(repo_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            return repo_path
        except subprocess.CalledProcessError as e:
            print(f"Warning: Failed to clone {name}: {e}")
            raise

    def initialize_repositories(self) -> None:
        """Initialize and load all recipe repositories in priority order."""
        # Priority order: melpa → nongnu-elpa → gnu-elpa-mirror → el-get → emacsmirror
        # (org-elpa is skipped as it's handled specially by Doom Emacs)

        print("Cloning recipe repositories...")

        # MELPA
        MelpaRecipeRepository.initialize_into(self)

        # NonGNU ELPA (archive-contents)
        ArchiveContentsRecipeRepository.initialize_nongnu_into(self)

        # GNU ELPA (archive-contents)
        ArchiveContentsRecipeRepository.initialize_gnu_into(self)

        # el-get
        ElGetRecipeRepository.initialize_into(self)

        # Emacsmirror
        EmacsmirrorRecipeRepository.initialize_into(self)

        print("Recipe repositories initialized successfully")

    def find_recipe(self, package_name: str) -> RecipeInfo | None:
        """Find recipe for the given package name.

        Searches repositories in priority order and returns the first match.
        """
        for repo in self.repositories:
            recipe = repo.find_recipe(package_name)
            if recipe:
                return recipe
        return None


def extract_plist_value(elements: list[str | SExp], key: str) -> str | SExp | None:
    """Extract value from plist."""
    i = 0
    while i < len(elements):
        elem = elements[i]
        if isinstance(elem, str) and elem == f":{key}":
            # Find next non-empty element
            j = i + 1
            while j < len(elements):
                next_elem = elements[j]
                # Skip empty strings
                if isinstance(next_elem, str) and next_elem == "":
                    j += 1
                    continue
                # Skip keywords (next plist key)
                if isinstance(next_elem, str) and next_elem.startswith(":"):
                    return None
                return next_elem
        i += 1
    return None


def sexp_to_json_value(value: str | SExp | list[str | SExp]) -> JsonValue:
    """Convert S-expression to JSON-serializable value."""
    if isinstance(value, SExp):
        return [sexp_to_json_value(elem) for elem in value.elements]
    elif isinstance(value, list):
        return [sexp_to_json_value(elem) for elem in value]
    else:
        return value


def parse_package_declaration(
    sexp: SExp, registry: RecipeRegistry | None = None
) -> PackageInfo | None:
    """Parse a package! declaration."""
    elements = sexp.elements

    if len(elements) < 2:
        return None

    first_elem = elements[0]
    if not isinstance(first_elem, str) or first_elem != "package!":
        return None

    name_elem = elements[1]
    if not isinstance(name_elem, str):
        return None
    name = name_elem

    # Check if this is a built-in package
    built_in_value = extract_plist_value(elements, "built-in")
    if built_in_value == "t":
        # Skip built-in packages
        return None

    # Extract plist values
    pin_value = extract_plist_value(elements, "pin")
    recipe = extract_plist_value(elements, "recipe")

    # Collect package information
    pin: str | None = pin_value if isinstance(pin_value, str) else None
    # Intermediate variables for repo_url construction
    host: str | None = None
    repo: str | None = None
    branch: str | None = None
    files: JsonValue = None
    registry_name: str | None = None

    # Parse recipe
    if recipe and isinstance(recipe, SExp):
        recipe_elements = recipe.elements
        host_value = extract_plist_value(recipe_elements, "host")
        repo_value = extract_plist_value(recipe_elements, "repo")
        branch_value = extract_plist_value(recipe_elements, "branch")
        files_value = extract_plist_value(recipe_elements, "files")

        if isinstance(host_value, str):
            host = host_value
        if isinstance(repo_value, str):
            repo = repo_value
        if isinstance(branch_value, str):
            branch = branch_value
        # Convert files to JSON-serializable format
        if files_value is not None:
            files = sexp_to_json_value(files_value)

    # Determine repository URL
    repo_url: str | None = None

    if repo:
        if host == "github":
            repo_url = f"https://github.com/{repo}"
        elif host == "gitlab":
            repo_url = f"https://gitlab.com/{repo}"
        elif host == "codeberg":
            repo_url = f"https://codeberg.org/{repo}"
        elif host == "sourcehut":
            repo_url = f"https://git.sr.ht/~{repo}"
    elif not host and not repo and registry:
        # Try to resolve from recipe registries
        recipe_info = registry.find_recipe(name)
        if recipe_info:
            # Use recipe information directly
            registry_name = recipe_info.registry
            if recipe_info.repo_url:
                repo_url = recipe_info.repo_url
            if recipe_info.branch:
                branch = recipe_info.branch
            if recipe_info.files is not None:
                files = sexp_to_json_value(recipe_info.files)

        # Fallback to emacsmirror if still not resolved
        if not repo_url:
            repo_url = f"https://github.com/emacsmirror/{name}"
            host = "github"
            repo = f"emacsmirror/{name}"
            registry_name = "emacsmirror"
    elif not host and not repo:
        # No registry available, fallback to emacsmirror
        repo_url = f"https://github.com/emacsmirror/{name}"
        host = "github"
        repo = f"emacsmirror/{name}"
        registry_name = "emacsmirror"

    # Create PackageInfo instance with collected information
    return PackageInfo(
        name=name,
        pin=pin,
        repo_url=repo_url,
        branch=branch,
        files=files,
        registry=registry_name,
    )


def find_package_declarations(
    sexp: str | SExp,
    packages: list[PackageInfo],
    registry: RecipeRegistry | None = None,
) -> None:
    """Recursively find all package! declarations in an S-expression."""
    if isinstance(sexp, SExp):
        # Check if this is a package! declaration
        if len(sexp.elements) >= 2:
            first_elem = sexp.elements[0]
            if isinstance(first_elem, str) and first_elem == "package!":
                pkg = parse_package_declaration(sexp, registry)
                if pkg:
                    packages.append(pkg)

        # Recursively search in all elements
        for elem in sexp.elements:
            find_package_declarations(elem, packages, registry)


def extract_packages_from_file(
    file_path: Path, registry: RecipeRegistry | None = None
) -> list[PackageInfo]:
    """Extract all package declarations from a file."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Warning: Failed to read {file_path}: {e}")
        return []

    packages: list[PackageInfo] = []

    try:
        # Tokenize the entire file
        lexer = Lexer(content)
        tokens = lexer.tokenize()

        # Parse all top-level S-expressions
        parser = Parser(tokens)

        while parser.current_token().type != TokenType.EOF:
            try:
                sexp = parser.parse_value()
                # Recursively find all package! declarations
                find_package_declarations(sexp, packages, registry)
            except Exception:
                # Skip unparseable expressions and continue
                # This can happen with malformed S-expressions
                if parser.pos < len(parser.tokens):
                    parser.pos += 1
                else:
                    break

    except Exception as e:
        print(f"Warning: Failed to parse {file_path}: {e}")

    return packages


def get_doom_emacs_source(repo_root: Path) -> Path:
    """Get Doom Emacs source from nix flake inputs."""
    print("Fetching Doom Emacs source from flake inputs...")
    try:
        # Get flake metadata to access lock information
        metadata_result = subprocess.run(
            ["nix", "flake", "metadata", "--json"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        metadata: JsonValue = json.loads(metadata_result.stdout)

        # Find doom-emacs input in the lock file
        if not isinstance(metadata, dict):
            raise ValueError("Invalid metadata structure")
        locks = metadata.get("locks", {})
        if not isinstance(locks, dict):
            raise ValueError("Invalid locks structure in flake metadata")
        nodes = locks.get("nodes", {})
        if not isinstance(nodes, dict):
            raise ValueError("Invalid nodes structure in flake metadata")
        doom_node = nodes.get("doom-emacs", {})
        if not isinstance(doom_node, dict):
            raise ValueError("Invalid doom-emacs node in flake metadata")
        locked = doom_node.get("locked", {})
        if not isinstance(locked, dict):
            raise ValueError("Invalid locked structure in doom-emacs node")

        # Get the narHash and other info to fetch the source
        owner = locked.get("owner")
        repo = locked.get("repo")
        rev = locked.get("rev")

        if not all([owner, repo, rev]) or not all(
            isinstance(x, str) for x in [owner, repo, rev]
        ):
            raise ValueError(
                "Could not find doom-emacs lock information in flake metadata"
            )

        print(f"Found doom-emacs: {owner}/{repo}@{rev}")

        # Fetch the source using nix
        fetch_result = subprocess.run(
            ["nix", "flake", "prefetch", "--json", f"github:{owner}/{repo}/{rev}"],
            capture_output=True,
            text=True,
            check=True,
        )
        fetch_data: JsonValue = json.loads(fetch_result.stdout)
        if not isinstance(fetch_data, dict):
            raise ValueError("Invalid fetch result structure")
        store_path = fetch_data.get("storePath")
        if not isinstance(store_path, str):
            raise ValueError("Invalid storePath in fetch result")
        doom_src = Path(store_path)

        print(f"Doom Emacs source found at: {doom_src}")
        return doom_src
    except subprocess.CalledProcessError as e:
        print(f"Error fetching Doom Emacs source from flake: {e}")
        if e.stderr:
            print(f"stderr: {e.stderr}")
        raise
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        print(f"Error parsing flake metadata: {e}")
        raise


def main() -> None:
    """Main entry point."""
    scripts_dir = Path(__file__).parent
    repo_root = scripts_dir.parent
    output_file = repo_root / "packages" / "doom-emacs-packages" / "packages.json"

    # Get Doom Emacs source from flake
    doom_src = get_doom_emacs_source(repo_root)

    # Find all packages.el files in Doom Emacs source
    packages_files = sorted(doom_src.rglob("**/packages.el"))

    print(f"Found {len(packages_files)} packages.el files")

    # Create temporary directory and clone recipe repositories
    with tempfile.TemporaryDirectory(prefix="doom-emacs-recipes-") as temp_dir:
        temp_path = Path(temp_dir)
        print(f"Using temporary directory: {temp_path}")

        registry = RecipeRegistry(temp_path)
        registry.initialize_repositories()

        all_packages: list[dict[str, JsonValue]] = []

        print("Extracting package information...")
        # First pass: collect all packages (including duplicates)
        all_declarations: dict[str, list[PackageInfo]] = {}
        for file_path in packages_files:
            packages = extract_packages_from_file(file_path, registry)
            for pkg in packages:
                if pkg.name not in all_declarations:
                    all_declarations[pkg.name] = []
                all_declarations[pkg.name].append(pkg)

        # Second pass: merge duplicate package declarations
        raw_packages: list[PackageInfo] = []
        for name, pkg_list in all_declarations.items():
            if len(pkg_list) == 1:
                raw_packages.append(pkg_list[0])
            else:
                # Merge multiple declarations - prioritize more specific info
                merged = PackageInfo(
                    name=name,
                    pin=None,
                    repo_url=None,
                    branch=None,
                    files=None,
                    registry=None,
                )

                # Collect all non-None values
                for pkg in pkg_list:
                    if pkg.pin and not merged.pin:
                        merged.pin = pkg.pin
                    if pkg.repo_url and not merged.repo_url:
                        merged.repo_url = pkg.repo_url
                    if pkg.branch and not merged.branch:
                        merged.branch = pkg.branch
                    if pkg.files is not None and merged.files is None:
                        merged.files = pkg.files
                    if pkg.registry and not merged.registry:
                        merged.registry = pkg.registry

                raw_packages.append(merged)

        # Third pass: for packages without pin, inherit from same-repo packages
        # Group packages by repo_url
        repo_groups: dict[str, list[PackageInfo]] = {}
        for pkg in raw_packages:
            if pkg.repo_url:
                if pkg.repo_url not in repo_groups:
                    repo_groups[pkg.repo_url] = []
                repo_groups[pkg.repo_url].append(pkg)

        # Find pin for each repo group
        repo_pins: dict[str, str | None] = {}
        for repo_url, pkgs in repo_groups.items():
            # Find any package with a pin in this repo
            pin = None
            for pkg in pkgs:
                if pkg.pin:
                    pin = pkg.pin
                    break
            # If no pin found, try branch
            if not pin:
                for pkg in pkgs:
                    if pkg.branch:
                        pin = pkg.branch
                        break
            repo_pins[repo_url] = pin

        # Fourth pass: create final package list with inherited pins
        for pkg in raw_packages:
            rev = pkg.pin if pkg.pin else pkg.branch
            # If no rev, try to inherit from same repo
            if not rev and pkg.repo_url and pkg.repo_url in repo_pins:
                rev = repo_pins[pkg.repo_url]

            all_packages.append(
                {
                    "name": pkg.name,
                    "rev": rev,
                    "repo": pkg.repo_url,
                    "files": pkg.files,
                    "registry": pkg.registry,
                }
            )

        # Write JSON output
        with output_file.open("w", encoding="utf-8") as f:
            json.dump(all_packages, f, indent=2, ensure_ascii=False)

        print(
            f"Package information extracted to {output_file} ({len(all_packages)} packages)"
        )


if __name__ == "__main__":
    main()
