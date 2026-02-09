from unittest.mock import Mock, patch

import pytest
import requests  # type: ignore

from holmes.core.tools import StructuredToolResultStatus, ToolInvokeContext
from holmes.plugins.toolsets.http.http_toolset import (
    AuthConfig,
    EndpointConfig,
    HttpRequest,
    HttpToolset,
    HttpToolsetConfig,
)


class TestAuthConfig:
    def test_basic_auth_valid(self):
        auth = AuthConfig(type="basic", username="user", password="pass")
        assert auth.type == "basic"
        assert auth.username == "user"
        assert auth.password == "pass"

    def test_basic_auth_missing_username(self):
        with pytest.raises(ValueError, match="Basic auth requires"):
            AuthConfig(type="basic", password="pass")

    def test_basic_auth_missing_password(self):
        with pytest.raises(ValueError, match="Basic auth requires"):
            AuthConfig(type="basic", username="user")

    def test_bearer_auth_valid(self):
        auth = AuthConfig(type="bearer", token="mytoken")
        assert auth.type == "bearer"
        assert auth.token == "mytoken"

    def test_bearer_auth_missing_token(self):
        with pytest.raises(ValueError, match="Bearer auth requires"):
            AuthConfig(type="bearer")

    def test_header_auth_valid(self):
        auth = AuthConfig(type="header", name="X-API-Key", value="secret")
        assert auth.type == "header"
        assert auth.name == "X-API-Key"
        assert auth.value == "secret"

    def test_header_auth_missing_name(self):
        with pytest.raises(ValueError, match="Header auth requires"):
            AuthConfig(type="header", value="secret")

    def test_header_auth_missing_value(self):
        with pytest.raises(ValueError, match="Header auth requires"):
            AuthConfig(type="header", name="X-API-Key")

    def test_none_auth(self):
        auth = AuthConfig(type="none")
        assert auth.type == "none"


class TestEndpointConfig:
    def test_default_values(self):
        endpoint = EndpointConfig(hosts=["example.com"])
        assert endpoint.hosts == ["example.com"]
        assert endpoint.paths == ["*"]
        assert endpoint.get_methods() == ["GET"]

    def test_multiple_hosts(self):
        endpoint = EndpointConfig(hosts=["api.example.com", "api2.example.com"])
        assert endpoint.hosts == ["api.example.com", "api2.example.com"]

    def test_custom_methods(self):
        endpoint = EndpointConfig(hosts=["example.com"], methods=["GET", "POST", "DELETE"])
        assert endpoint.get_methods() == ["GET", "POST", "DELETE"]

    def test_methods_normalized_to_uppercase(self):
        endpoint = EndpointConfig(hosts=["example.com"], methods=["get", "post", "put"])
        assert endpoint.get_methods() == ["GET", "POST", "PUT"]


class TestHttpToolsetHostMatching:
    @pytest.fixture
    def toolset(self):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[
                EndpointConfig(hosts=["api.github.com"]),
                EndpointConfig(hosts=["*.atlassian.net"]),
                EndpointConfig(hosts=["example.com"], paths=["/api/*"]),
                EndpointConfig(
                    hosts=["argocd.mycompany.com"],
                    paths=["/api/v1/*"],
                    methods=["GET", "POST"],
                ),
            ]
        )
        return ts

    def test_exact_host_match(self, toolset):
        endpoint, error = toolset.match_endpoint("https://api.github.com/repos/foo/bar")
        assert error is None
        assert endpoint is not None
        assert "api.github.com" in endpoint.hosts

    def test_wildcard_host_match(self, toolset):
        endpoint, error = toolset.match_endpoint(
            "https://mycompany.atlassian.net/wiki/rest/api/content"
        )
        assert error is None
        assert endpoint is not None
        assert "*.atlassian.net" in endpoint.hosts

    def test_wildcard_match_multiple_subdomains(self, toolset):
        endpoint, error = toolset.match_endpoint(
            "https://foo.bar.atlassian.net/wiki/rest/api/content"
        )
        assert error is None
        assert endpoint is not None
        assert "*.atlassian.net" in endpoint.hosts

    def test_path_match(self, toolset):
        endpoint, error = toolset.match_endpoint("https://example.com/api/users")
        assert error is None
        assert endpoint is not None

    def test_path_no_match(self, toolset):
        endpoint, error = toolset.match_endpoint("https://example.com/other/path")
        assert error is not None
        assert endpoint is None

    def test_no_host_match(self, toolset):
        endpoint, error = toolset.match_endpoint("https://unknown.com/api")
        assert error is not None
        assert endpoint is None

    def test_invalid_url(self, toolset):
        endpoint, error = toolset.match_endpoint("not-a-url")
        assert error is not None
        assert endpoint is None

    def test_host_match_with_port(self, toolset):
        endpoint, error = toolset.match_endpoint("https://api.github.com:8443/repos/foo/bar")
        assert error is None
        assert endpoint is not None
        assert "api.github.com" in endpoint.hosts


class TestHttpToolsetMethodCheck:
    @pytest.fixture
    def toolset(self):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[
                EndpointConfig(hosts=["readonly.example.com"]),  # Default GET only
                EndpointConfig(
                    hosts=["readwrite.example.com"],
                    methods=["GET", "POST", "PUT", "DELETE"],
                ),
            ]
        )
        return ts

    def test_get_allowed_by_default(self, toolset):
        endpoint, _ = toolset.match_endpoint("https://readonly.example.com/api")
        assert toolset.is_method_allowed("GET", endpoint)

    def test_post_not_allowed_by_default(self, toolset):
        endpoint, _ = toolset.match_endpoint("https://readonly.example.com/api")
        assert not toolset.is_method_allowed("POST", endpoint)

    def test_post_allowed_when_configured(self, toolset):
        endpoint, _ = toolset.match_endpoint("https://readwrite.example.com/api")
        assert toolset.is_method_allowed("POST", endpoint)

    def test_delete_allowed_when_configured(self, toolset):
        endpoint, _ = toolset.match_endpoint("https://readwrite.example.com/api")
        assert toolset.is_method_allowed("DELETE", endpoint)

    def test_put_allowed_when_configured(self, toolset):
        endpoint, _ = toolset.match_endpoint("https://readwrite.example.com/api")
        assert toolset.is_method_allowed("PUT", endpoint)

    def test_method_check_case_insensitive(self, toolset):
        endpoint, _ = toolset.match_endpoint("https://readwrite.example.com/api")
        assert toolset.is_method_allowed("post", endpoint)
        assert toolset.is_method_allowed("Post", endpoint)


class TestHttpToolsetHeaders:
    @pytest.fixture
    def toolset(self):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[EndpointConfig(hosts=["example.com"])]
        )
        return ts

    def test_bearer_auth_headers(self, toolset):
        endpoint = EndpointConfig(
            hosts=["example.com"], auth=AuthConfig(type="bearer", token="mytoken")
        )
        headers = toolset.build_headers(endpoint)
        assert headers["Authorization"] == "Bearer mytoken"

    def test_custom_header_auth(self, toolset):
        endpoint = EndpointConfig(
            hosts=["example.com"],
            auth=AuthConfig(type="header", name="X-API-Key", value="secret"),
        )
        headers = toolset.build_headers(endpoint)
        assert headers["X-API-Key"] == "secret"

    def test_basic_auth_not_in_headers(self, toolset):
        endpoint = EndpointConfig(
            hosts=["example.com"],
            auth=AuthConfig(type="basic", username="user", password="pass"),
        )
        headers = toolset.build_headers(endpoint)
        assert "Authorization" not in headers

    def test_basic_auth_tuple(self):
        toolset = HttpToolset()
        endpoint = EndpointConfig(
            hosts=["example.com"],
            auth=AuthConfig(type="basic", username="user", password="pass"),
        )
        auth = toolset.get_basic_auth(endpoint)
        assert auth == ("user", "pass")

    def test_extra_headers_override(self, toolset):
        endpoint = EndpointConfig(hosts=["example.com"], auth=AuthConfig(type="none"))
        headers = toolset.build_headers(endpoint, {"Accept": "text/plain"})
        assert headers["Accept"] == "text/plain"

    def test_default_headers_applied(self):
        toolset = HttpToolset()
        toolset._http_config = HttpToolsetConfig(
            endpoints=[EndpointConfig(hosts=["example.com"])],
            default_headers={"X-Custom-Header": "custom-value"},
        )
        endpoint = EndpointConfig(hosts=["example.com"], auth=AuthConfig(type="none"))
        headers = toolset.build_headers(endpoint)
        assert headers["X-Custom-Header"] == "custom-value"
        assert headers["Accept"] == "application/json"

    def test_extra_headers_override_default_headers(self):
        toolset = HttpToolset()
        toolset._http_config = HttpToolsetConfig(
            endpoints=[EndpointConfig(hosts=["example.com"])],
            default_headers={"X-Custom-Header": "default-value"},
        )
        endpoint = EndpointConfig(hosts=["example.com"], auth=AuthConfig(type="none"))
        headers = toolset.build_headers(
            endpoint, {"X-Custom-Header": "overridden-value"}
        )
        assert headers["X-Custom-Header"] == "overridden-value"


class TestHttpToolsetPrerequisites:
    def test_valid_config(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "hosts": ["example.com"],
                        "auth": {"type": "bearer", "token": "test"},
                    }
                ]
            }
        )
        assert success is True
        assert "1 endpoint" in message

    def test_empty_endpoints(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable({"endpoints": []})
        assert success is False
        assert "No endpoints configured" in message

    def test_invalid_auth(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {"endpoints": [{"hosts": ["example.com"], "auth": {"type": "basic"}}]}
        )
        assert success is False
        assert "Failed to validate" in message

    def test_invalid_method(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "hosts": ["example.com"],
                        "methods": ["GET", "INVALID"],
                    }
                ]
            }
        )
        assert success is False
        assert "invalid method" in message

    def test_multiple_endpoints_with_different_auth(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "hosts": ["api1.example.com"],
                        "auth": {"type": "bearer", "token": "token1"},
                    },
                    {
                        "hosts": ["api2.example.com"],
                        "auth": {
                            "type": "basic",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                    {
                        "hosts": ["api3.example.com"],
                        "auth": {"type": "header", "name": "X-API-Key", "value": "key"},
                    },
                ]
            }
        )
        assert success is True
        assert "3 endpoint" in message
        assert "3 host pattern" in message

    def test_endpoint_with_multiple_hosts(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "hosts": ["api1.example.com", "api2.example.com"],
                        "auth": {"type": "bearer", "token": "shared-token"},
                    }
                ]
            }
        )
        assert success is True
        assert "1 endpoint" in message
        assert "2 host pattern" in message


class TestHttpToolsetNaming:
    def test_default_tool_name(self):
        toolset = HttpToolset()
        assert toolset._derive_tool_name() == "http_request"

    def test_custom_name_with_slash(self):
        toolset = HttpToolset(name="http/confluence")
        assert toolset._derive_tool_name() == "http_confluence_request"

    def test_custom_name_with_dash(self):
        toolset = HttpToolset(name="my-api")
        assert toolset._derive_tool_name() == "my_api_request"

    def test_custom_name_simple(self):
        toolset = HttpToolset(name="confluence")
        assert toolset._derive_tool_name() == "confluence_request"

    def test_tool_name_set_after_prerequisites(self):
        toolset = HttpToolset(name="dagster")
        toolset.prerequisites_callable(
            {
                "endpoints": [
                    {"hosts": ["dagster.example.com"], "auth": {"type": "none"}}
                ]
            }
        )
        assert len(toolset.tools) == 1
        assert toolset.tools[0].name == "dagster_request"


class TestHttpToolsetMultiInstance:
    def test_two_instances_different_names(self):
        ts1 = HttpToolset(name="confluence")
        ts1.prerequisites_callable(
            {
                "endpoints": [
                    {"hosts": ["*.atlassian.net"], "auth": {"type": "none"}}
                ]
            }
        )

        ts2 = HttpToolset(name="dagster")
        ts2.prerequisites_callable(
            {
                "endpoints": [
                    {"hosts": ["dagster.internal.com"], "auth": {"type": "none"}}
                ]
            }
        )

        assert ts1.tools[0].name == "confluence_request"
        assert ts2.tools[0].name == "dagster_request"
        assert ts1.name != ts2.name

    def test_instance_with_llm_instructions(self):
        toolset = HttpToolset(
            name="confluence", llm_instructions="Use Confluence REST API v2."
        )
        toolset.prerequisites_callable(
            {
                "endpoints": [
                    {"hosts": ["*.atlassian.net"], "auth": {"type": "none"}}
                ]
            }
        )
        assert "Use Confluence REST API v2." in toolset.llm_instructions


class TestHttpRequest:
    @pytest.fixture
    def toolset(self):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[
                EndpointConfig(hosts=["api.example.com"], auth=AuthConfig(type="none"))
            ]
        )
        ts.tools = [HttpRequest(ts, tool_name="http_request")]
        return ts

    @pytest.fixture
    def mock_context(self):
        return Mock(spec=ToolInvokeContext)

    def test_headers_must_be_dict(self, toolset, mock_context):
        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test", "headers": '["value1", "value2"]'},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "must be a JSON object" in result.error

    def test_headers_invalid_json(self, toolset, mock_context):
        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test", "headers": "not-valid-json"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid headers JSON" in result.error

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_error_response_includes_error_field(self, mock_request, toolset, mock_context):
        mock_response = Mock()
        mock_response.ok = False
        mock_response.status_code = 404
        mock_response.json.return_value = {"message": "Not found"}
        mock_request.return_value = mock_response

        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert result.error is not None
        assert "HTTP 404" in result.error
        assert result.data["status_code"] == 404

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_success_response_no_error_field(self, mock_request, toolset, mock_context):
        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}
        mock_request.return_value = mock_response

        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.error is None
        assert result.data["status_code"] == 200
        assert result.data["body"]["data"] == "test"

    def test_url_not_whitelisted(self, toolset, mock_context):
        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://not-whitelisted.com/test"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in whitelist" in result.error

    def test_unsupported_method(self, toolset, mock_context):
        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test", "method": "FOOBAR"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Unsupported HTTP method" in result.error

    def test_method_not_allowed_for_endpoint(self, toolset, mock_context):
        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test", "method": "POST"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not allowed for this endpoint" in result.error

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_post_with_body(self, mock_request, mock_context):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[
                EndpointConfig(
                    hosts=["api.example.com"],
                    methods=["GET", "POST"],
                    auth=AuthConfig(type="none"),
                )
            ]
        )
        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 1}
        mock_request.return_value = mock_response

        tool = HttpRequest(ts)
        result = tool._invoke(
            {
                "url": "https://api.example.com/items",
                "method": "POST",
                "body": '{"name": "test"}',
            },
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["status_code"] == 201
        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args
        assert call_kwargs[0][0] == "POST"
        assert call_kwargs[1]["data"] == '{"name": "test"}'

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_put_method(self, mock_request, mock_context):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[
                EndpointConfig(
                    hosts=["api.example.com"],
                    methods=["PUT"],
                    auth=AuthConfig(type="none"),
                )
            ]
        )
        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {"updated": True}
        mock_request.return_value = mock_response

        tool = HttpRequest(ts)
        result = tool._invoke(
            {
                "url": "https://api.example.com/items/1",
                "method": "PUT",
                "body": '{"name": "updated"}',
            },
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        call_kwargs = mock_request.call_args
        assert call_kwargs[0][0] == "PUT"

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_delete_method(self, mock_request, mock_context):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[
                EndpointConfig(
                    hosts=["api.example.com"],
                    methods=["DELETE"],
                    auth=AuthConfig(type="none"),
                )
            ]
        )
        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 204
        mock_response.json.side_effect = Exception("No content")
        mock_response.text = ""
        mock_request.return_value = mock_response

        tool = HttpRequest(ts)
        result = tool._invoke(
            {"url": "https://api.example.com/items/1", "method": "DELETE"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        call_kwargs = mock_request.call_args
        assert call_kwargs[0][0] == "DELETE"

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_timeout_error(self, mock_request, toolset, mock_context):
        mock_request.side_effect = requests.exceptions.Timeout("timed out")
        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "timed out" in result.error

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_connection_error(self, mock_request, toolset, mock_context):
        mock_request.side_effect = requests.exceptions.ConnectionError("refused")
        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Connection error" in result.error


class TestHttpToolsetHealthCheck:
    @patch("holmes.plugins.toolsets.http.http_toolset.requests.get")
    def test_health_check_success(self, mock_get):
        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "hosts": ["api.example.com"],
                        "auth": {"type": "bearer", "token": "test"},
                        "health_check_url": "https://api.example.com/health",
                    }
                ]
            }
        )
        assert success is True
        mock_get.assert_called_once()

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.get")
    def test_health_check_failure(self, mock_get):
        mock_response = Mock()
        mock_response.ok = False
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_get.return_value = mock_response

        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "hosts": ["api.example.com"],
                        "auth": {"type": "bearer", "token": "bad-token"},
                        "health_check_url": "https://api.example.com/health",
                    }
                ]
            }
        )
        assert success is False
        assert "Health check failed" in message
        assert "HTTP 401" in message
        assert "To troubleshoot, run: curl" in message
        assert "$TOKEN" in message

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.get")
    def test_health_check_connection_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "hosts": ["api.example.com"],
                        "auth": {"type": "bearer", "token": "test"},
                        "health_check_url": "https://api.example.com/health",
                    }
                ]
            }
        )
        assert success is False
        assert "Health check failed" in message
        assert "Connection error" in message
        assert "To troubleshoot, run: curl" in message

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.get")
    def test_health_check_timeout(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("Request timed out")

        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "hosts": ["api.example.com"],
                        "auth": {"type": "bearer", "token": "test"},
                        "health_check_url": "https://api.example.com/health",
                    }
                ]
            }
        )
        assert success is False
        assert "Health check failed" in message
        assert "timed out" in message

    def test_no_health_check_skips_request(self):
        toolset = HttpToolset()
        with patch(
            "holmes.plugins.toolsets.http.http_toolset.requests.get"
        ) as mock_get:
            success, message = toolset.prerequisites_callable(
                {
                    "endpoints": [
                        {
                            "hosts": ["api.example.com"],
                            "auth": {"type": "bearer", "token": "test"},
                        }
                    ]
                }
            )
            assert success is True
            mock_get.assert_not_called()


class TestHttpRequestOneLiner:
    def test_short_url(self):
        ts = HttpToolset()
        tool = HttpRequest(ts)
        result = tool.get_parameterized_one_liner(
            {"url": "https://api.example.com/test", "method": "GET"}
        )
        assert result == "HTTP GET https://api.example.com/test"

    def test_long_url_truncated(self):
        ts = HttpToolset()
        tool = HttpRequest(ts)
        long_url = "https://api.example.com/" + "a" * 100
        result = tool.get_parameterized_one_liner({"url": long_url})
        assert len(result) < len(long_url) + 20
        assert "..." in result

    def test_default_method(self):
        ts = HttpToolset()
        tool = HttpRequest(ts)
        result = tool.get_parameterized_one_liner({"url": "https://api.example.com/test"})
        assert result.startswith("HTTP GET")
