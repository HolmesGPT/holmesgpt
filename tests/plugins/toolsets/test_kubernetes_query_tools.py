"""Behaviour tests for the kubernetes/core query tools (kubernetes_jq_query,
kubernetes_count, kubernetes_tabular_query).

The real tool scripts run through the production path (sanitize -> jinja ->
bash) against a stub ``kubectl`` that serves canned API responses and records
every call, so we can assert both the result and the exact API requests made.
"""

import json
import os
import shutil
import stat

import pytest

from holmes.plugins.toolsets import load_toolsets_from_file

KUBERNETES_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "holmes",
    "plugins",
    "toolsets",
    "kubernetes.yaml",
)

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="bash and jq are required to execute the kubernetes tool scripts",
)


def _load_core_tools():
    toolsets = load_toolsets_from_file(KUBERNETES_YAML, strict_check=False)
    core = next(t for t in toolsets if t.name == "kubernetes/core")
    return {t.name: t for t in core.tools}


TOOLS = _load_core_tools()
PAGINATED_TOOLS = ["kubernetes_jq_query", "kubernetes_count"]

# `kubectl api-resources` sorts by group name, so Calico's CRD is listed before
# networking.k8s.io - exactly the ordering that used to shadow the built-in.
API_RESOURCES = """\
bindings                                       v1                                true         Binding
nodes                             no           v1                                false        Node
pods                              po           v1                                true         Pod
networkpolicies                                crd.projectcalico.org/v1          true         NetworkPolicy
pods                                           metrics.k8s.io/v1beta1            true         PodMetrics
networkpolicies                   netpol       networking.k8s.io/v1              true         NetworkPolicy
"""

# Server discovery order (what `kubectl get` uses): built-ins before CRD groups.
APIS = {
    "groups": [
        {"name": "networking.k8s.io"},
        {"name": "metrics.k8s.io"},
        {"name": "crd.projectcalico.org"},
    ]
}

STUB_KUBECTL = r"""#!/bin/bash
printf '%s\n' "$*" >> "$KUBE_STUB_DIR/calls.log"
if [ "$1" = "api-resources" ]; then
  # `--namespaced=X -o name` prints name.group (just name for the core group)
  case "$*" in
    *--namespaced=*)
      args="$*"; want="${args##*--namespaced=}"; want="${want%% *}"
      awk -v want="$want" '$(NF-1) == want {g = $(NF-2); n = $1; if (index(g, "/")) n = n "." substr(g, 1, index(g, "/") - 1); print n}' \
        "$KUBE_STUB_DIR/api-resources.txt" ;;
    *) cat "$KUBE_STUB_DIR/api-resources.txt" ;;
  esac
  exit 0
fi
if [ "$1" = "get" ] && [ "$2" = "--raw" ]; then
  if [ "$3" = "/apis" ]; then
    cat "$KUBE_STUB_DIR/apis.json"
    exit 0
  fi
  path="${3%%\?*}"
  page=""
  case "$3" in *continue=*) page=".page2" ;; esac
  f="$KUBE_STUB_DIR/raw$(printf '%s' "$path" | tr '/' '_')$page.json"
  if [ -f "$f" ]; then
    cat "$f"
    exit 0
  fi
  echo "Error from server (NotFound): the server could not find the requested resource" >&2
  exit 1
fi
if [ "$1" = "get" ]; then
  cat "$KUBE_STUB_DIR/tabular.txt"
  exit 0
fi
exit 1
"""


def _items(*names, namespace="team-a"):
    return [{"metadata": {"name": n, "namespace": namespace}} for n in names]


def _list(items, cont=None):
    meta = {"continue": cont} if cont else {}
    return {"kind": "List", "metadata": meta, "items": items}


@pytest.fixture
def kube_stub(tmp_path, monkeypatch):
    """A PATH dir with a recording ``kubectl`` stub. Returns a helper object."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(STUB_KUBECTL)
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "api-resources.txt").write_text(API_RESOURCES)
    (tmp_path / "apis.json").write_text(json.dumps(APIS))
    (tmp_path / "tabular.txt").write_text("NAME\n")
    monkeypatch.setenv("KUBE_STUB_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    class Stub:
        def serve(self, path, body, page2=False):
            name = "raw" + path.replace("/", "_") + (".page2" if page2 else "")
            (tmp_path / f"{name}.json").write_text(json.dumps(body))

        def tabular(self, text):
            (tmp_path / "tabular.txt").write_text(text)

        def fail_discovery(self):
            (tmp_path / "apis.json").unlink()

        def calls(self):
            log = tmp_path / "calls.log"
            return log.read_text().splitlines() if log.exists() else []

        def raw_calls(self):
            # resource list requests only (not the /apis discovery lookup)
            return [
                c[len("get --raw ") :]
                for c in self.calls()
                if c.startswith("get --raw /api") and c != "get --raw /apis"
            ]

    return Stub()


def _run(tool_name, params):
    output, _code, _rendered = TOOLS[tool_name]._YAMLTool__invoke_script(params, None)
    return json.loads(output)


def _params(tool_name, kind, **extra):
    if tool_name == "kubernetes_tabular_query":
        params = {"kind": kind, "columns": "NAME:.metadata.name"}
    else:
        params = {"kind": kind, "jq_expr": ".items[] | .metadata.name"}
    params.update(extra)
    return params


def _match_count(tool_name, result):
    return len(result["results"] if tool_name == "kubernetes_jq_query" else result["preview"])


# --- kind resolution (ROB-1564: networkpolicies silently returned 0) ----------


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_builtin_group_wins_over_colliding_crd(kube_stub, tool_name):
    kube_stub.serve("/apis/networking.k8s.io/v1/networkpolicies", _list(_items("deny-all", "allow-web")))
    kube_stub.serve("/apis/crd.projectcalico.org/v1/networkpolicies", _list([]))

    result = _run(tool_name, _params(tool_name, "networkpolicies"))

    assert result["stderr"] is None
    assert result["total_items_processed"] == 2
    assert _match_count(tool_name, result) == 2
    assert kube_stub.raw_calls() == ["/apis/networking.k8s.io/v1/networkpolicies?limit=500"]


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_core_group_wins_over_other_groups(kube_stub, tool_name):
    # metrics.k8s.io also serves "pods"; the core group must still be used
    kube_stub.serve("/api/v1/pods", _list(_items("web-1")))

    result = _run(tool_name, _params(tool_name, "pods"))

    assert result["total_items_processed"] == 1
    assert kube_stub.raw_calls() == ["/api/v1/pods?limit=500"]


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_failed_discovery_falls_back_with_a_note(kube_stub, tool_name):
    kube_stub.fail_discovery()
    kube_stub.serve("/apis/crd.projectcalico.org/v1/networkpolicies", _list([]))

    result = _run(tool_name, _params(tool_name, "networkpolicies"))

    assert kube_stub.raw_calls() == ["/apis/crd.projectcalico.org/v1/networkpolicies?limit=500"]
    assert result["notes"] == [
        "Several API groups serve 'networkpolicies' and server discovery (/apis) failed, "
        "so the first group listed by kubectl api-resources was used."
    ]


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_unknown_kind_reports_error(kube_stub, tool_name):
    result = _run(tool_name, _params(tool_name, "widgets"))

    assert result["stderr"].startswith("Unable to find resource kind 'widgets'.")
    assert kube_stub.raw_calls() == []


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_continue_token_is_url_encoded(kube_stub, tool_name):
    kube_stub.serve("/api/v1/pods", _list(_items("web-1"), cont="a+b/c="))
    kube_stub.serve("/api/v1/pods", _list(_items("web-2")), page2=True)

    result = _run(tool_name, _params(tool_name, "pods"))

    assert result["total_items_processed"] == 2
    assert kube_stub.raw_calls() == [
        "/api/v1/pods?limit=500",
        "/api/v1/pods?limit=500&continue=a%2Bb%2Fc%3D",
    ]


# --- namespace / label / field selectors --------------------------------------


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_selectors_are_applied_server_side(kube_stub, tool_name):
    kube_stub.serve("/api/v1/namespaces/team-a/pods", _list(_items("web-1")))

    result = _run(
        tool_name,
        _params(
            tool_name,
            "pods",
            namespace="team-a",
            label_selector="app=web,tier!=db",
            field_selector="status.phase=Running",
        ),
    )

    assert result["stderr"] is None
    assert result["total_items_processed"] == 1
    assert kube_stub.raw_calls() == [
        "/api/v1/namespaces/team-a/pods?limit=500"
        "&labelSelector=app%3Dweb%2Ctier%21%3Ddb&fieldSelector=status.phase%3DRunning"
    ]
    assert result["scope"] == {
        "resource": "pods",
        "api_version": "v1",
        "namespace": "team-a",
        "label_selector": "app=web,tier!=db",
        "field_selector": "status.phase=Running",
    }


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_set_based_label_selector_is_encoded(kube_stub, tool_name):
    kube_stub.serve("/apis/networking.k8s.io/v1/networkpolicies", _list(_items("np")))

    _run(tool_name, _params(tool_name, "networkpolicies", label_selector="env in (prod,stg)"))

    assert kube_stub.raw_calls() == [
        "/apis/networking.k8s.io/v1/networkpolicies?limit=500&labelSelector=env%20in%20%28prod%2Cstg%29"
    ]


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_selectors_are_kept_on_every_page(kube_stub, tool_name):
    kube_stub.serve("/api/v1/namespaces/team-a/pods", _list(_items("web-1"), cont="tok"))
    kube_stub.serve("/api/v1/namespaces/team-a/pods", _list(_items("web-2")), page2=True)

    result = _run(tool_name, _params(tool_name, "pods", namespace="team-a", label_selector="app=web"))

    assert result["total_items_processed"] == 2
    assert kube_stub.raw_calls() == [
        "/api/v1/namespaces/team-a/pods?limit=500&labelSelector=app%3Dweb",
        "/api/v1/namespaces/team-a/pods?limit=500&labelSelector=app%3Dweb&continue=tok",
    ]


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_null_selectors_behave_like_omitted(kube_stub, tool_name):
    """Strict tool calls send null for unset optional params; it reaches the
    script as the string 'None' and must not become a namespace or selector."""
    kube_stub.serve("/api/v1/pods", _list(_items("web-1")))

    result = _run(
        tool_name,
        _params(tool_name, "pods", namespace=None, label_selector=None, field_selector=None),
    )

    assert result["stderr"] is None
    assert kube_stub.raw_calls() == ["/api/v1/pods?limit=500"]
    assert result["scope"]["namespace"] is None
    assert result["scope"]["label_selector"] is None
    assert result["scope"]["field_selector"] is None


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_namespace_ignored_for_cluster_scoped_kind(kube_stub, tool_name):
    kube_stub.serve("/api/v1/nodes", _list(_items("node-1", namespace=None)))

    result = _run(tool_name, _params(tool_name, "nodes", namespace="team-a"))

    assert result["total_items_processed"] == 1
    assert kube_stub.raw_calls() == ["/api/v1/nodes?limit=500"]
    assert result["scope"]["namespace"] is None
    assert result["notes"] == ["namespace 'team-a' ignored: nodes is cluster-scoped."]


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
@pytest.mark.parametrize(
    "namespace",
    [
        "default/services/http:admin:80/proxy/config?x=",
        "default/pods/web-1/log?x=",
        "a/b",
        "..",
        "team-a%2Fsecrets",
        "Team-A",
        "-team",
        "a" * 64,
    ],
)
def test_invalid_namespace_is_rejected_before_any_request(kube_stub, tool_name, namespace):
    # The namespace is part of the raw API path, so it must not be able to reach other endpoints
    result = _run(tool_name, _params(tool_name, "pods", namespace=namespace))

    assert result["stderr"].startswith(f"Invalid namespace '{namespace}'")
    assert kube_stub.calls() == []


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
@pytest.mark.parametrize("namespace", ["a", "team-a", "kube-system", "ns1", "a" * 63])
def test_valid_namespace_names_are_accepted(kube_stub, tool_name, namespace):
    kube_stub.serve(f"/api/v1/namespaces/{namespace}/pods", _list(_items("web-1", namespace=namespace)))

    result = _run(tool_name, _params(tool_name, "pods", namespace=namespace))

    assert result["stderr"] is None
    assert kube_stub.raw_calls() == [f"/api/v1/namespaces/{namespace}/pods?limit=500"]


@pytest.mark.parametrize("tool_name", PAGINATED_TOOLS)
def test_api_error_for_bad_field_selector_is_returned_with_query(kube_stub, tool_name):
    # The stub has no response for this path, so it fails like the API server would
    result = _run(tool_name, _params(tool_name, "pods", field_selector="spec.foo=bar"))

    assert result["stderr"].startswith(
        "kubectl get --raw failed for /api/v1/pods?limit=500&fieldSelector=spec.foo%3Dbar."
    )
    assert "Error from server" in result["stderr"]
    assert result["scope"]["field_selector"] == "spec.foo=bar"


def _tabular_get_calls(stub):
    return [c for c in stub.calls() if c.startswith("get ") and not c.startswith("get --raw")]


def test_tabular_query_passes_selectors_to_kubectl(kube_stub):
    kube_stub.tabular("NAME\nweb-1\n")

    result = _run(
        "kubernetes_tabular_query",
        _params(
            "kubernetes_tabular_query",
            "pods",
            namespace="team-a",
            label_selector="app=web",
            field_selector="status.phase=Running",
        ),
    )

    assert result["rows"] == ["web-1"]
    assert _tabular_get_calls(kube_stub) == [
        "get pods -n team-a -l app=web --field-selector status.phase=Running -o custom-columns=NAME:.metadata.name"
    ]
    assert result["scope"] == {
        "namespace": "team-a",
        "label_selector": "app=web",
        "field_selector": "status.phase=Running",
    }
    assert result["notes"] == []


def test_tabular_query_notes_namespace_ignored_for_cluster_scoped_kind(kube_stub):
    kube_stub.tabular("NAME\nnode-1\n")

    result = _run("kubernetes_tabular_query", _params("kubernetes_tabular_query", "nodes", namespace="team-a"))

    assert _tabular_get_calls(kube_stub) == ["get nodes --all-namespaces -o custom-columns=NAME:.metadata.name"]
    assert result["scope"]["namespace"] is None
    assert result["notes"] == ["namespace 'team-a' ignored: nodes is cluster-scoped."]


def test_tabular_query_keeps_namespace_for_grouped_namespaced_kind(kube_stub):
    kube_stub.tabular("NAME\ndeny-all\n")

    result = _run(
        "kubernetes_tabular_query", _params("kubernetes_tabular_query", "networkpolicies", namespace="team-a")
    )

    assert _tabular_get_calls(kube_stub) == [
        "get networkpolicies -n team-a -o custom-columns=NAME:.metadata.name"
    ]
    assert result["scope"]["namespace"] == "team-a"
    assert result["notes"] == []


@pytest.mark.parametrize("value", [None, ""])
def test_tabular_query_without_selectors_lists_all_namespaces(kube_stub, value):
    kube_stub.tabular("NAME\nweb-1\nworker-1\n")

    result = _run(
        "kubernetes_tabular_query",
        _params(
            "kubernetes_tabular_query",
            "pods",
            namespace=value,
            label_selector=value,
            field_selector=value,
            filter_pattern=value,
        ),
    )

    assert result["count"] == 2
    assert _tabular_get_calls(kube_stub) == [
        "get pods --all-namespaces -o custom-columns=NAME:.metadata.name"
    ]
    assert result["scope"] == {"namespace": None, "label_selector": None, "field_selector": None}


def test_tabular_query_filter_pattern_is_optional_and_still_filters(kube_stub):
    kube_stub.tabular("NAME\nweb-1\nworker-1\n")

    result = _run("kubernetes_tabular_query", _params("kubernetes_tabular_query", "pods", filter_pattern="^web"))

    assert result["rows"] == ["web-1"]


def test_tabular_query_with_no_rows_returns_empty_list(kube_stub):
    result = _run("kubernetes_tabular_query", _params("kubernetes_tabular_query", "pods"))

    assert result["count"] == 0
    assert result["rows"] == []


# --- large outputs (results must not be passed as a command-line argument) ------

BIG_BLOB = "x" * 3000  # 1000 items x 3KB = ~3MB, above macOS and Linux single-arg limits


def test_jq_query_returns_results_larger_than_arg_limit(kube_stub):
    items = [{"metadata": {"name": f"cm-{i}", "namespace": "team-a"}, "data": {"blob": BIG_BLOB}} for i in range(1000)]
    kube_stub.serve("/api/v1/pods", _list(items))

    result = _run("kubernetes_jq_query", _params("kubernetes_jq_query", "pods", jq_expr=".items[]"))

    assert result["stderr"] is None
    assert result["matches_found"] == 1000
    assert len(result["results"]) == 1000


def test_jq_query_with_no_matches_returns_empty_list(kube_stub):
    kube_stub.serve("/api/v1/pods", _list(_items("web-1")))

    result = _run("kubernetes_jq_query", _params("kubernetes_jq_query", "pods", jq_expr=".items[] | select(.x)"))

    assert result["matches_found"] == 0
    assert result["results"] == []


def test_tabular_query_returns_rows_larger_than_arg_limit(kube_stub):
    kube_stub.tabular("NAME\n" + "".join(f"row-{i}-{BIG_BLOB}\n" for i in range(1000)))

    result = _run("kubernetes_tabular_query", _params("kubernetes_tabular_query", "pods"))

    assert result["count"] == 1000
    assert len(result["rows"]) == 1000


# --- tool schema -----------------------------------------------------------------


@pytest.mark.parametrize("tool_name", list(TOOLS))
def test_selector_params_are_optional(tool_name):
    tool = TOOLS[tool_name]
    optional = {"namespace", "label_selector", "field_selector"}
    if tool_name == "kubernetes_tabular_query":
        optional.add("filter_pattern")

    assert {name for name, p in tool.parameters.items() if not p.required} == optional
    properties = tool.get_openai_format()["function"]["parameters"]["properties"]
    for name in optional:
        assert {"type": "null"} in properties[name].get("anyOf", []) or "null" in properties[name].get(
            "type", []
        ), f"{tool_name}.{name} must accept null"


def test_user_description_shows_selectors():
    tool = TOOLS["kubernetes_jq_query"]

    scoped = tool.get_parameterized_one_liner(
        {"kind": "pods", "jq_expr": ".items", "namespace": "team-a", "label_selector": "app=web", "field_selector": None}
    )
    omitted = tool.get_parameterized_one_liner({"kind": "pods", "jq_expr": ".items"})

    assert scoped == (
        "jq query: kubectl get pods | jq '.items' (namespace=team-a, label_selector=app=web, field_selector=None)"
    )
    # 'namespace' is a Jinja global; an omitted param must not render it
    assert omitted == "jq query: kubectl get pods | jq '.items' (namespace=, label_selector=, field_selector=)"
