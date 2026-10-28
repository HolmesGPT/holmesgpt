"""The fitted calibration map, separated from the code that fits it.

`validator.py` needs to *apply* a calibration model; `calibration.py` needs the
`validator` to *fit* one, because its training data comes from running the
validator under leave-one-out. Keeping both in one module made that a cycle,
which only worked because the fitting side imported the validator inside a
function - against this repo's rule that imports live at module scope.

Splitting on "the map you apply" versus "how the map is fitted" removes the
cycle rather than hiding it. This module imports nothing from the package, so
anything may depend on it.
"""

import math

from pydantic import BaseModel, Field


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    # Rearranged for negative inputs so exp() cannot overflow.
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


class CalibrationModel(BaseModel):
    """A fitted logistic map from raw evidence score to probability."""

    slope: float = 1.0
    intercept: float = 0.0
    mean: float = Field(default=0.0, description="Training mean, used to standardize the input.")
    std: float = Field(default=1.0, description="Training standard deviation of the input.")
    l2: float = Field(default=0.0, description="Penalty strength chosen by cross-validation.")
    samples: int = Field(default=0, description="Leave-one-out examples the fit was built from.")
    positives: int = Field(default=0, description="How many of those were genuinely missing checks.")
    fitted: bool = False

    def apply(self, raw_confidence: float) -> float:
        """Map a raw score to a probability. Identity until the model is fitted."""
        if not self.fitted:
            return raw_confidence
        standardized = (raw_confidence - self.mean) / self.std
        return sigmoid(self.slope * standardized + self.intercept)

    def describe(self) -> str:
        if not self.fitted:
            return "uncalibrated (raw evidence score passed through unchanged)"
        return (
            f"platt(slope={self.slope:.2f}, intercept={self.intercept:.2f}, l2={self.l2:g}) "
            f"fitted on {self.samples} leave-one-out samples, {self.positives} positive"
        )
