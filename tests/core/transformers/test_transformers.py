"""
Unit tests for transformer base classes and registry.
"""

import pytest
from pydantic import Field

from holmes.core.transformers.base import BaseTransformer, TransformerError
from holmes.core.transformers.registry import TransformerRegistry


class MockTransformer(BaseTransformer):
    """Mock transformer for testing."""

    def transform(self, input_text: str) -> str:
        return f"transformed: {input_text}"

    def should_apply(self, input_text: str) -> bool:
        return len(input_text) > 10

    @property
    def name(self) -> str:
        return "mock"


class ThresholdTransformer(BaseTransformer):
    """Transformer with configurable threshold for testing."""

    threshold: int = Field(
        default=5, ge=0, description="Threshold for triggering transformation"
    )

    def transform(self, input_text: str) -> str:
        return input_text.upper()

    def should_apply(self, input_text: str) -> bool:
        return len(input_text) > self.threshold

    @property
    def name(self) -> str:
        return "threshold"


class FailingTransformer(BaseTransformer):
    """Transformer that always fails for testing error handling."""

    def transform(self, input_text: str) -> str:
        raise RuntimeError("Transformation failed")

    def should_apply(self, input_text: str) -> bool:
        return True

    @property
    def name(self) -> str:
        return "failing"


class TestBaseTransformer:
    """Test cases for BaseTransformer class."""

    def test_init_with_no_config(self):
        """Test transformer initialization without config."""
        transformer = MockTransformer()
        # Pydantic models don't have a 'config' attribute - they have field values directly
        assert hasattr(transformer, "model_fields")

    def test_init_with_config(self):
        """Test transformer initialization with config."""
        # ThresholdTransformer has a threshold field
        transformer = ThresholdTransformer(threshold=100)
        assert transformer.threshold == 100

    def test_name_property(self):
        """Test transformer name property."""
        transformer = MockTransformer()
        assert transformer.name == "mock"

    def test_transform_method(self):
        """Test transform method execution."""
        transformer = MockTransformer()
        result = transformer.transform("test input")
        assert result == "transformed: test input"

    def test_should_apply_method(self):
        """Test should_apply method logic."""
        transformer = MockTransformer()

        # Short input should not apply
        assert not transformer.should_apply("short")

        # Long input should apply
        assert transformer.should_apply("this is a longer input")

    def test_config_validation_success(self):
        """Test successful config validation."""
        transformer = ThresholdTransformer(threshold=100)
        assert transformer.threshold == 100

    def test_config_validation_failure(self):
        """Test config validation failure."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ThresholdTransformer(threshold=-1)

    def test_threshold_transformer_behavior(self):
        """Test transformer with configurable threshold."""
        # Default threshold (5)
        transformer = ThresholdTransformer()
        assert not transformer.should_apply("short")
        assert transformer.should_apply("longer text")

        # Custom threshold (20)
        transformer = ThresholdTransformer(threshold=20)
        assert not transformer.should_apply("short text")
        assert transformer.should_apply("this is a much longer text input")

    def test_abstract_methods_must_be_implemented(self):
        """Test that abstract methods must be implemented."""

        class IncompleteTransformer(BaseTransformer):
            pass

        with pytest.raises(TypeError):
            IncompleteTransformer()  # type: ignore[abstract]


class TestTransformerRegistry:
    """Test cases for TransformerRegistry class."""

    def setup_method(self):
        """Set up test registry for each test."""
        self.registry = TransformerRegistry()

    def test_register_transformer(self):
        """Test registering a transformer."""
        self.registry.register(MockTransformer)
        assert self.registry.is_registered("mock")
        assert "mock" in self.registry.list_transformers()

    def test_register_duplicate_name(self):
        """Test registering transformer with duplicate name fails."""
        self.registry.register(MockTransformer)

        # Create another transformer with the same name
        class DuplicateMockTransformer(BaseTransformer):
            def transform(self, input_text: str) -> str:
                return f"duplicate: {input_text}"

            def should_apply(self, input_text: str) -> bool:
                return True

            @property
            def name(self) -> str:
                return "mock"  # Same name as MockTransformer

        with pytest.raises(
            ValueError, match="Transformer 'mock' is already registered"
        ):
            self.registry.register(DuplicateMockTransformer)

    def test_register_invalid_transformer_class(self):
        """Test registering invalid transformer class fails."""

        class NotATransformer:
            pass

        with pytest.raises(
            ValueError, match="Transformer class must inherit from BaseTransformer"
        ):
            self.registry.register(NotATransformer)  # type: ignore[abstract]

    def test_unregister_transformer(self):
        """Test unregistering a transformer."""
        self.registry.register(MockTransformer)
        assert self.registry.is_registered("mock")

        self.registry.unregister("mock")
        assert not self.registry.is_registered("mock")
        assert "mock" not in self.registry.list_transformers()

    def test_unregister_nonexistent_transformer(self):
        """Test unregistering non-existent transformer fails."""
        with pytest.raises(
            KeyError, match="Transformer 'nonexistent' is not registered"
        ):
            self.registry.unregister("nonexistent")

    def test_create_transformer_success(self):
        """Test successful transformer creation."""
        self.registry.register(MockTransformer)
        transformer = self.registry.create_transformer("mock")

        assert isinstance(transformer, MockTransformer)
        # Pydantic models don't have a config attribute
        assert hasattr(transformer, "model_fields")

    def test_create_transformer_with_config(self):
        """Test transformer creation with config."""
        self.registry.register(ThresholdTransformer)
        config = {"threshold": 15}
        transformer = self.registry.create_transformer("threshold", config)

        assert isinstance(transformer, ThresholdTransformer)
        assert transformer.threshold == 15

    def test_create_transformer_nonexistent(self):
        """Test creating non-existent transformer fails."""
        with pytest.raises(
            KeyError, match="Transformer 'nonexistent' is not registered"
        ):
            self.registry.create_transformer("nonexistent")

    def test_create_transformer_initialization_failure(self):
        """Test transformer creation with initialization failure."""
        self.registry.register(ThresholdTransformer)

        with pytest.raises(
            TransformerError, match="Failed to create transformer 'threshold'"
        ):
            self.registry.create_transformer("threshold", {"threshold": -1})

    def test_is_registered(self):
        """Test checking if transformer is registered."""
        assert not self.registry.is_registered("mock")

        self.registry.register(MockTransformer)
        assert self.registry.is_registered("mock")

    def test_list_transformers_empty(self):
        """Test listing transformers when registry is empty."""
        assert self.registry.list_transformers() == []

    def test_list_transformers_multiple(self):
        """Test listing multiple registered transformers."""
        self.registry.register(MockTransformer)
        self.registry.register(ThresholdTransformer)

        transformers = self.registry.list_transformers()
        assert len(transformers) == 2
        assert "mock" in transformers
        assert "threshold" in transformers

    def test_clear_registry(self):
        """Test clearing all transformers from registry."""
        self.registry.register(MockTransformer)
        self.registry.register(ThresholdTransformer)
        assert len(self.registry.list_transformers()) == 2

        self.registry.clear()
        assert len(self.registry.list_transformers()) == 0

    def test_transformer_creation_preserves_isolation(self):
        """Test that created transformers are independent instances."""
        self.registry.register(ThresholdTransformer)

        transformer1 = self.registry.create_transformer("threshold", {"threshold": 10})
        transformer2 = self.registry.create_transformer("threshold", {"threshold": 20})

        assert transformer1 is not transformer2
        assert transformer1.threshold == 10
        assert transformer2.threshold == 20


class TestTransformerIntegration:
    """Integration tests combining transformers and registry."""

    def setup_method(self):
        """Set up test registry for each test."""
        self.registry = TransformerRegistry()
        self.registry.register(MockTransformer)
        self.registry.register(ThresholdTransformer)

    def test_end_to_end_transformation(self):
        """Test complete transformation workflow."""
        transformer = self.registry.create_transformer("mock")

        input_text = "this is a test input"

        # Check if transformation should apply
        assert transformer.should_apply(input_text)

        # Perform transformation
        result = transformer.transform(input_text)
        assert result == "transformed: this is a test input"

    def test_conditional_transformation(self):
        """Test transformation that conditionally applies."""
        transformer = self.registry.create_transformer("threshold", {"threshold": 20})

        short_text = "short"
        long_text = "this is a much longer text that exceeds the threshold"

        # Short text should not be transformed
        assert not transformer.should_apply(short_text)

        # Long text should be transformed
        assert transformer.should_apply(long_text)
        result = transformer.transform(long_text)
        assert result == long_text.upper()

    def test_multiple_transformer_instances(self):
        """Test using multiple transformer instances simultaneously."""
        mock_transformer = self.registry.create_transformer("mock")
        threshold_transformer = self.registry.create_transformer(
            "threshold", {"threshold": 5}
        )

        test_input = "test input text"

        # Both transformers should apply
        assert mock_transformer.should_apply(test_input)
        assert threshold_transformer.should_apply(test_input)

        # Transform with both
        mock_result = mock_transformer.transform(test_input)
        threshold_result = threshold_transformer.transform(test_input)

        assert mock_result == "transformed: test input text"
        assert threshold_result == "TEST INPUT TEXT"


class TestTransformerError:
    """Test cases for TransformerError exception."""

    def test_transformer_error_creation(self):
        """Test creating TransformerError."""
        error = TransformerError("Test error message")
        assert str(error) == "Test error message"
        assert isinstance(error, Exception)

    def test_transformer_error_in_registry(self):
        """Test TransformerError raised by registry."""
        registry = TransformerRegistry()
        registry.register(FailingTransformer)

        # Creation should succeed
        transformer = registry.create_transformer("failing")

        # But transformation should fail with TransformerError would be caught
        # at the tool execution level, not here. The transformer itself
        # raises RuntimeError, which is expected behavior.
        with pytest.raises(RuntimeError, match="Transformation failed"):
            transformer.transform("test input")
