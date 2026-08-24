"""Calibration: does the confidence number mean what it says?

The raw evidence score is a product of four terms below 1, so it reads far lower
than the real hit rate. These tests pin down that the fit corrects that, refuses
to fit when there is no signal, and does not quietly become over-confident on a
small sample.
"""

import ast
import pathlib

import pytest

from holmes.core.investigation_path.calibration import (
    build_calibration_samples,
    fit_calibration,
    fit_platt,
)
from holmes.core.investigation_path.calibration_model import CalibrationModel
from holmes.core.investigation_path.corpus import load_corpus
from holmes.core.investigation_path.metrics import expected_calibration_error

PACKAGE_DIR = pathlib.Path(__file__).parents[3] / "holmes" / "core" / "investigation_path"


def import_graph():
    """Which modules in the package import which, read from the source."""
    graph = {}
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        deps = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                if "investigation_path" in node.module:
                    deps.add(node.module.rsplit(".", 1)[-1])
        graph[path.stem] = deps
    return graph


def separable_sample(n=60):
    """Low scores mostly wrong, high scores mostly right - like the real data."""
    raw = [0.2] * n + [0.5] * n
    labels = [False] * n + [True] * n
    return raw, labels


class TestFitting:
    def test_a_fitted_model_separates_the_two_score_levels(self):
        model = fit_platt(*separable_sample())
        assert model.fitted
        assert model.apply(0.5) > 0.8
        assert model.apply(0.2) < 0.2

    def test_the_map_is_monotone(self):
        model = fit_platt(*separable_sample())
        probabilities = [model.apply(x / 100) for x in range(0, 101)]
        assert probabilities == sorted(probabilities)

    def test_output_is_always_a_probability(self):
        model = fit_platt(*separable_sample())
        for x in (-5.0, 0.0, 0.5, 1.0, 100.0):
            assert 0.0 <= model.apply(x) <= 1.0

    def test_the_fit_is_deterministic(self):
        first = fit_platt(*separable_sample())
        second = fit_platt(*separable_sample())
        assert first.slope == second.slope
        assert first.intercept == second.intercept
        assert first.l2 == second.l2

    def test_the_penalty_is_chosen_by_cross_validation(self):
        model = fit_platt(*separable_sample())
        assert model.l2 > 0

    def test_an_explicit_penalty_overrides_the_search(self):
        assert fit_platt(*separable_sample(), l2=3.0).l2 == 3.0

    def test_standardizing_makes_the_fit_independent_of_input_scale(self):
        """The first attempt used un-standardized inputs; a fixed penalty then
        crushed the slope because raw scores sit in a narrow band near zero."""
        raw, labels = separable_sample()
        scaled = fit_platt([r / 100 for r in raw], labels)
        normal = fit_platt(raw, labels)
        assert scaled.apply(0.005) == pytest.approx(normal.apply(0.5), abs=0.05)


class TestRefusingToFit:
    def test_all_positive_labels_will_not_fit(self):
        model = fit_platt([0.1, 0.5, 0.9], [True, True, True])
        assert not model.fitted

    def test_all_negative_labels_will_not_fit(self):
        model = fit_platt([0.1, 0.5, 0.9], [False, False, False])
        assert not model.fitted

    def test_a_single_sample_will_not_fit(self):
        assert not fit_platt([0.5], [True]).fitted

    def test_no_variation_in_score_will_not_fit(self):
        assert not fit_platt([0.4, 0.4, 0.4, 0.4], [True, False, True, False]).fitted

    def test_an_unfitted_model_passes_the_raw_score_through(self):
        model = CalibrationModel(fitted=False)
        assert model.apply(0.37) == 0.37

    def test_an_unfitted_model_says_so(self):
        assert "uncalibrated" in CalibrationModel(fitted=False).describe()

    def test_no_samples_gives_an_unfitted_model(self):
        assert not fit_calibration([]).fitted


class TestModuleLayout:
    """`validator` applies a calibration model; `calibration` needs `validator`
    to fit one. Holding both in one module made that a cycle, worked around by
    importing inside a function - against the repo rule that imports live at
    module scope. `calibration_model` exists to break it properly.
    """

    def test_the_package_has_no_import_cycle(self):
        graph = import_graph()
        colour = dict.fromkeys(graph, "white")
        stack = []

        def visit(module):
            colour[module] = "grey"
            stack.append(module)
            for dep in sorted(graph.get(module, ())):
                if dep not in graph:
                    continue
                if colour[dep] == "grey":
                    return stack[stack.index(dep):] + [dep]
                if colour[dep] == "white":
                    found = visit(dep)
                    if found:
                        return found
            colour[module] = "black"
            stack.pop()
            return None

        for module in sorted(graph):
            if colour[module] == "white":
                cycle = visit(module)
                assert cycle is None, "import cycle: " + " -> ".join(cycle)

    def test_the_applied_model_depends_on_nothing_in_the_package(self):
        """It is imported by both sides of the old cycle, so it has to stay a leaf."""
        assert import_graph()["calibration_model"] == set()

    def test_the_fitting_side_imports_the_validator_at_module_scope(self):
        """The whole point of the split. A lazy import here would mean the cycle
        was hidden rather than removed."""
        assert "validator" in import_graph()["calibration"]

    def test_no_module_imports_inside_a_function(self):
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for inner in ast.walk(node):
                    if isinstance(inner, (ast.Import, ast.ImportFrom)):
                        raise AssertionError(
                            f"{path.name}:{inner.lineno} imports inside {node.name}()"
                        )


class TestMismatchedInput:
    """Scores and labels are zipped during the fit, so a length mismatch is
    silent corruption rather than an error. It is a caller bug, so it raises
    instead of returning an unfitted model, which would hide it.
    """

    def test_more_scores_than_labels_raises(self):
        """Previously an IndexError from deep inside cross-validation."""
        with pytest.raises(ValueError, match="raw scores and"):
            fit_platt([0.1 * i for i in range(10)], [True, False] * 3)

    def test_more_labels_than_scores_raises(self):
        """Previously returned a model claiming 7 positives out of 6 samples."""
        with pytest.raises(ValueError, match="raw scores and"):
            fit_platt([0.1 * i for i in range(6)], [True] * 7 + [False] * 3)

    def test_the_error_names_both_lengths(self):
        """A bare 'length mismatch' leaves the caller guessing which is wrong."""
        with pytest.raises(ValueError, match=r"3 raw scores and 2 labels"):
            fit_platt([0.1, 0.5, 0.9], [True, False])

    def test_empty_and_empty_is_still_an_unfitted_model_not_an_error(self):
        """Equal lengths, just no data. That is a data condition, not a bug."""
        assert not fit_platt([], []).fitted

    def test_matching_lengths_are_unaffected(self):
        assert fit_platt(*separable_sample()).fitted


class TestSmallSampleSafety:
    def test_a_tiny_separable_sample_does_not_claim_near_certainty(self):
        """Target smoothing exists so four examples cannot buy 99% confidence."""
        model = fit_platt([0.1, 0.2, 0.8, 0.9], [False, False, True, True])
        assert model.apply(0.9) < 0.95

    def test_more_evidence_permits_more_confidence(self):
        small = fit_platt([0.1, 0.2, 0.8, 0.9], [False, False, True, True])
        large = fit_platt([0.1, 0.2, 0.8, 0.9] * 40, [False, False, True, True] * 40)
        assert large.apply(0.9) > small.apply(0.9)


class TestOnTheRealCorpus:
    @pytest.fixture(scope="class")
    def pool(self):
        return load_corpus(split="corpus")

    def test_leave_one_out_produces_both_outcomes(self, pool):
        """A single-class training set would teach the prior, not a mapping."""
        samples = build_calibration_samples(pool)
        labels = [label for _, label in samples]
        assert len(samples) > 20
        assert 0 < sum(labels) < len(labels)

    def test_the_corpus_fits_a_usable_model(self, pool):
        model = fit_calibration(pool)
        assert model.fitted
        assert model.samples > 20

    def test_the_fit_reduces_calibration_error_on_its_own_training_data(self, pool):
        samples = build_calibration_samples(pool)
        raw = [r for r, _ in samples]
        labels = [label for _, label in samples]
        model = fit_calibration(pool)

        before = expected_calibration_error(raw, labels)
        after = expected_calibration_error([model.apply(r) for r in raw], labels)
        assert after < before

    def test_the_model_is_serializable(self, pool):
        model = fit_calibration(pool)
        restored = CalibrationModel.model_validate_json(model.model_dump_json())
        assert restored.apply(0.4) == pytest.approx(model.apply(0.4))
