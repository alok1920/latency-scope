from latencyscope.core.stats import compute_stats, rate_quality


def test_empty_samples():
    st = compute_stats([])
    assert st.sent == 0
    assert st.received == 0
    assert st.avg_ms is None
    assert st.quality == "unreachable"
    assert st.score == 0


def test_all_lost():
    st = compute_stats([None, None, None])
    assert st.sent == 3
    assert st.received == 0
    assert st.lost == 3
    assert st.loss_pct == 100.0
    assert st.quality == "unreachable"


def test_basic_aggregation():
    st = compute_stats([10.0, 20.0, 30.0])
    assert st.sent == 3
    assert st.received == 3
    assert st.min_ms == 10.0
    assert st.max_ms == 30.0
    assert st.avg_ms == 20.0
    assert st.last_ms == 30.0
    # jitter = mean(|20-10|, |30-20|) = 10
    assert st.jitter_ms == 10.0


def test_partial_loss():
    st = compute_stats([10.0, None, 30.0, None])
    assert st.sent == 4
    assert st.received == 2
    assert st.lost == 2
    assert st.loss_pct == 50.0
    assert st.avg_ms == 20.0
    # jitter computed over received-in-order only: |30-10| = 20
    assert st.jitter_ms == 20.0


def test_single_sample_has_zero_jitter_and_stdev():
    st = compute_stats([42.0])
    assert st.jitter_ms == 0.0
    assert st.stdev_ms == 0.0
    assert st.avg_ms == 42.0


def test_quality_tiers():
    assert compute_stats([5.0] * 10).quality == "excellent"
    assert rate_quality(compute_stats([45.0] * 10))[0] == "good"
    assert rate_quality(compute_stats([90.0] * 10))[0] == "fair"
    assert rate_quality(compute_stats([300.0] * 10))[0] == "poor"


def test_loss_demotes_quality():
    # Low latency but 20% loss must not be "excellent".
    samples = [5.0, 5.0, 5.0, 5.0, None]
    st = compute_stats(samples)
    assert st.loss_pct == 20.0
    assert st.quality != "excellent"


def test_score_monotonic_with_latency():
    low = compute_stats([10.0] * 10).score
    high = compute_stats([200.0] * 10).score
    assert low > high
    assert 0 <= high <= 100 and 0 <= low <= 100
