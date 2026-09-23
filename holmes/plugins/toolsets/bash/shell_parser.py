"""
Shell command parsing for bash toolset validation, built on tree-sitter-bash.

`parse_command()` turns a command string into the facts validation.py needs:

- segments: the source text of every simple command (including commands nested
  in `$(...)`, backticks, `<(...)`, heredoc bodies and compound statements), in
  pre-order;
- contains_compound_command: True if a for/while/until/if/subshell/brace group/
  function definition appears;
- command_argvs: the unquoted argv of every simple command;
- command_arg_dynamic: per argv, whether an *argument* (not argv[0]) contains a
  runtime expansion (`$VAR`, `${VAR}`, `$(...)`, backticks, `<(...)`), so the
  static argv may differ from what the shell actually runs;
- write_redirect_targets: targets of output redirections to real files.

Security model: this is a *fail-closed* parser. tree-sitter-bash is an
error-tolerant grammar that does not match bash exactly, and some of its
misparses would hide commands or arguments from validation (e.g. it reads
`ls<NL>\\<NL>rm x` as the single command `ls rm x`, and `$'\\\\'` swallows the
rest of the line into a string). Every construct we do not explicitly model,
and every known misparse pattern, raises ShellParseError, which validation
routes to APPROVAL_REQUIRED so the command is never auto-executed.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import tree_sitter_bash
from tree_sitter import Language, Node, Parser

from holmes.plugins.toolsets.bash.argv_utils import is_benign_redirect_target

_PARSER = Parser(Language(tree_sitter_bash.language()))


class ShellParseError(Exception):
    """The command cannot be parsed with enough confidence to validate it."""


# Constructs we don't model. They route to approval.
UNSUPPORTED_NODES = frozenset(
    {
        "case_statement",
        "c_style_for_statement",
        "arithmetic_expansion",  # $(( ))
        "array",  # a=(1 2)
        "ternary_expression",
        "binary_expression",
        "unary_expression",
        "postfix_expression",
        "parenthesized_expression",
    }
)

COMPOUND_NODES = frozenset(
    {
        "for_statement",
        "while_statement",  # also `until`
        "if_statement",
        "subshell",
        "compound_statement",  # { ...; }
        "function_definition",
    }
)

CONTAINER_NODES = frozenset(
    {"program", "list", "pipeline", "negated_command", "do_group", "elif_clause", "else_clause"}
)

SIMPLE_COMMAND_NODES = frozenset({"command", "declaration_command", "unset_command"})

DYNAMIC_NODES = frozenset(
    {"simple_expansion", "expansion", "command_substitution", "process_substitution"}
)

WORD_NODES = frozenset(
    {
        "word",
        "string",
        "raw_string",
        "ansi_c_string",
        "translated_string",
        "concatenation",
        "number",
        "brace_expression",
        "simple_expansion",
        "expansion",
        "command_substitution",
        "process_substitution",
    }
)

REDIRECT_NODES = frozenset({"file_redirect", "heredoc_redirect", "herestring_redirect"})

# Leaves inside words that cannot contain commands.
INERT_LEAF_NODES = frozenset(
    {
        "word",
        "raw_string",
        "ansi_c_string",
        "number",
        "string_content",
        "variable_name",
        "special_variable_name",
        "heredoc_content",
        "heredoc_start",
        "heredoc_end",
        "file_descriptor",
        "regex",
        "extglob_pattern",
        "test_operator",
    }
)

TEST_EXPRESSION_NODES = frozenset(
    {"unary_expression", "binary_expression", "parenthesized_expression", "test_command"}
)

# tree-sitter-bash lets a line continuation right after a newline glue the next
# line onto these nodes; in bash the newline ends the command.
NO_NEWLINE_BETWEEN_CHILDREN = SIMPLE_COMMAND_NODES | REDIRECT_NODES | {
    "redirected_statement",
    "concatenation",
    "variable_assignment",
    "variable_assignments",
    "negated_command",
}

HEREDOC_REDIRECT_CHILDREN = frozenset(
    {
        "heredoc_start",
        "heredoc_body",
        "heredoc_end",
        "file_descriptor",
        "file_redirect",
        "herestring_redirect",
        "pipeline",
        "list",
        "command",
        "redirected_statement",
    }
)

_ASSIGNMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NAMED_FD_REDIRECT = re.compile(rb"\{[A-Za-z_][A-Za-z0-9_]*\}[<>]")
_UNPARSED_SUBSTITUTION = re.compile(rb"`|\$[({]")
_UNESCAPED_BLANK = re.compile(rb"(?<!\\)[ \t]")
_LINE_CONTINUATION = b"\\\n"

_ANSI_C_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}
_HEX_DIGITS = "0123456789abcdefABCDEF"


def _unescape_unquoted(s: str) -> str:
    """Quote removal for unquoted text: `\\x` -> `x`, `\\<newline>` -> nothing."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            if s[i + 1] != "\n":
                out.append(s[i + 1])
            i += 2
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _unescape_double_quoted(s: str) -> str:
    """Inside double quotes a backslash only escapes `"`, `\\`, `$`, `` ` `` and newline."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in '"\\$`\n':
            if s[i + 1] != "\n":
                out.append(s[i + 1])
            i += 2
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _decode_ansi_c(s: str) -> str:
    """Decode the body of a `$'...'` string."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c != "\\" or i + 1 >= len(s):
            out.append(c)
            i += 1
            continue
        n = s[i + 1]
        if n in _ANSI_C_ESCAPES:
            out.append(_ANSI_C_ESCAPES[n])
            i += 2
        elif n in "01234567":
            j = i + 1
            while j < len(s) and j < i + 4 and s[j] in "01234567":
                j += 1
            out.append(chr(int(s[i + 1 : j], 8) & 0xFF))
            i = j
        elif n in "xuU":
            width = {"x": 2, "u": 4, "U": 8}[n]
            j = i + 2
            while j < len(s) and j < i + 2 + width and s[j] in _HEX_DIGITS:
                j += 1
            out.append(chr(int(s[i + 2 : j], 16)) if j > i + 2 else "\\" + n)
            i = j
        elif n == "c" and i + 2 < len(s):
            out.append(chr(ord(s[i + 2]) & 0x1F))
            i += 3
        else:
            out.append("\\" + n)
            i += 2
    return "".join(out)


@dataclass
class ParsedCommand:
    """Facts about a shell command that validation needs."""

    command: str
    segments: List[str] = field(default_factory=list)
    contains_compound_command: bool = False
    command_argvs: List[List[str]] = field(default_factory=list)
    command_arg_dynamic: List[bool] = field(default_factory=list)
    write_redirect_targets: List[str] = field(default_factory=list)


# (node, unquoted text, contains a runtime expansion)
_Word = Tuple[Node, str, bool]


class _CommandVisitor:
    """Walks a tree-sitter-bash tree and fills a ParsedCommand."""

    def __init__(self, command: str):
        self.src = command.encode("utf-8")
        self.result = ParsedCommand(command=command)
        # A redirected_statement whose body is a pipeline/list: the trailing
        # redirects belong to the body's last simple command.
        # node id -> (segment end byte, redirect nodes)
        self._pending_redirects: Dict[int, Tuple[int, List[Node]]] = {}

    # -- text helpers --------------------------------------------------------

    def text(self, start: int, end: int) -> str:
        return self.src[start:end].decode("utf-8", errors="surrogateescape")

    def node_text(self, n: Node) -> str:
        return self.text(n.start_byte, n.end_byte)

    def node_bytes(self, n: Node) -> bytes:
        return self.src[n.start_byte : n.end_byte]

    def unquote(self, n: Node) -> str:
        """The shell word after quote removal. Expansions are kept literally."""
        t = n.type
        if t in ("word", "number"):
            return _unescape_unquoted(self.node_text(n))
        if t == "raw_string":
            return self.node_text(n)[1:-1]
        if t == "ansi_c_string":
            return _decode_ansi_c(self.node_text(n)[2:-1])
        if t == "translated_string":
            inner = [c for c in n.named_children if c.type == "string"]
            if len(inner) != 1:
                raise ShellParseError("unexpected $\"...\" string shape")
            return self.unquote(inner[0])
        if t == "string":
            out, pos, end = [], n.start_byte + 1, n.end_byte - 1
            for c in n.named_children:
                if c.type == "string_content":
                    continue
                if c.type not in DYNAMIC_NODES:
                    raise ShellParseError(f"unsupported syntax in string: {c.type}")
                out.append(_unescape_double_quoted(self.text(pos, c.start_byte)))
                out.append(self.node_text(c))
                pos = c.end_byte
            out.append(_unescape_double_quoted(self.text(pos, end)))
            return "".join(out)
        if t in DYNAMIC_NODES or t == "brace_expression":
            return self.node_text(n)
        if t == "concatenation":
            out = []
            for c in n.children:
                if c.is_named:
                    self.check_word(c)
                    out.append(self.unquote(c))
                else:
                    out.append(_unescape_unquoted(self.node_text(c)))
            return "".join(out)
        raise ShellParseError(f"unsupported word syntax: {t}")

    def unquote_assignment(self, n: Node) -> str:
        """`A="b c"` -> `A=b c` (for `export A="b c"` style argv words)."""
        out = []
        for c in n.children:
            if not c.is_named or c.type in ("variable_name", "subscript"):
                out.append(self.node_text(c))
            else:
                self.check_word(c)
                out.append(self.unquote(c))
        return "".join(out)

    def is_valid_assignment(self, n: Node) -> bool:
        name = n.child_by_field_name("name")
        return name is not None and bool(
            _ASSIGNMENT_NAME.match(self.node_text(name).split("[")[0])
        )

    def check_word(self, n: Node) -> None:
        if n.type in UNSUPPORTED_NODES or n.type not in WORD_NODES:
            raise ShellParseError(f"unsupported syntax: {n.type}")

    def is_dynamic(self, n: Node) -> bool:
        if n.type in DYNAMIC_NODES:
            return True
        if n.type in ("raw_string", "ansi_c_string"):
            return False
        return any(self.is_dynamic(c) for c in n.named_children)

    def word(self, n: Node) -> _Word:
        return (n, self.unquote(n), self.is_dynamic(n))

    @staticmethod
    def is_bracket_test(n: Node) -> bool:
        """`[ ... ]` is an ordinary command in bash (`[[ ... ]]` is not)."""
        return n.type == "test_command" and bool(n.children) and n.children[0].type == "["

    # -- trust checks --------------------------------------------------------

    def check_tree(self, root: Node) -> None:
        """Refuse trees where tree-sitter-bash is known to disagree with bash."""
        covered = bytearray(len(self.src))
        stack = [root]
        while stack:
            x = stack.pop()
            stack.extend(x.children)
            if x.child_count == 0 or x.type in (
                "string_content",
                "heredoc_content",
                "heredoc_body",
                "comment",
            ):
                covered[x.start_byte : x.end_byte] = b"\x01" * (x.end_byte - x.start_byte)
            self.check_node(x)

        # tree-sitter can silently drop source text (e.g. a standalone escaped
        # space `\ `): every non-blank byte must belong to a leaf token.
        uncovered = bytes(
            b
            for b, c in zip(self.src.replace(_LINE_CONTINUATION, b"  "), covered)
            if not c
        )
        if uncovered.strip():
            raise ShellParseError("part of the command was not understood")

    def check_node(self, x: Node) -> None:  # noqa: C901
        t = x.type
        raw = self.node_bytes(x)

        if t in NO_NEWLINE_BETWEEN_CHILDREN:
            # `ls<NL>\<NL>rm x` is parsed as ONE command `ls rm x`.
            for a, b in zip(x.children, x.children[1:]):
                if b.type == "heredoc_body" or a.type == "heredoc_start":
                    continue
                if b"\n" in self.src[a.end_byte : b.start_byte].replace(_LINE_CONTINUATION, b""):
                    raise ShellParseError("command continues across a newline")

        if t in SIMPLE_COMMAND_NODES:
            kids = x.children
            for a, b in zip(kids, kids[1:]):
                gap = self.src[a.end_byte : b.start_byte]
                # Two tree-sitter nodes with nothing between them are one shell
                # word that tree-sitter split (`a\<NL>b`, `A=x\]`, `>f'|'x`).
                if (
                    gap.replace(_LINE_CONTINUATION, b"") == b""
                    and b.type not in REDIRECT_NODES
                    and a.is_named
                    and b.is_named
                ):
                    raise ShellParseError("ambiguous word boundary")
            if t == "command" and any(not c.is_named for c in kids):
                raise ShellParseError("unexpected token in command")  # e.g. a lone `$`
            name = x.child_by_field_name("name")
            if name is not None and self.node_text(name) in ("time", "coproc"):
                # keywords that prefix another command; tree-sitter treats them
                # as the command name and the real command as an argument
                raise ShellParseError(f"unsupported syntax: {self.node_text(name)}")

        if t in ("declaration_command", "unset_command") and x.children:
            keyword = x.children[0]
            if keyword.end_byte != x.end_byte and not self.src[
                keyword.end_byte : keyword.end_byte + 1
            ].isspace():
                raise ShellParseError("ambiguous word boundary")  # `export}x`

        if t == "negated_command" and not self.src[x.start_byte + 1 : x.start_byte + 2].isspace():
            raise ShellParseError("'!' not followed by a blank")  # a word in bash

        if t == "compound_statement" and not x.named_children:
            raise ShellParseError("empty compound statement")  # `{}` is a word in bash

        if t == "command_substitution" and raw[:1] == b"`" and b"\\" in raw:
            # backslashes inside backticks are unescaped before the inner
            # command is parsed; tree-sitter doesn't model that
            raise ShellParseError("backslash inside backtick substitution")

        if not x.is_named and t == "$" and (
            x.parent is None or x.parent.type not in ("simple_expansion", "expansion", "string")
        ):
            raise ShellParseError("unexpected '$'")

        if t in ("file_redirect", "herestring_redirect"):
            dests = [c for c in x.named_children if c.type != "file_descriptor"]
            for a, b in zip(dests, dests[1:]):
                if self.src[a.end_byte : b.start_byte].replace(_LINE_CONTINUATION, b"") == b"":
                    raise ShellParseError("ambiguous redirect target")
            if t == "herestring_redirect" and len(dests) > 1:
                raise ShellParseError("ambiguous here-string")

        if t == "heredoc_redirect":
            for c in x.named_children:
                if c.type not in HEREDOC_REDIRECT_CHILDREN:
                    raise ShellParseError("unsupported heredoc syntax")
            self.check_heredoc_body(x)

        if self.is_bracket_test(x):
            tokens: List[Node] = []
            pending = [x]
            while pending:
                y = pending.pop()
                if y.type in TEST_EXPRESSION_NODES:
                    pending.extend(reversed(y.children))
                else:
                    tokens.append(y)
            for a, b in zip(tokens, tokens[1:]):
                if a.end_byte == b.start_byte:
                    raise ShellParseError("ambiguous word boundary")

        if t in ("concatenation", "simple_expansion"):
            for a, b in zip(x.children, x.children[1:]):
                if a.end_byte != b.start_byte:
                    raise ShellParseError("ambiguous word boundary")

        if t in ("raw_string", "ansi_c_string", "string"):
            self.check_quote_extent(x)

        if t in ("word", "string_content", "heredoc_content") and _UNPARSED_SUBSTITUTION.search(raw):
            # a substitution tree-sitter left as literal text
            raise ShellParseError("unsupported substitution syntax")

        if t in ("word", "concatenation", "variable_assignment") and _LINE_CONTINUATION in raw:
            raise ShellParseError("line continuation inside a word")

        if t == "word":
            if _UNESCAPED_BLANK.search(raw):
                raise ShellParseError("ambiguous word boundary")
            if b"\n" in raw.replace(_LINE_CONTINUATION, b""):
                raise ShellParseError("ambiguous word boundary")

    def check_heredoc_body(self, x: Node) -> None:
        start = [c for c in x.named_children if c.type == "heredoc_start"]
        if start and re.search(rb"['\"\\]", self.node_bytes(start[0])):
            return  # quoted delimiter: the body is literal text
        for body in (c for c in x.named_children if c.type == "heredoc_body"):
            parsed = [(c.start_byte, c.end_byte) for c in body.named_children if c.type in DYNAMIC_NODES]
            for m in _UNPARSED_SUBSTITUTION.finditer(self.node_bytes(body)):
                pos = body.start_byte + m.start()
                if not any(a <= pos < b for a, b in parsed):
                    raise ShellParseError("unsupported substitution syntax in heredoc")

    def check_quote_extent(self, x: Node) -> None:
        """Re-lex a quoted string with bash's rules and make sure it ends where
        tree-sitter says (it mis-lexes e.g. `$'\\\\'` and swallows the following
        commands into the string)."""
        t = self.node_bytes(x)
        if x.type == "raw_string":
            ok = len(t) >= 2 and t[:1] == b"'" and t[-1:] == b"'" and b"'" not in t[1:-1]
        elif x.type == "ansi_c_string":
            i, close = 2, None
            while i < len(t):
                if t[i : i + 1] == b"\\":
                    i += 2
                    continue
                if t[i : i + 1] == b"'":
                    close = i
                    break
                i += 1
            ok = t[:2] == b"$'" and close == len(t) - 1
        else:  # double-quoted: skip nested substitutions/expansions
            nested = [
                (c.start_byte - x.start_byte, c.end_byte - x.start_byte)
                for c in x.named_children
                if c.type in DYNAMIC_NODES or c.type == "arithmetic_expansion"
            ]
            i, close = 1, None
            while i < len(t):
                skip_to = next((b for a, b in nested if a <= i < b), None)
                if skip_to is not None:
                    i = skip_to
                    continue
                if t[i : i + 1] == b"\\":
                    i += 2
                    continue
                if t[i : i + 1] == b'"':
                    close = i
                    break
                i += 1
            ok = t[:1] == b'"' and close == len(t) - 1
        if not ok:
            raise ShellParseError("ambiguous quoting")

    # -- traversal -----------------------------------------------------------

    def run(self) -> ParsedCommand:
        if _NAMED_FD_REDIRECT.search(self.src):
            # `{fd}>f`: tree-sitter parses `{fd}` as an argument
            raise ShellParseError("unsupported syntax: named file descriptor redirect")
        if b"\\\n#" in self.src:
            raise ShellParseError("line continuation before '#'")
        if b"\r" in self.src:
            # bash treats CR as part of a word; tree-sitter as whitespace
            raise ShellParseError("carriage return in command")
        root = _PARSER.parse(self.src).root_node
        if root.has_error:
            raise ShellParseError("invalid or unsupported shell syntax")
        self.check_tree(root)
        self.visit(root)
        return self.result

    def visit(self, n: Node) -> None:  # noqa: C901
        if not n.is_named:
            return
        t = n.type
        if t == "comment":
            return
        if t in SIMPLE_COMMAND_NODES or self.is_bracket_test(n):
            self.visit_simple_command(n, n.start_byte, n.end_byte, [])
            return
        if t in UNSUPPORTED_NODES or t == "test_command":  # `[[ ... ]]`
            raise ShellParseError(f"unsupported syntax: {t}")
        if t == "redirected_statement":
            self.visit_redirected_statement(n)
            return
        if t in CONTAINER_NODES:
            for c in n.named_children:
                self.visit(c)
            return
        if t in COMPOUND_NODES:
            if t == "for_statement" and n.children[0].type == "select":
                raise ShellParseError("unsupported syntax: select")
            if t == "compound_statement" and n.children[0].type == "((":
                raise ShellParseError("unsupported syntax: arithmetic command")
            self.result.contains_compound_command = True
            for c in n.named_children:
                if c.type == "variable_name":
                    continue
                if c.type in WORD_NODES:  # for-loop values, function name
                    self.visit_nested(c)
                else:
                    self.visit(c)
            return
        if t in ("variable_assignment", "variable_assignments"):
            assignments = (
                [n] if t == "variable_assignment" else [c for c in n.named_children if c.type == "variable_assignment"]
            )
            self.result.segments.append(self.node_text(n).strip())
            if not all(self.is_valid_assignment(a) for a in assignments):
                if t != "variable_assignment":
                    raise ShellParseError("invalid variable assignment")
                # `-a-b=1` is not an assignment in bash; it is a command
                self.add_argv([(n, self.unquote_assignment(n), self.is_dynamic(n))])
            self.visit_nested(n)
            return
        if t in REDIRECT_NODES:  # redirect with no command, e.g. `> f`
            self.result.segments.append(self.node_text(n).strip())
            self.add_argv([self.word(e) for e in self.check_redirect(n)])
            self.visit_redirect(n)
            return
        raise ShellParseError(f"unsupported syntax: {t}")

    def visit_nested(self, n: Node) -> None:
        """Visit commands nested inside a word (substitutions, heredoc bodies)."""
        if n.type in UNSUPPORTED_NODES:
            raise ShellParseError(f"unsupported syntax: {n.type}")
        if n.type in ("command_substitution", "process_substitution"):
            for c in n.named_children:
                self.visit(c)
            return
        for c in n.named_children:
            if c.type in INERT_LEAF_NODES:
                continue
            if c.type in REDIRECT_NODES:
                self.visit_redirect(c)
            elif c.type in UNSUPPORTED_NODES:
                raise ShellParseError(f"unsupported syntax: {c.type}")
            elif c.type in WORD_NODES or c.type in ("variable_assignment", "heredoc_body", "subscript"):
                self.visit_nested(c)
            else:
                raise ShellParseError(f"unsupported syntax: {c.type}")

    def last_simple_command(self, n: Node) -> Optional[Node]:
        if n.type in SIMPLE_COMMAND_NODES or self.is_bracket_test(n):
            return n
        if n.type not in ("pipeline", "list", "negated_command"):
            return None
        kids = [c for c in n.named_children if c.type != "comment"]
        return self.last_simple_command(kids[-1]) if kids else None

    def segment_end(self, redirects: List[Node], default_end: int) -> int:
        """A command's segment text stops before a heredoc body."""
        for r in redirects:
            if r.type == "heredoc_redirect":
                body = [c for c in r.named_children if c.type == "heredoc_body"]
                if body:
                    return len(self.src[: body[0].start_byte].rstrip(b"\n"))
        return default_end

    def visit_redirected_statement(self, n: Node) -> None:
        body = n.child_by_field_name("body")
        redirects = [c for c in n.named_children if c.type in REDIRECT_NODES]
        end = self.segment_end(redirects, n.end_byte)
        if body is None:
            self.result.segments.append(self.text(n.start_byte, end).strip())
            extra_words: List[Node] = []
            for r in redirects:
                extra_words += self.check_redirect(r)
            self.add_argv([self.word(e) for e in extra_words])
            for r in redirects:
                self.visit_redirect(r)
            return
        if body.type in SIMPLE_COMMAND_NODES or self.is_bracket_test(body):
            self.visit_simple_command(body, n.start_byte, end, redirects)
            return
        owner = self.last_simple_command(body)
        if owner is not None:
            # `a | b > f`: the redirect belongs to `b`
            self._pending_redirects[owner.id] = (end, redirects)
            self.visit(body)
            if owner.id in self._pending_redirects:
                raise ShellParseError("unsupported redirect syntax")
            return
        # redirect on a compound statement, e.g. `{ ls; } > f`
        self.visit(body)
        for r in redirects:
            if self.check_redirect(r):
                raise ShellParseError("unsupported redirect syntax")
        for r in redirects:
            self.visit_redirect(r)

    def check_redirect(self, r: Node) -> List[Node]:
        """Record output-file targets. Returns word nodes that tree-sitter put in
        the redirect but that bash treats as command arguments: it parses
        `find . 2>/dev/null -exec rm {} \\;` with `-exec rm {} ;` as extra
        redirect destinations."""
        if r.type == "heredoc_redirect":
            extra_words: List[Node] = []
            for c in r.named_children:
                if c.type in REDIRECT_NODES:  # `cat <<EOF > out`
                    extra_words += self.check_redirect(c)
            return extra_words
        if r.type == "herestring_redirect":
            return []
        operator, destinations = None, []
        for c in r.children:
            if not c.is_named:
                operator = c.type
            elif c.type != "file_descriptor":
                self.check_word(c)
                destinations.append(c)
        if not destinations:
            return []
        if operator in (">&-", "<&-"):
            return destinations  # closes an fd; takes no target
        first = destinations[0]
        is_fd = operator in (">&", "<&") and (first.type == "number" or self.node_text(first) == "-")
        if operator and ">" in operator and not is_fd:
            target = self.unquote(first)
            if not is_benign_redirect_target(target):
                self.result.write_redirect_targets.append(target)
        return destinations[1:]

    def visit_redirect(self, r: Node) -> None:
        for c in r.named_children:
            if c.type in ("heredoc_start", "heredoc_end", "file_descriptor"):
                continue
            if c.type == "heredoc_body":
                self.visit_nested(c)
            elif c.type in REDIRECT_NODES:
                self.visit_redirect(c)
            elif c.type in ("pipeline", "list", "redirected_statement") or c.type in SIMPLE_COMMAND_NODES:
                # `cat <<EOF | grep x`: tree-sitter nests the pipeline tail here
                self.visit(c)
            elif c.type in WORD_NODES:
                self.visit_nested(c)
            else:
                raise ShellParseError(f"unsupported redirect syntax: {c.type}")

    def add_argv(self, words: List[_Word]) -> None:
        if words:
            words.sort(key=lambda w: w[0].start_byte)
            self.result.command_argvs.append([w[1] for w in words])
            self.result.command_arg_dynamic.append(any(w[2] for w in words[1:]))

    def visit_simple_command(  # noqa: C901
        self, n: Node, start: int, end: int, outer_redirects: List[Node]
    ) -> None:
        if n.id in self._pending_redirects:
            end, outer_redirects = self._pending_redirects.pop(n.id)
        if not outer_redirects:
            own_redirects = [c for c in n.named_children if c.type in REDIRECT_NODES]
            end = self.segment_end(own_redirects, end)
        self.result.segments.append(self.text(start, end).strip())

        words: List[_Word] = []
        nested: List[Node] = []  # nodes that may contain commands, source order
        extra_words: List[Node] = []
        if self.is_bracket_test(n):

            def collect(x: Node) -> None:
                if x.type in TEST_EXPRESSION_NODES:
                    for c in x.children:
                        collect(c)
                elif not x.is_named or x.type == "test_operator":
                    words.append((x, _unescape_unquoted(self.node_text(x)), False))
                elif x.type in REDIRECT_NODES:
                    extra_words.extend(self.check_redirect(x))
                    nested.append(x)
                else:
                    self.check_word(x)
                    words.append(self.word(x))
                    nested.append(x)

            collect(n)
        else:
            for c in n.children:
                if not c.is_named:
                    if n.type != "command":  # export/local/declare/unset keyword
                        words.append((c, c.type, False))
                    continue
                if c.type == "comment":
                    continue
                if c.type in REDIRECT_NODES:
                    extra_words += self.check_redirect(c)
                    nested.append(c)
                elif c.type == "variable_assignment":
                    if n.type != "command" or not self.is_valid_assignment(c):
                        # `export A=b` -> argv word 'A=b'; `-x=1 cmd` is a word too
                        words.append((c, self.unquote_assignment(c), self.is_dynamic(c)))
                    # a valid `A=1 cmd` prefix is an environment assignment, not argv
                    nested.append(c)
                elif c.type == "variable_name":
                    words.append((c, self.node_text(c), False))
                elif c.type == "command_name":
                    inner = c.named_children
                    if len(inner) != 1:
                        raise ShellParseError("unsupported command name syntax")
                    self.check_word(inner[0])
                    words.append(self.word(inner[0]))
                    nested.append(inner[0])
                else:
                    self.check_word(c)
                    words.append(self.word(c))
                    nested.append(c)
        for r in outer_redirects:
            extra_words += self.check_redirect(r)
        words += [self.word(e) for e in extra_words]
        self.add_argv(words)

        # The command itself first, then commands nested in its words.
        for x in sorted(nested + outer_redirects, key=lambda x: x.start_byte):
            if x.type in REDIRECT_NODES:
                self.visit_redirect(x)
            else:
                self.visit_nested(x)


def parse_command(command: str) -> ParsedCommand:
    """Parse a shell command for validation.

    Raises:
        ShellParseError: If the command is invalid, uses syntax we don't model,
            or matches a pattern tree-sitter-bash is known to misparse.
    """
    try:
        return _CommandVisitor(command).run()
    except RecursionError:
        raise ShellParseError("command is nested too deeply")


def scan_for_deny_checks(command: str) -> Tuple[List[List[str]], List[str]]:
    """Best-effort (argvs, write redirect targets) for a command that
    parse_command() refused. The tree may be wrong, so callers may only use the
    result to DENY a command, never to allow one."""
    visitor = _CommandVisitor(command)
    argvs: List[List[str]] = []
    targets: List[str] = []

    def text(n: Node) -> str:
        try:
            return visitor.unquote(n)
        except ShellParseError:
            return visitor.node_text(n)

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
            if n.parent is not None and n.parent.type == "redirected_statement":
                redirects += [c for c in n.parent.named_children if c.type.endswith("redirect")]
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
