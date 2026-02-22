from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from holmes.core.tools import ToolInvokeContext, ToolsetStatusEnum
from holmes.plugins.toolsets.confluence.confluence import (
    ATLASSIAN_GATEWAY_BASE,
    ConfluenceConfig,
    ConfluenceToolset,
    GetChildPages,
    GetComments,
    GetPage,
    ListSpaces,
    SearchPages,
)


@pytest.fixture()
def toolset():
    ts = ConfluenceToolset()
    ts.config = ConfluenceConfig(
        api_url="https://test.atlassian.net",
        user="user@test.com",
        api_key="fake-token",
    )
    ts.status = ToolsetStatusEnum.ENABLED
    return ts


@pytest.fixture()
def context():
    return Mock(spec=ToolInvokeContext)


# ---------------------------------------------------------------------------
# GetPage
# ---------------------------------------------------------------------------


class TestGetPage:
    def test_get_by_content_id(self, toolset, context):
        tool = GetPage(toolset)
        mock_response = {
            "id": "12345",
            "title": "My Page",
            "body": {"storage": {"value": "<p>Hello</p>"}},
            "version": {"number": 3},
            "space": {"key": "SRE"},
            "ancestors": [{"id": "100", "title": "Parent"}],
            "childTypes": {"page": {"value": True}, "comment": {"value": False}, "attachment": {"value": False}},
            "_links": {"self": "http://x"},
            "_expandable": {},
        }
        with patch.object(ConfluenceToolset, "make_request", return_value=mock_response) as mock_req:
            result = tool._invoke({"content_id": "12345"}, context)

            assert result.status.value == "success"
            assert result.data["title"] == "My Page"
            assert "body_markdown" in result.data
            assert "Hello" in result.data["body_markdown"]
            assert result.data["ancestors"] == [{"id": "100", "title": "Parent"}]
            assert result.data["childTypes"]["page"]["value"] is True
            assert "_links" not in result.data
            assert "_expandable" not in result.data

            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert "/rest/api/content/12345" in call_args[0]
            assert "ancestors" in call_args[1]["query_params"]["expand"]
            assert "childTypes.all" in call_args[1]["query_params"]["expand"]

    def test_get_by_title_and_space_key(self, toolset, context):
        tool = GetPage(toolset)
        with patch.object(ConfluenceToolset, "make_request", side_effect=[
            {"results": [{"id": "999"}]},
            {
                "id": "999",
                "title": "Found Page",
                "body": {"storage": {"value": "<p>Content</p>"}},
                "version": {"number": 1},
                "space": {"key": "ENG"},
                "ancestors": [],
                "childTypes": {},
            },
        ]) as mock_req:
            result = tool._invoke({"title": "Found Page", "space_key": "ENG"}, context)

            assert result.status.value == "success"
            assert result.data["title"] == "Found Page"
            assert mock_req.call_count == 2
            first_call = mock_req.call_args_list[0]
            assert "/rest/api/content" in first_call[0][0]
            assert first_call[1]["query_params"]["title"] == "Found Page"
            assert first_call[1]["query_params"]["spaceKey"] == "ENG"

    def test_title_lookup_not_found(self, toolset, context):
        tool = GetPage(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": []}):
            result = tool._invoke({"title": "Missing", "space_key": "X"}, context)

            assert result.status.value == "error"
            assert "No page found" in result.error

    def test_neither_id_nor_title(self, toolset, context):
        tool = GetPage(toolset)
        result = tool._invoke({}, context)

        assert result.status.value == "error"
        assert "content_id" in result.error

    def test_include_body_false(self, toolset, context):
        tool = GetPage(toolset)
        mock_response = {
            "id": "12345",
            "title": "Lightweight",
            "version": {"number": 1},
            "space": {"key": "SRE"},
            "ancestors": [],
            "childTypes": {"page": {"value": True}},
        }
        with patch.object(ConfluenceToolset, "make_request", return_value=mock_response) as mock_req:
            result = tool._invoke({"content_id": "12345", "include_body": False}, context)

            assert result.status.value == "success"
            expand = mock_req.call_args[1]["query_params"]["expand"]
            assert "body.storage" not in expand
            assert "ancestors" in expand

    def test_http_error(self, toolset, context):
        tool = GetPage(toolset)
        resp = MagicMock()
        resp.status_code = 404
        resp.text = "Not Found"
        with patch.object(ConfluenceToolset, "make_request", side_effect=requests.exceptions.HTTPError(response=resp)):
            result = tool._invoke({"content_id": "999"}, context)

            assert result.status.value == "error"
            assert "404" in result.error

    def test_one_liner_with_content_id(self, toolset):
        tool = GetPage(toolset)
        assert "12345" in tool.get_parameterized_one_liner({"content_id": "12345"})

    def test_one_liner_with_title(self, toolset):
        tool = GetPage(toolset)
        assert "My Page" in tool.get_parameterized_one_liner({"title": "My Page"})


# ---------------------------------------------------------------------------
# SearchPages
# ---------------------------------------------------------------------------


class TestSearchPages:
    def test_basic_search(self, toolset, context):
        tool = SearchPages(toolset)
        mock_response = {
            "results": [
                {"id": "1", "title": "Page A", "_links": {}, "_expandable": {}},
                {"id": "2", "title": "Page B", "_links": {}, "_expandable": {}},
            ],
            "totalSize": 2,
        }
        with patch.object(ConfluenceToolset, "make_request", return_value=mock_response):
            result = tool._invoke({"cql": 'text~"test"'}, context)

            assert result.status.value == "success"
            assert result.data["total_size"] == 2
            assert len(result.data["results"]) == 2
            assert result.data["start"] == 0
            assert result.data["limit"] == 10

    def test_pagination(self, toolset, context):
        tool = SearchPages(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "totalSize": 50}) as mock_req:
            result = tool._invoke({"cql": "space=SRE", "start": 20, "limit": 10}, context)

            assert result.status.value == "success"
            assert result.data["start"] == 20
            assert result.data["limit"] == 10
            call_params = mock_req.call_args[1]["query_params"]
            assert call_params["start"] == "20"
            assert call_params["limit"] == "10"

    def test_limit_capped_at_50(self, toolset, context):
        tool = SearchPages(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "totalSize": 0}) as mock_req:
            tool._invoke({"cql": "space=SRE", "limit": 200}, context)

            call_params = mock_req.call_args[1]["query_params"]
            assert call_params["limit"] == "50"


# ---------------------------------------------------------------------------
# ListSpaces
# ---------------------------------------------------------------------------


class TestListSpaces:
    def test_list_all_spaces(self, toolset, context):
        tool = ListSpaces(toolset)
        mock_response = {
            "results": [
                {"key": "SRE", "name": "SRE Team", "_links": {}},
                {"key": "ENG", "name": "Engineering", "_links": {}},
            ],
            "size": 2,
        }
        with patch.object(ConfluenceToolset, "make_request", return_value=mock_response):
            result = tool._invoke({}, context)

            assert result.status.value == "success"
            assert len(result.data["results"]) == 2
            assert result.data["results"][0]["key"] == "SRE"
            assert "_links" not in result.data["results"][0]

    def test_filter_by_type(self, toolset, context):
        tool = ListSpaces(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "size": 0}) as mock_req:
            tool._invoke({"type": "global"}, context)

            call_params = mock_req.call_args[1]["query_params"]
            assert call_params["type"] == "global"

    def test_filter_by_status(self, toolset, context):
        tool = ListSpaces(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "size": 0}) as mock_req:
            tool._invoke({"status": "archived"}, context)

            call_params = mock_req.call_args[1]["query_params"]
            assert call_params["status"] == "archived"

    def test_filter_by_label(self, toolset, context):
        tool = ListSpaces(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "size": 0}) as mock_req:
            tool._invoke({"label": "production"}, context)

            call_params = mock_req.call_args[1]["query_params"]
            assert call_params["label"] == "production"

    def test_pagination(self, toolset, context):
        tool = ListSpaces(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "size": 0}) as mock_req:
            tool._invoke({"start": 25, "limit": 10}, context)

            call_params = mock_req.call_args[1]["query_params"]
            assert call_params["start"] == "25"
            assert call_params["limit"] == "10"

    def test_limit_capped_at_100(self, toolset, context):
        tool = ListSpaces(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "size": 0}) as mock_req:
            tool._invoke({"limit": 500}, context)

            call_params = mock_req.call_args[1]["query_params"]
            assert call_params["limit"] == "100"

    def test_expands_description(self, toolset, context):
        tool = ListSpaces(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "size": 0}) as mock_req:
            tool._invoke({}, context)

            call_params = mock_req.call_args[1]["query_params"]
            assert "description.plain" in call_params["expand"]

    def test_one_liner_with_filters(self, toolset):
        tool = ListSpaces(toolset)
        one_liner = tool.get_parameterized_one_liner({"type": "global", "label": "sre"})
        assert "type=global" in one_liner
        assert "label=sre" in one_liner

    def test_one_liner_no_filters(self, toolset):
        tool = ListSpaces(toolset)
        one_liner = tool.get_parameterized_one_liner({})
        assert "List Confluence spaces" in one_liner


# ---------------------------------------------------------------------------
# GetChildPages
# ---------------------------------------------------------------------------


class TestGetChildPages:
    def test_get_children(self, toolset, context):
        tool = GetChildPages(toolset)
        mock_response = {
            "results": [
                {"id": "10", "title": "Child 1", "version": {"number": 1}, "_links": {}, "_expandable": {}},
                {"id": "11", "title": "Child 2", "version": {"number": 2}, "_links": {}, "_expandable": {}},
            ],
            "size": 2,
        }
        with patch.object(ConfluenceToolset, "make_request", return_value=mock_response):
            result = tool._invoke({"content_id": "100"}, context)

            assert result.status.value == "success"
            assert result.data["parent_id"] == "100"
            assert len(result.data["results"]) == 2
            assert result.data["results"][0]["title"] == "Child 1"
            assert "_links" not in result.data["results"][0]

    def test_pagination(self, toolset, context):
        tool = GetChildPages(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "size": 0}) as mock_req:
            tool._invoke({"content_id": "100", "start": 25, "limit": 10}, context)

            call_params = mock_req.call_args[1]["query_params"]
            assert call_params["start"] == "25"
            assert call_params["limit"] == "10"

    def test_expand_body(self, toolset, context):
        tool = GetChildPages(toolset)
        mock_response = {
            "results": [
                {
                    "id": "10",
                    "title": "Child",
                    "body": {"storage": {"value": "<p>Body</p>"}},
                    "version": {"number": 1},
                },
            ],
            "size": 1,
        }
        with patch.object(ConfluenceToolset, "make_request", return_value=mock_response) as mock_req:
            result = tool._invoke({"content_id": "100", "expand_body": True}, context)

            assert result.status.value == "success"
            assert "body_markdown" in result.data["results"][0]
            expand = mock_req.call_args[1]["query_params"]["expand"]
            assert "body.storage" in expand

    def test_http_error(self, toolset, context):
        tool = GetChildPages(toolset)
        resp = MagicMock()
        resp.status_code = 404
        resp.text = "Content not found"
        with patch.object(ConfluenceToolset, "make_request", side_effect=requests.exceptions.HTTPError(response=resp)):
            result = tool._invoke({"content_id": "999"}, context)

            assert result.status.value == "error"
            assert "404" in result.error

    def test_one_liner(self, toolset):
        tool = GetChildPages(toolset)
        assert "100" in tool.get_parameterized_one_liner({"content_id": "100"})


# ---------------------------------------------------------------------------
# GetComments
# ---------------------------------------------------------------------------


class TestGetComments:
    def test_get_comments(self, toolset, context):
        tool = GetComments(toolset)
        mock_response = {
            "results": [
                {
                    "id": "50",
                    "title": "Re: Page",
                    "body": {"storage": {"value": "<p>Great page!</p>"}},
                    "version": {"number": 1},
                    "_links": {},
                    "_expandable": {},
                },
            ],
            "size": 1,
        }
        with patch.object(ConfluenceToolset, "make_request", return_value=mock_response):
            result = tool._invoke({"content_id": "100"}, context)

            assert result.status.value == "success"
            assert result.data["page_id"] == "100"
            assert len(result.data["results"]) == 1
            assert "body_markdown" in result.data["results"][0]
            assert "Great page!" in result.data["results"][0]["body_markdown"]
            assert "_links" not in result.data["results"][0]

    def test_pagination(self, toolset, context):
        tool = GetComments(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "size": 0}) as mock_req:
            tool._invoke({"content_id": "100", "start": 25, "limit": 5}, context)

            call_params = mock_req.call_args[1]["query_params"]
            assert call_params["start"] == "25"
            assert call_params["limit"] == "5"

    def test_expands_body_and_version(self, toolset, context):
        tool = GetComments(toolset)
        with patch.object(ConfluenceToolset, "make_request", return_value={"results": [], "size": 0}) as mock_req:
            tool._invoke({"content_id": "100"}, context)

            expand = mock_req.call_args[1]["query_params"]["expand"]
            assert "body.storage" in expand
            assert "version" in expand

    def test_http_error(self, toolset, context):
        tool = GetComments(toolset)
        resp = MagicMock()
        resp.status_code = 403
        resp.text = "Forbidden"
        with patch.object(ConfluenceToolset, "make_request", side_effect=requests.exceptions.HTTPError(response=resp)):
            result = tool._invoke({"content_id": "100"}, context)

            assert result.status.value == "error"
            assert "403" in result.error

    def test_one_liner(self, toolset):
        tool = GetComments(toolset)
        assert "100" in tool.get_parameterized_one_liner({"content_id": "100"})


# ---------------------------------------------------------------------------
# Gateway auto-detection for scoped tokens
# ---------------------------------------------------------------------------


class TestGatewayAutoDetection:
    def test_direct_url_used_when_no_gateway_needed(self, toolset):
        """When direct URL works, no gateway is activated."""
        url = toolset._build_url("/rest/api/space")
        assert url.startswith("https://test.atlassian.net/wiki/rest/api/space")
        assert toolset._gateway_base_url is None

    def test_gateway_activated_with_explicit_cloud_id(self):
        """When cloud_id is configured, gateway is activated during health check."""
        ts = ConfluenceToolset()
        config = {
            "api_url": "https://mycompany.atlassian.net",
            "user": "user@test.com",
            "api_key": "scoped-token",
            "cloud_id": "abc-123",
        }

        with patch.object(ConfluenceToolset, "make_request", return_value={"results": []}):
            ok, msg = ts.prerequisites_callable(config)

        assert ok is True
        assert ts._gateway_base_url == f"{ATLASSIAN_GATEWAY_BASE}/abc-123"

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_gateway_fallback_on_auth_error(self, status_code):
        """When direct URL returns 401 or 403, auto-detect cloud_id and switch to gateway."""
        ts = ConfluenceToolset()
        config = {
            "api_url": "https://mycompany.atlassian.net",
            "user": "user@test.com",
            "api_key": "scoped-token",
        }

        # First call (direct) fails, second call (gateway) succeeds
        error_resp = MagicMock()
        error_resp.status_code = status_code
        error_resp.text = '{"message":"auth error"}'

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise requests.exceptions.HTTPError(response=error_resp)
            return {"results": []}

        tenant_resp = MagicMock()
        tenant_resp.status_code = 200
        tenant_resp.json.return_value = {"cloudId": "detected-cloud-id"}
        tenant_resp.raise_for_status.return_value = None

        with (
            patch.object(ConfluenceToolset, "make_request", side_effect=side_effect),
            patch("holmes.plugins.toolsets.confluence.confluence.requests.get", return_value=tenant_resp),
        ):
            ok, msg = ts.prerequisites_callable(config)

        assert ok is True
        assert "gateway" in msg.lower()
        assert ts._gateway_base_url == f"{ATLASSIAN_GATEWAY_BASE}/detected-cloud-id"

    def test_gateway_url_used_in_build_url(self):
        """Once gateway is activated, _build_url uses the gateway base URL."""
        ts = ConfluenceToolset()
        ts.config = ConfluenceConfig(
            api_url="https://mycompany.atlassian.net",
            user="user@test.com",
            api_key="token",
        )
        ts._gateway_base_url = f"{ATLASSIAN_GATEWAY_BASE}/my-cloud-id"

        url = ts._build_url("/rest/api/space")
        assert url.startswith(f"{ATLASSIAN_GATEWAY_BASE}/my-cloud-id/wiki/rest/api/space")
        assert "mycompany.atlassian.net" not in url

    def test_no_gateway_for_data_center(self):
        """Data Center URLs (non-atlassian.net) should never trigger gateway fallback."""
        ts = ConfluenceToolset()
        config = {
            "api_url": "https://confluence.mycompany.com",
            "api_key": "pat-token",
            "auth_type": "bearer",
            "api_path_prefix": "",
        }

        forbidden_resp = MagicMock()
        forbidden_resp.status_code = 403
        forbidden_resp.text = "Forbidden"

        with patch.object(
            ConfluenceToolset,
            "make_request",
            side_effect=requests.exceptions.HTTPError(response=forbidden_resp),
        ):
            ok, msg = ts.prerequisites_callable(config)

        assert ok is False
        assert ts._gateway_base_url is None

    def test_gateway_fallback_fails_gracefully(self):
        """If gateway fallback also fails, report the original error."""
        ts = ConfluenceToolset()
        config = {
            "api_url": "https://mycompany.atlassian.net",
            "user": "user@test.com",
            "api_key": "bad-token",
        }

        forbidden_resp = MagicMock()
        forbidden_resp.status_code = 403
        forbidden_resp.text = '{"message":"Current user not permitted to use Confluence"}'

        # Both direct and gateway calls fail with 403
        with (
            patch.object(
                ConfluenceToolset,
                "make_request",
                side_effect=requests.exceptions.HTTPError(response=forbidden_resp),
            ),
            patch("holmes.plugins.toolsets.confluence.confluence.requests.get") as mock_get,
        ):
            tenant_resp = MagicMock()
            tenant_resp.status_code = 200
            tenant_resp.json.return_value = {"cloudId": "some-id"}
            tenant_resp.raise_for_status.return_value = None
            mock_get.return_value = tenant_resp

            ok, msg = ts.prerequisites_callable(config)

        assert ok is False
        assert ts._gateway_base_url is None

    def test_cloud_id_resolution_failure(self):
        """If cloud_id cannot be resolved, fallback fails gracefully."""
        ts = ConfluenceToolset()
        config = {
            "api_url": "https://mycompany.atlassian.net",
            "user": "user@test.com",
            "api_key": "scoped-token",
        }

        forbidden_resp = MagicMock()
        forbidden_resp.status_code = 403
        forbidden_resp.text = '{"message":"Current user not permitted to use Confluence"}'

        with (
            patch.object(
                ConfluenceToolset,
                "make_request",
                side_effect=requests.exceptions.HTTPError(response=forbidden_resp),
            ),
            patch(
                "holmes.plugins.toolsets.confluence.confluence.requests.get",
                side_effect=requests.exceptions.ConnectionError("DNS failure"),
            ),
        ):
            ok, msg = ts.prerequisites_callable(config)

        assert ok is False
        assert ts._gateway_base_url is None
