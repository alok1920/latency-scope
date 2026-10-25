import pytest

from latencyscope.core.analyzer import (
    Protocol,
    Target,
    TargetResult,
    best_target,
    parse_target,
    rank_targets,
)
from latencyscope.core.stats import compute_stats


def test_parse_plain_host_defaults_to_tcp_443():
    t = parse_target("example.com")
    assert t.host == "example.com"
    assert t.port == 443
    assert t.protocol is Protocol.TCP
    assert t.ipv6 is False


def test_parse_host_port_proto():
    t = parse_target("1.2.3.4:27015/udp")
    assert t.host == "1.2.3.4"
    assert t.port == 27015
    assert t.protocol is Protocol.UDP


def test_parse_icmp_has_no_port():
    t = parse_target("host.example/icmp")
    assert t.protocol is Protocol.ICMP
    assert t.port is None


def test_parse_bracketed_ipv6_with_port():
    t = parse_target("[2001:db8::1]:443")
    assert t.host == "2001:db8::1"
    assert t.port == 443
    assert t.ipv6 is True


def test_parse_bare_ipv6_no_port():
    t = parse_target("2001:db8::1/tcp")
    assert t.host == "2001:db8::1"
    assert t.ipv6 is True
    assert t.port == 443  # default applied


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        parse_target("   ")


def _result(label_host, samples, error=None):
    tgt = Target(host=label_host, port=443, protocol=Protocol.TCP)
    return TargetResult(target=tgt, resolved_ip="1.1.1.1",
                        stats=compute_stats(samples), error=error)


def test_ranking_prefers_lower_latency():
    slow = _result("slow", [100.0] * 5)
    fast = _result("fast", [10.0] * 5)
    ranked = rank_targets([slow, fast])
    assert ranked[0].target.host == "fast"


def test_ranking_puts_unreachable_last():
    ok = _result("ok", [50.0] * 5)
    dead = _result("dead", [None] * 5)
    ranked = rank_targets([dead, ok])
    assert ranked[0].target.host == "ok"
    assert ranked[-1].target.host == "dead"


def test_ranking_errors_last():
    ok = _result("ok", [50.0] * 5)
    err = _result("err", [], error="dns failure")
    ranked = rank_targets([err, ok])
    assert ranked[0].target.host == "ok"


def test_best_target_none_when_all_dead():
    assert best_target([_result("a", [None]), _result("b", [None])]) is None


def test_best_target_picks_lowest_loss_then_latency():
    lossy = _result("lossy", [10.0, None, 10.0, None])  # 50% loss, low latency
    clean = _result("clean", [40.0] * 4)                # 0% loss, higher latency
    best = best_target([lossy, clean])
    assert best.target.host == "clean"
