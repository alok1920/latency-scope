import socket
import threading

import pytest

from latencyscope.core.engine import LatencyEngine


def _open_tcp_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]

    def accept_loop():
        srv.settimeout(3.0)
        try:
            while True:
                conn, _ = srv.accept()
                conn.close()
        except OSError:
            pass

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    return srv, port


def test_engine_requires_targets():
    with pytest.raises(ValueError):
        LatencyEngine([])


def test_engine_delivers_samples_then_stops():
    srv, port = _open_tcp_server()
    got: list = []
    done = threading.Event()

    def on_sample(rtt):
        got.append(rtt)
        if len(got) >= 2:
            done.set()

    eng = LatencyEngine([("127.0.0.1", port)], interval=0.1, timeout=1.0)
    eng.start(on_sample)
    assert done.wait(4.0), "engine did not deliver samples in time"
    eng.stop(wait=True)
    srv.close()

    assert len(got) >= 1
    assert any(isinstance(x, float) for x in got)
    assert not eng.running


def test_engine_records_target_array():
    eng = LatencyEngine([("127.0.0.1", 1), ("127.0.0.1", 2)])
    assert eng.targets == [("127.0.0.1", 1), ("127.0.0.1", 2)]
    assert eng.running is False


def test_engine_double_start_is_noop():
    srv, port = _open_tcp_server()
    eng = LatencyEngine([("127.0.0.1", port)], interval=0.2, timeout=1.0)
    eng.start(lambda _r: None)
    first = eng._thread
    eng.start(lambda _r: None)  # must not spawn a second thread
    assert eng._thread is first
    eng.stop(wait=True)
    srv.close()
