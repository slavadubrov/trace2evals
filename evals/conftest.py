import pytest

from trace2evals.tracing import init_tracing


@pytest.fixture(scope="session")
def tracer(tmp_path_factory):
    """Regression re-runs get their own span file so they never pollute the
    mined production trace store the flywheel reads."""
    spans = tmp_path_factory.mktemp("regression-traces") / "spans.jsonl"
    return init_tracing(service_name="trace2evals-regression", spans_path=spans)
