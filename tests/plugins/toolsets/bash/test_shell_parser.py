"""Tests for the tree-sitter based shell parser used by bash toolset validation.

Three groups:

- extraction: what parse_command() reports for ordinary commands;
- bypass regressions: commands the previous parser (bashlex) mis-read so that
  validate_command auto-allowed something bash would run differently;
- misparse guards: inputs tree-sitter-bash parses differently from bash. Each
  must raise ShellParseError (-> approval) rather than return a wrong tree.
"""

import pytest

from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.shell_parser import ShellParseError, parse_command
from holmes.plugins.toolsets.bash.validation import (
    ValidationStatus,
    get_effective_lists,
    validate_command,
)

_CORE = get_effective_lists(BashExecutorConfig(builtin_allowlist="core"))
_EXTENDED = get_effective_lists(BashExecutorConfig(builtin_allowlist="extended"))


@pytest.fixture(autouse=True)
def _default_deny_env(monkeypatch):
    monkeypatch.delenv("HOLMES_BASH_UNSAFE_ARGS_MODE", raising=False)


class TestExtraction:
    def test_pipeline_segments_and_argv(self):
        p = parse_command("kubectl get pods -n default | grep -v Running | wc -l")
        assert p.segments == ["kubectl get pods -n default", "grep -v Running", "wc -l"]
        assert p.command_argvs == [
            ["kubectl", "get", "pods", "-n", "default"],
            ["grep", "-v", "Running"],
            ["wc", "-l"],
        ]
        assert p.command_arg_dynamic == [False, False, False]
        assert not p.contains_compound_command
        assert p.write_redirect_targets == []

    def test_operators(self):
        p = parse_command("a && b || c; d & e")
        assert p.segments == ["a", "b", "c", "d", "e"]

    def test_line_continuation_between_words(self):
        p = parse_command("kubectl get pods \\\n  -n x \\\n  -o wide")
        assert p.command_argvs == [["kubectl", "get", "pods", "-n", "x", "-o", "wide"]]

    @pytest.mark.parametrize(
        "command, argv",
        [
            ("echo 'a b'", ["echo", "a b"]),
            ('echo "a b"', ["echo", "a b"]),
            ("echo a\\ b", ["echo", "a b"]),
            ("echo 'it'\\''s'", ["echo", "it's"]),
            ("echo \"a\"'b'c", ["echo", "abc"]),
            ("echo '-ex''ec'", ["echo", "-exec"]),
            ('echo "\\$x \\"q\\" \\\\"', ["echo", '$x "q" \\']),
            ('echo "\\a"', ["echo", "\\a"]),
            ("echo $'a\\nb'", ["echo", "a\nb"]),
            ("echo '{\"k\": \"v\"}'", ["echo", '{"k": "v"}']),
            ("kubectl get pods -o jsonpath='{.items[*].metadata.name}'",
             ["kubectl", "get", "pods", "-o", "jsonpath={.items[*].metadata.name}"]),
            ("kubectl get pods -o jsonpath={.items[*].metadata.name}",
             ["kubectl", "get", "pods", "-o", "jsonpath={.items[*].metadata.name}"]),
            ("echo {} *.txt ~/x", ["echo", "{}", "*.txt", "~/x"]),
            ('echo "{a,b}" \\{a,b} {.a}', ["echo", "{a,b}", "{a,b}", "{.a}"]),
            ("echo héllo '日本'", ["echo", "héllo", "日本"]),
            ("echo a#b # comment", ["echo", "a#b"]),
        ],
    )
    def test_unquoting(self, command, argv):
        assert parse_command(command).command_argvs == [argv]

    def test_assignment_prefix_is_not_argv(self):
        p = parse_command("A=1 B=2 ls -l")
        assert p.segments == ["A=1 B=2 ls -l"]
        assert p.command_argvs == [["ls", "-l"]]

    @pytest.mark.parametrize(
        "command, dynamic",
        [
            ("find . -name $X", True),
            ('find . -name "${X}"', True),
            ("find . -name $(echo x)", True),
            ("find . -name `echo x`", True),
            ("sort <(ls)", True),
            ("find . -name ${X:-$(id)}", True),
            ("find . -name '$(x)'", False),  # single-quoted: never expanded
            ("find . -name $'x'", False),  # ANSI-C quoting is a literal
            ("$CMD -l", False),  # only arguments count
        ],
    )
    def test_dynamic_arguments(self, command, dynamic):
        assert parse_command(command).command_arg_dynamic[0] is dynamic

    def test_nested_commands_are_extracted_in_preorder(self):
        p = parse_command('echo "$(echo "$(rm x)")" `id` <(ls)')
        assert [a[0] for a in p.command_argvs] == ["echo", "echo", "rm", "id", "ls"]

    @pytest.mark.parametrize(
        "command, targets",
        [
            ("ls > f", ["f"]),
            ("ls >> f", ["f"]),
            ("ls &> f", ["f"]),
            ("ls >| f", ["f"]),
            ("ls >& f", ["f"]),
            ("ls > '/tmp/a b'", ["/tmp/a b"]),
            ("ls 2>&1", []),
            ("ls >&2", []),
            ("ls > /dev/null 2>/dev/stderr", []),
            ("ls < in", []),
            ("a | b > f", ["f"]),
            ("echo $(ls > f)", ["f"]),
        ],
    )
    def test_write_redirect_targets(self, command, targets):
        assert parse_command(command).write_redirect_targets == targets

    def test_comment_only(self):
        # bashlex crashed on this
        p = parse_command("# just a comment")
        assert p.segments == []

    @pytest.mark.parametrize(
        "command",
        [
            "echo 'unterminated",
            # not supported by this parser: validate_command asks for approval
            "A=1",
            'export A="b c"',
            "unset x",
            '[ -f x ] && cat x',
            "for i in 1 2; do cat $i; done",
            "if a; then b; fi",
            "(ls)",
            "{ ls; } > f",
            "f() { ls; }",
            "cat <<EOF\nhi\nEOF",
            "cat <<< hi",
            "find . 2>/dev/null -exec rm {} \\;",
            "ls >&-",
            "echo $'\\x41'",
            "ls |",
            "| ls",
            "(ls",
            "case $x in a) ls;; esac",
            "[[ -f x ]] && cat x",
            "echo $((1+2))",
            "((x++))",
            "a=(1 2)",
            "for ((i=0;i<3;i++)); do ls; done",
            "select x in a; do ls; done",
            "time rm x",
            "coproc rm x",
        ],
    )
    def test_unparseable_or_unsupported(self, command):
        with pytest.raises(ShellParseError):
            parse_command(command)


class TestBypassRegressions:
    """Commands the bashlex-based validator auto-allowed while bash would run
    something the checks were meant to catch."""

    @pytest.mark.parametrize(
        "command, lists",
        [
            # bashlex dropped everything after a newline inside $(...)
            ('grep x "$(echo a\nkubectl delete pod foo)"', _CORE),
            ("echo $(date\nrm -rf /tmp/victim)", _CORE),
            # bashlex did not parse heredoc bodies
            ("grep x <<EOF\n$(kubectl delete pod foo)\nEOF", _CORE),
        ],
    )
    def test_hidden_command_not_allowed(self, command, lists):
        result = validate_command(command, [], *lists)
        assert result.status != ValidationStatus.ALLOWED

    @pytest.mark.parametrize(
        "command",
        [
            # bashlex left `''` / `"'` inside the word, so -exec/-delete went unseen
            "find . '-ex''ec' rm -rf {} \\;",
            "find /tmp '-de''lete'",
            "find . \"-ex\"'ec' rm {} \\;",
            "find . -\"de\"'lete'",
        ],
    )
    def test_quote_concatenation_denied(self, command):
        result = validate_command(command, [], *_EXTENDED)
        assert result.status == ValidationStatus.DENIED

    @pytest.mark.parametrize(
        "command",
        [
            # bash deletes `\<NL>`, so `#` joins the previous word and `;touch`
            # runs; tree-sitter read `#;touch pwn` as a comment
            "ls -la\\\n#;touch pwn",
            "kubectl get pods\\\n#;kubectl delete pod x",
            'ls "a"\\\n#;touch pwn',
            # `$(...)` in a `${...}` subscript or pattern runs
            "echo ${a[$(touch /tmp/p)]}",
            "echo ${HOME#$(touch /tmp/p)}",
            "echo ${HOME^^$(touch /tmp/p)}",
            "echo ${HOME%`touch /tmp/p`}",
            "kubectl get pods ${a[$(kubectl delete ns prod)]}",
            # brace expansion builds arguments the per-argument checks never see
            "find . {-exec,} sh -c id \\;",
            "find . -name x {-delete,}",
            'find . {"-exec",} sh -c id \\;',
        ],
    )
    def test_hidden_code_not_allowed(self, command):
        result = validate_command(command, [], *_EXTENDED)
        assert result.status != ValidationStatus.ALLOWED

    def test_ansi_c_quoted_argument_is_checked(self):
        # bashlex saw `$-delete` (a bogus expansion); bash runs find -delete
        result = validate_command("find . $'-delete'", [], *_EXTENDED)
        assert result.status == ValidationStatus.DENIED


class TestMisparseGuards:
    """tree-sitter-bash disagrees with bash on these. Returning its tree would
    hide commands or arguments, so they must be refused."""

    @pytest.mark.parametrize(
        "command",
        [
            # one command to tree-sitter (`ls rm -rf x`), two to bash
            "ls\n\\\nrm -rf x",
            "cat f >g \n\\\n rm x",
            # `$'\\'` swallows the rest into the string
            "echo $'\\\\' ; rm -rf x ; echo '",
            # escaped backticks inside backticks
            "echo `echo \\`kubectl delete pod x\\``",
            'echo "`find . \\"-exec\\" rm {} \\;`"',
            # adjacent backticks merged into one substitution
            "kubectl get pods `echo a` `kubectl delete pod x`",
            "ls `ls -la` `find . -delete`",
            "echo `echo x` `find / -exec sh -c id {} +`",
            "echo `ls``find . -delete`",
            'echo "`ls` `find . -delete`"',
            # backticks in an unquoted heredoc left as text
            "cat <<EOF\n`id`\nEOF",
            # words after a heredoc delimiter
            "cat <<EOF  grep h\nhi\nEOF",
            # a standalone escaped space is dropped
            "tee -c \\  -fprint x",
            # one bash word split in two
            "echo a\\\nb",
            "find . -de\\\nlete",
            "A=\\\n curl x",
            "ls { ]]",
            # a lone `$` is dropped / swallows the next word
            "ls $ f",
            "A=$ xargs x",
            # CR is a word character in bash, whitespace to tree-sitter
            "ls\r\nrm x",
            # `{fd}>` named fd redirect parsed as an argument
            "ls {fd}>f",
            # `!` glued to a word is not negation in bash
            '!"x" y',
            # redirect target split in two
            "grep x&>>f'|'\\|=",
            # `{}` is a word in bash, an empty group to tree-sitter
            "ls && {}",
            "export} A=1",
            # `#` after `\<NL>` or an escaped blank is not a comment in bash
            "ls -la\\\n#;touch pwn",
            "ls \\ #c;touch pwn",
            # substitutions tree-sitter keeps as text inside `${...}`
            "echo ${HOME#$(id)}",
            "echo ${HOME/x/`id`}",
            # brace expansion
            "echo {a,b}",
            "echo a{,.bak}",
            "echo {1..3}",
            "cat >{a,b}",
        ],
    )
    def test_refused(self, command):
        with pytest.raises(ShellParseError):
            parse_command(command)

    @pytest.mark.parametrize(
        "command",
        [
            "ls\n\\\nrm -rf x",
            "echo $'\\\\' ; rm -rf x ; echo '",
            "echo `echo \\`kubectl delete pod x\\``",
            "kubectl get pods `echo a` `kubectl delete pod x`",
            "ls `ls -la` `find . -delete`",
            "echo `echo x` `find / -exec sh -c id {} +`",
        ],
    )
    def test_refused_commands_are_not_auto_allowed(self, command):
        result = validate_command(command, [], *_EXTENDED)
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert result.prefixes_needing_approval == []


class TestUnparseableFallback:
    """Commands the parser refuses still get the deny checks on the raw text,
    so a command that would be denied is never downgraded to approval."""

    @pytest.mark.parametrize(
        "command",
        [
            "ls <> f",
            "ls {fd}>f",
            "echo `echo \\`x\\`` > out",
            "case $x in a) find . -delete;; esac",
            "[[ -f x ]] && sort -o out f",
            "echo $((1+2)) > f",
        ],
    )
    def test_denied(self, command):
        with pytest.raises(ShellParseError):
            parse_command(command)
        result = validate_command(command, [], *_EXTENDED)
        assert result.status == ValidationStatus.DENIED

    @pytest.mark.parametrize(
        "command",
        [
            "[[ -f x ]] > f",
            "(( 3 > 2 )) && ls > f",
            "echo $(( $(ls > f) + 1 ))",
            # unquoted heredoc: bash runs the substitution in the body
            "cat <<EOF\n$(echo x > f)\nEOF",
            "cat <<EOF\nhi\nEOF\nls > f",
            # `<<` in quotes is not a heredoc
            "echo '<<EOF'\nls > f\nEOF",
        ],
    )
    def test_writes_around_comparisons_and_heredocs_denied(self, command):
        result = validate_command(command, [], *_EXTENDED)
        assert result.status == ValidationStatus.DENIED

    @pytest.mark.parametrize(
        "command",
        [
            "[[ -f x ]] && cat x 2>/dev/null",
            "case $x in a) ls;; esac",
            "echo $((1+2)) 2>&1",
            # `>` compares here, or is heredoc data: not a file write
            '[[ "$a" > "$b" ]]',
            "echo $(( 3 > 2 ))",
            "(( 3 > 2 )) && ls",
            "cat <<EOF\n<html><body>x</body></html>\nEOF",
            "cat <<'EOF'\nkey: >\n  folded\nEOF",
            "cat <<'EOF'\n$(echo x > f)\nEOF",
        ],
    )
    def test_approval(self, command):
        result = validate_command(command, [], *_EXTENDED)
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
