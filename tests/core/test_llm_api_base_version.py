from unittest.mock import patch

import pytest

from holmes.core.llm import DefaultLLM


class TestDefaultLLMConstructor:
    """Test DefaultLLM constructor with api_base and api_version parameters."""

    def test_constructor_with_all_parameters(self):
        """Test DefaultLLM constructor with all parameters including api_base and api_version."""
        with patch.object(DefaultLLM, "check_llm") as mock_check:
            llm = DefaultLLM(
                model="test-model",
                api_key="test-key",
                api_base="https://test.api.base",
                api_version="2023-12-01",
                args={"param": "value"},
            )

            assert llm.model == "test-model"
            assert llm.api_key == "test-key"
            assert llm.api_base == "https://test.api.base"
            assert llm.api_version == "2023-12-01"
            assert llm.args == {"param": "value"}

            mock_check.assert_called_once_with(
                "test-model",
                "test-key",
                "https://test.api.base",
                "2023-12-01",
                {"param": "value"},
            )

    def test_constructor_with_defaults(self):
        """Test DefaultLLM constructor with default None values for api_base and api_version."""
        with patch.object(DefaultLLM, "check_llm") as mock_check:
            llm = DefaultLLM(model="test-model")

            assert llm.model == "test-model"
            assert llm.api_key is None
            assert llm.api_base is None
            assert llm.api_version is None
            assert llm.args == {}

            mock_check.assert_called_once_with("test-model", None, None, None, {})

    def test_constructor_partial_parameters(self):
        """Test DefaultLLM constructor with some parameters set."""
        with patch.object(DefaultLLM, "check_llm"):
            llm = DefaultLLM(
                model="test-model",
                api_key="test-key",
                api_base="https://test.api.base",
                # api_version not set - should default to None
            )

            assert llm.model == "test-model"
            assert llm.api_key == "test-key"
            assert llm.api_base == "https://test.api.base"
            assert llm.api_version is None
            assert llm.args == {}


class TestDefaultLLMCheckLLM:
    """Test DefaultLLM.check_llm method with api_base and api_version parameters."""

    @patch("litellm.get_llm_provider")
    @patch("litellm.validate_environment")
    def test_check_llm_with_api_base_version(self, mock_validate, mock_get_provider):
        """Test check_llm passes api_base to validate_environment."""
        mock_get_provider.return_value = ("test-model", "openai")
        mock_validate.return_value = {"keys_in_environment": True, "missing_keys": []}

        # Create instance without __init__
        llm = DefaultLLM.__new__(DefaultLLM)
        llm.is_robusta_model = False
        llm.check_llm(
            model="test-model",
            api_key="test-key",
            api_base="https://test.api.base",
            api_version="2023-12-01",
        )

        mock_validate.assert_called_once_with(
            model="test-model", api_key="test-key", api_base="https://test.api.base"
        )

    @patch("litellm.get_llm_provider")
    def test_check_llm_azure_api_version_handling(
        self, mock_get_provider, monkeypatch
    ):
        """Test Azure-specific api_version handling in check_llm."""
        mock_get_provider.return_value = ("azure/gpt-4o", "azure")
        monkeypatch.setenv("AZURE_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_API_BASE", "https://test.api.base")
        monkeypatch.setenv("AZURE_API_VERSION", "2023-12-01")

        llm = DefaultLLM.__new__(DefaultLLM)
        llm.is_robusta_model = False
        llm.model = "azure/gpt-4o"
        # Should not raise exception when all Azure env vars are set
        llm.check_llm(
            model="azure/gpt-4o",
            api_key="test-key",
            api_base="https://test.api.base",
            api_version="2023-12-01",
        )

    @patch("litellm.get_llm_provider")
    @patch("litellm.validate_environment")
    def test_check_llm_azure_other_missing_keys_still_raise(
        self, mock_validate, mock_get_provider
    ):
        """Test Azure provider still raises for other missing keys even with api_version."""
        mock_get_provider.return_value = ("test-model", "azure")
        mock_validate.return_value = {
            "keys_in_environment": False,
            "missing_keys": ["AZURE_OPENAI_ENDPOINT", "AZURE_API_VERSION"],
        }

        llm = DefaultLLM.__new__(DefaultLLM)
        llm.is_robusta_model = False

        with pytest.raises(
            Exception,
            match="model azure/gpt-4o requires the following environment variables",
        ):
            llm.check_llm(
                model="azure/gpt-4o",
                api_key="test-key",
                api_base="https://test.api.base",
                api_version="2023-12-01",
            )

    @patch("litellm.get_llm_provider")
    def test_check_llm_azure_env_vars_remove_missing_keys(
        self, mock_get_provider, monkeypatch
    ):
        """Test Azure provider removes keys from missing_keys when they exist in the environment."""
        mock_get_provider.return_value = ("azure/gpt-4o", "azure")
        # Only set AZURE_API_KEY and AZURE_API_BASE in env, leave AZURE_API_VERSION unset
        monkeypatch.setenv("AZURE_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_API_BASE", "https://test.api.base")
        monkeypatch.delenv("AZURE_API_VERSION", raising=False)

        llm = DefaultLLM.__new__(DefaultLLM)
        llm.is_robusta_model = False
        llm.model = "azure/gpt-4o"

        with pytest.raises(
            Exception,
            match=r"model azure/gpt-4o requires the following environment variables: \['AZURE_API_VERSION'\]",
        ):
            llm.check_llm(
                model="azure/gpt-4o",
                api_key="test-key",
                api_base="https://test.api.base",
                api_version=None,
            )

    @patch("litellm.get_llm_provider")
    def test_check_llm_azure_all_env_vars_set_passes(
        self, mock_get_provider, monkeypatch
    ):
        """Test Azure provider passes when all AZURE_* env vars are set, even if validate_environment reports them missing."""
        mock_get_provider.return_value = ("gpt-4o", "azure")
        monkeypatch.setenv("AZURE_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_API_BASE", "https://test.api.base")
        monkeypatch.setenv("AZURE_API_VERSION", "2024-02-01")

        llm = DefaultLLM.__new__(DefaultLLM)
        llm.is_robusta_model = False
        llm.model = "azure/gpt-4o"
        # Should not raise - env vars cover all required keys
        llm.check_llm(
            model="azure/gpt-4o",
            api_key=None,
            api_base=None,
            api_version=None,
        )

    @patch("litellm.get_llm_provider")
    def test_check_llm_azure_all_config_passes(
        self, mock_get_provider, monkeypatch
    ):
        """Test Azure provider passes when all variables from configuration."""
        mock_get_provider.return_value = ("gpt-4o", "azure")
        monkeypatch.delenv("AZURE_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_API_BASE", raising=False)
        monkeypatch.delenv("AZURE_API_VERSION", raising=False)

        llm = DefaultLLM.__new__(DefaultLLM)
        llm.is_robusta_model = False
        llm.model = "azure/gpt-4o"
        # Should not raise - env vars cover all required keys
        llm.check_llm(
            model="azure/gpt-4o",
            api_key="test-key",
            api_base="https://test.api.base",
            api_version="2024-02-01",
        )

    @patch("holmes.core.llm.AZURE_AD_TOKEN_AUTH", True)
    @patch("litellm.get_llm_provider")
    def test_check_llm_azure_ad_token_auth_removes_api_key_requirement(
        self, mock_get_provider, monkeypatch
    ):
        """Test Azure AD token auth removes AZURE_API_KEY from missing_keys."""
        mock_get_provider.return_value = ("gpt-4o", "azure")
        # Set AZURE_API_BASE and AZURE_API_VERSION but not AZURE_API_KEY
        monkeypatch.delenv("AZURE_API_KEY", raising=False)
        monkeypatch.setenv("AZURE_API_BASE", "https://test.api.base")
        monkeypatch.setenv("AZURE_API_VERSION", "2024-02-01")

        llm = DefaultLLM.__new__(DefaultLLM)
        llm.is_robusta_model = False
        llm.model = "azure/gpt-4o"
        # Should not raise - AZURE_AD_TOKEN_AUTH=True makes AZURE_API_KEY optional
        llm.check_llm(
            model="azure/gpt-4o",
            api_key=None,
            api_base="https://test.api.base",
            api_version="2024-02-01",
        )

    @patch("holmes.core.llm.AZURE_AD_TOKEN_AUTH", False)
    @patch("litellm.get_llm_provider")
    def test_check_llm_azure_no_ad_token_auth_requires_api_key(
        self, mock_get_provider, monkeypatch
    ):
        """Test Azure provider still requires AZURE_API_KEY when AZURE_AD_TOKEN_AUTH is disabled."""
        mock_get_provider.return_value = ("gpt-4o", "azure")
        monkeypatch.delenv("AZURE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("AZURE_API_BASE", "https://test.api.base")
        monkeypatch.setenv("AZURE_API_VERSION", "2024-02-01")

        llm = DefaultLLM.__new__(DefaultLLM)
        llm.is_robusta_model = False
        llm.model = "azure/gpt-4o"

        with pytest.raises(
            Exception,
            match=r"azure/gpt-4o requires the following environment variables: \['AZURE_API_KEY'\]",
        ):
            llm.check_llm(
                model="azure/gpt-4o",
                api_key=None,
                api_base="https://test.api.base",
                api_version="2024-02-01",
            )

    @patch("litellm.get_llm_provider")
    @patch("litellm.validate_environment")
    def test_check_llm_non_azure_provider(self, mock_validate, mock_get_provider):
        """Test check_llm with non-Azure provider doesn't apply special api_version handling."""
        mock_get_provider.return_value = ("test-model", "openai")
        mock_validate.return_value = {
            "keys_in_environment": False,
            "missing_keys": ["OPENAI_API_KEY"],
        }

        llm = DefaultLLM.__new__(DefaultLLM)
        llm.is_robusta_model = False

        with pytest.raises(
            Exception,
            match="model openai/gpt-4o requires the following environment variables",
        ):
            llm.check_llm(
                model="openai/gpt-4o",
                api_key=None,
                api_base="https://test.api.base",
                api_version="2023-12-01",
            )

    @patch("litellm.get_llm_provider")
    def test_check_llm_unknown_provider_raises(self, mock_get_provider):
        """Test check_llm raises exception for unknown provider."""
        mock_get_provider.return_value = None

        llm = DefaultLLM.__new__(DefaultLLM)
        llm.is_robusta_model = False

        with pytest.raises(Exception, match="Unknown provider for model"):
            llm.check_llm(
                model="unknown/model",
                api_key="test-key",
                api_base="https://test.api.base",
                api_version="2023-12-01",
            )

    def test_check_bedrock_model_list_without_env_vars(self):
        """Test Bedrock provider does not raise for model list when env vars are not set up."""
        DefaultLLM(
            "bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0",
            args={"aws_access_key_id": "test", "aws_secret_access_key": "test"},
        )
