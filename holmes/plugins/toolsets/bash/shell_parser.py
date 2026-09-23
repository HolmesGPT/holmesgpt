"""
Shell command parsing for bash toolset validation, built on tree-sitter-bash.

`parse_command()` reports, for every simple command (including commands nested
in `$(...)`, backticks and `<(...)`):

- segments: its source text;
- command_argvs: its argv after quote removal (expansions kept literally);
- command_arg_dynamic: whether an argument contains `$VAR`, `$(...)`, etc.;
- write_redirect_targets: output redirections to real files.

It supports simple commands joined by `|`, `&&`, `||`, `;`, `&` and newlines,
with redirects and nested substitutions. Anything else (loops, conditionals,
heredocs, `[[ ]]`, brace expansion, ...) raises ShellParseError, and so does input that
tree-sitter-bash is known to parse differently from bash. validate_command
treats ShellParseError as "requires approval" after running its raw-text deny
checks.
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple

import tree_sitter_bash
from tree_sitter import Language, Node, Parser

from holmes.plugins.toolsets.bash.argv_utils import is_benign_redirect_target

_PARSER = Parser(Language(tree_sitter_bash.language()))


class ShellParseError(Exception):
    """The command cannot be parsed with enough confidence to validate it."""


_CONTAINERS = frozenset({"program", "list", "pipeline", "negated_command"})
_WORDS = frozenset(
    {"word", "string", "raw_string", "ansi_c_string", "concatenation", "number",
     "simple_expansion", "expansion", "command_substitution", "process_substitution"}
)
_DYNAMIC = frozenset({"simple_expansion", "expansion", "command_substitution", "process_substitution"})
_SUBSTITUTIONS = frozenset({"command_substitution", "process_substitution"})
_WRITE_OPERATORS = frozenset({">", ">>", ">|", "&>", "&>>", ">&"})
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Unquoted brace expansion: `{a,b}` or a sequence `{1..3}` / `{a..z}`.
_BRACE_EXPANSION = re.compile(
    r"\{[^{}]*,[^{}]*\}|\{(-?\d+\.\.-?\d+|[A-Za-z]\.\.[A-Za-z])(\.\.-?\d+)?\}"
)
# Leaves whose text is literal to bash, so `$(` in them is not a substitution.
_LITERAL_LEAVES = frozenset({"raw_string", "ansi_c_string", "comment"})

_ANSI_C = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r",
           "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


@dataclass
class ParsedCommand:
    command: str
    segments: List[str] = field(default_factory=list)
    contains_compound_command: bool = False  # always False: compounds are refused
    command_argvs: List[List[str]] = field(default_factory=list)
    command_arg_dynamic: List[bool] = field(default_factory=list)
    write_redirect_targets: List[str] = field(default_factory=list)


def _unescape(s: str, escapable: str = "") -> str:
    """Backslash removal. Unquoted (escapable=""): `\\x` -> `x`. In double
    quotes, only the characters in `escapable` are escaped."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and (not escapable or s[i + 1] in escapable):
            if s[i + 1] != "\n":
                out.append(s[i + 1])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _decode_ansi_c(s: str) -> str:
    """Body of a `$'...'` string. Numeric escapes are refused rather than decoded."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            if s[i + 1] not in _ANSI_C:
                raise ShellParseError("unsupported escape in $'...'")
            out.append(_ANSI_C[s[i + 1]])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


class _Visitor:
    def __init__(self, command: str):
        self.src = command.encode("utf-8")
        self.result = ParsedCommand(command=command)

    def text(self, n: Node) -> str:
        return self.src[n.start_byte : n.end_byte].decode("utf-8", errors="surrogateescape")

    def gap(self, a: Node, b: Node) -> bytes:
        return self.src[a.end_byte : b.start_byte].replace(b"\\\n", b"")

    # -- words ---------------------------------------------------------------

    def unquote(self, n: Node) -> str:
        t = n.type
        if t in ("word", "number"):
            return _unescape(self.text(n))
        if t == "raw_string":
            return self.text(n)[1:-1]
        if t == "ansi_c_string":
            return _decode_ansi_c(self.text(n)[2:-1])
        if t == "string":
            out, pos = [], n.start_byte + 1
            for c in n.named_children:
                if c.type == "string_content":
                    continue
                if c.type not in _DYNAMIC:
                    raise ShellParseError(f"unsupported syntax in string: {c.type}")
                out.append(_unescape(self.src[pos : c.start_byte].decode(errors="surrogateescape"), '"\\$`\n'))
                out.append(self.text(c))
                pos = c.end_byte
            out.append(_unescape(self.src[pos : n.end_byte - 1].decode(errors="surrogateescape"), '"\\$`\n'))
            return "".join(out)
        if t in _DYNAMIC:
            return self.text(n)
        if t == "concatenation":
            return "".join(self.unquote(self.check_word(c)) if c.is_named else _unescape(self.text(c))
                           for c in n.children)
        raise ShellParseError(f"unsupported syntax: {t}")

    def check_word(self, n: Node) -> Node:
        if n.type not in _WORDS:
            raise ShellParseError(f"unsupported syntax: {n.type}")
        return n

    def is_dynamic(self, n: Node) -> bool:
        if n.type in _DYNAMIC:
            return True
        return n.type not in ("raw_string", "ansi_c_string") and any(self.is_dynamic(c) for c in n.named_children)

    # -- checks for known tree-sitter-bash misparses -------------------------

    def check(self, root: Node) -> None:  # noqa: C901
        covered = bytearray(len(self.src))
        stack = [root]
        while stack:
            x = stack.pop()
            stack.extend(x.children)
            raw = self.src[x.start_byte : x.end_byte]
            if x.child_count == 0 or x.type in ("string_content", "comment"):
                covered[x.start_byte : x.end_byte] = b"\x01" * len(raw)
            kids = x.children
            if x.type in ("command", "redirected_statement", "file_redirect", "concatenation",
                          "simple_expansion", "negated_command"):
                for a, b in zip(kids, kids[1:]):
                    # `ls<NL>\<NL>rm x` is ONE command to tree-sitter
                    if b"\n" in self.gap(a, b):
                        raise ShellParseError("command continues across a newline")
                    # nodes with nothing between them are one shell word that
                    # tree-sitter split in two (`a\<NL>b`, `-x=1`, `>f'|'x`)
                    if (a.is_named and b.is_named and self.gap(a, b) == b""
                            and b.type != "file_redirect" and x.type not in ("concatenation", "simple_expansion")):
                        raise ShellParseError("ambiguous word boundary")
                    if x.type in ("concatenation", "simple_expansion") and a.end_byte != b.start_byte:
                        raise ShellParseError("ambiguous word boundary")
            if x.type == "command" and any(not c.is_named for c in kids):
                raise ShellParseError("unexpected token in command")  # e.g. a lone `$`
            if x.type == "command_name" and self.text(x) in ("time", "coproc"):
                raise ShellParseError(f"unsupported syntax: {self.text(x)}")
            if x.type == "negated_command" and not raw[1:2].isspace():
                raise ShellParseError("'!' not followed by a blank")
            # e.g. `${HOME#$(cmd)}`: tree-sitter keeps the pattern as a `regex`
            # leaf, but bash runs the substitution
            if x.is_named and x.child_count == 0 and x.type not in _LITERAL_LEAVES \
                    and re.search(rb"`|\$[({\[]", raw):
                raise ShellParseError("unparsed substitution inside a word")
            if x.type == "comment":
                self.check_comment_start(x)
            if x.type == "word" and re.search(rb"(?<!\\)\s", raw):
                raise ShellParseError("ambiguous word boundary")
            if x.type in ("word", "concatenation", "variable_assignment") and b"\\\n" in raw:
                raise ShellParseError("line continuation inside a word")
            if x.type == "command_substitution" and raw[:1] == b"`" and b"\\" in raw:
                raise ShellParseError("backslash inside backticks")
            if x.type in ("raw_string", "ansi_c_string", "string"):
                self.check_quote_extent(x, raw)
        # tree-sitter can silently drop text (e.g. a standalone `\ `)
        uncovered = bytes(b for b, c in zip(self.src.replace(b"\\\n", b"  "), covered) if not c)
        if uncovered.strip():
            raise ShellParseError("part of the command was not understood")

    def check_comment_start(self, x: Node) -> None:
        """`#` starts a comment only at the start of a word. tree-sitter also
        treats it as one after `\\<NL>` (which bash deletes, gluing the `#`
        to the previous word: `ls -la\\<NL>#;touch x` runs `touch x`) and
        after an escaped blank."""
        before = self.src[: x.start_byte]
        if not before:
            return
        if before.endswith(b"\\\n"):
            raise ShellParseError("comment after a line continuation")
        if before[-1:] not in (b" ", b"\t", b"\n", b";", b"&", b"|", b"("):
            raise ShellParseError("'#' inside a word")
        backslashes = len(before[:-1]) - len(before[:-1].rstrip(b"\\"))
        if backslashes % 2:
            raise ShellParseError("'#' after an escaped character")

    def check_braces(self, n: Node) -> None:
        """Bash brace-expands `{a,b}` and `{1..3}` into several words, so the
        argv we would check (`find . {-exec,} ...`) is not the one bash runs."""
        def unquoted(c: Node) -> str:
            if c.type == "word" or not c.is_named:
                return re.sub(r"\\.", "__", self.text(c), flags=re.S)
            if c.type == "concatenation":
                return "".join(unquoted(k) for k in c.children)
            return "_"  # quoted text and expansions never brace-expand
        if _BRACE_EXPANSION.search(unquoted(n)):
            raise ShellParseError("unsupported syntax: brace expansion")

    @staticmethod
    def check_quote_extent(x: Node, t: bytes) -> None:
        """Re-lex a quoted string with bash's rules: it must end where tree-sitter
        says (it mis-lexes e.g. `$'\\\\'` and swallows the following commands)."""
        if x.type == "raw_string":
            ok = t[-1:] == b"'" and b"'" not in t[1:-1]
        else:
            skip = [(c.start_byte - x.start_byte, c.end_byte - x.start_byte)
                    for c in x.named_children if c.type in _DYNAMIC]
            quote = b"'" if x.type == "ansi_c_string" else b'"'
            i, close = (2 if x.type == "ansi_c_string" else 1), None
            while i < len(t):
                end = next((b for a, b in skip if a <= i < b), None)
                if end is not None:
                    i = end
                elif t[i : i + 1] == b"\\":
                    i += 2
                elif t[i : i + 1] == quote:
                    close = i
                    break
                else:
                    i += 1
            ok = close == len(t) - 1
        if not ok:
            raise ShellParseError("ambiguous quoting")

    # -- traversal -----------------------------------------------------------

    def visit(self, n: Node, redirects: Tuple[Node, ...] = ()) -> None:
        t = n.type
        if t == "comment":
            return
        if t == "command":
            self.visit_command(n, redirects)
        elif t in _CONTAINERS:
            kids = [c for c in n.named_children if c.type != "comment"]
            for i, c in enumerate(kids):
                # redirects after `a | b` belong to the last command, `b`
                self.visit(c, redirects if i == len(kids) - 1 else ())
        elif t == "redirected_statement" and n.child_by_field_name("body") is not None:
            body = n.child_by_field_name("body")
            own = tuple(c for c in n.named_children if c.id != body.id and c.type != "comment")
            self.visit(body, own + redirects)
        else:
            raise ShellParseError(f"unsupported syntax: {t}")

    def visit_redirect(self, r: Node) -> None:
        if r.type != "file_redirect":
            raise ShellParseError(f"unsupported syntax: {r.type}")
        op = next((c.type for c in r.children if not c.is_named), None)
        targets = [c for c in r.named_children if c.type != "file_descriptor"]
        if len(targets) != 1 or op in (None, ">&-", "<&-", "<>"):
            # tree-sitter parses the words after a target (`2>/dev/null -exec
            # rm {} \;`) as more targets; bash passes them to the command
            raise ShellParseError("unsupported redirect syntax")
        target = self.check_word(targets[0])
        self.check_braces(target)
        self.visit_nested(target)
        if op in _WRITE_OPERATORS and not (op == ">&" and target.type == "number"):
            path = self.unquote(target)
            if not is_benign_redirect_target(path):
                self.result.write_redirect_targets.append(path)

    def visit_command(self, n: Node, outer_redirects: Tuple[Node, ...]) -> None:
        self.result.segments.append(self.text(n).strip())
        words: List[Node] = []
        assignments: List[Node] = []
        redirects: List[Node] = list(outer_redirects)
        for c in n.named_children:
            if c.type == "variable_assignment":
                name = c.child_by_field_name("name")
                if words or name is None or not _NAME.match(self.text(name)):
                    raise ShellParseError("unsupported assignment syntax")
                assignments.append(c)  # `A=1 cmd`: environment, not argv
            elif c.type == "command_name":
                words.append(self.check_word(c.named_children[0]))
            elif c.type.endswith("redirect"):
                redirects.append(c)
            else:
                words.append(self.check_word(c))
        if not words:
            raise ShellParseError("unsupported syntax: command without a name")
        for w in words:
            self.check_braces(w)
        self.result.command_argvs.append([self.unquote(w) for w in words])
        self.result.command_arg_dynamic.append(any(self.is_dynamic(w) for w in words[1:]))
        for x in words + assignments:
            self.visit_nested(x)
        for r in redirects:
            self.visit_redirect(r)

    def visit_nested(self, n: Node) -> None:
        """Visit commands inside `$(...)`, backticks and `<(...)`, wherever
        they are in the word (including `${a[$(...)]}` subscripts)."""
        if n.type in _SUBSTITUTIONS:
            for c in n.named_children:
                self.visit(c)
            return
        for c in n.named_children:
            if c.type == "arithmetic_expansion":
                raise ShellParseError("unsupported syntax: arithmetic expansion")
            if c.type not in _LITERAL_LEAVES:
                self.visit_nested(c)


def parse_command(command: str) -> ParsedCommand:
    """Parse a shell command for validation.

    Raises:
        ShellParseError: If the command is invalid, uses syntax that is not
            supported, or matches a pattern tree-sitter-bash is known to misparse.
    """
    if b"\r" in command.encode() or re.search(r"\{[A-Za-z_]\w*\}[<>]", command):
        raise ShellParseError("unsupported syntax")
    visitor = _Visitor(command)
    root = _PARSER.parse(visitor.src).root_node
    if root.has_error:
        raise ShellParseError("invalid or unsupported shell syntax")
    try:
        visitor.check(root)
        visitor.visit(root)
    except RecursionError:
        raise ShellParseError("command is nested too deeply")
    return visitor.result


def scan_for_deny_checks(command: str) -> Tuple[List[List[str]], List[str]]:
    """Best-effort (argvs, write redirect targets) for a command that
    parse_command() refused. The tree may be wrong, so callers may only use the
    result to DENY a command, never to allow one."""
    visitor = _Visitor(command)
    argvs: List[List[str]] = []
    targets: List[str] = []

    def text(n: Node) -> str:
        try:
            return visitor.unquote(n)
        except ShellParseError:
            return visitor.text(n)

    def redirect_parts(r: Node) -> Tuple[str, List[Node]]:
        op = next((c.type for c in r.children if not c.is_named), "")
        return op, [c for c in r.named_children if c.type != "file_descriptor"]

    def extra_words(r: Node) -> List[Node]:
        # tree-sitter puts words after a redirect target into the redirect
        op, dests = redirect_parts(r)
        if r.type != "file_redirect" or not dests:
            return []
        return dests if op in (">&-", "<&-") else dests[1:]

    stack = [_PARSER.parse(visitor.src).root_node]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        if n.type == "file_redirect":
            op, dests = redirect_parts(n)
            if dests and ">" in op and op not in (">&-", "<&-") and not (op in (">&", "<&") and dests[0].type == "number"):
                path = text(dests[0])
                if not is_benign_redirect_target(path):
                    targets.append(path)
        elif n.type == "command":
            redirects = [c for c in n.named_children if c.type.endswith("redirect")]
            # `a | b > f x`: redirects after a pipeline/list belong to its last command
            owner = n
            while owner.parent is not None and owner.parent.type in ("pipeline", "list", "negated_command") \
                    and owner.parent.named_children and owner.parent.named_children[-1].id == owner.id:
                owner = owner.parent
            if owner.parent is not None and owner.parent.type == "redirected_statement":
                redirects += [c for c in owner.parent.named_children if c.type.endswith("redirect")]
            words = [
                c.named_children[0] if c.type == "command_name" and c.named_children else c
                for c in n.named_children
                if not c.type.endswith("redirect") and c.type != "variable_assignment"
            ]
            for r in redirects:
                words += extra_words(r)
            words.sort(key=lambda w: w.start_byte)
            argvs.append([text(w) for w in words])
    return argvs, targets
