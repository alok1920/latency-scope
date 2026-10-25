"""Command-line interface for LatencyScope."""

from __future__ import annotations

import argparse
import json
import sys

from . import __app_name__, __version__
from .core.analyzer import (
    Protocol,
    TargetResult,
    parse_target,
    ping_target,
    rank_targets,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="latencyscope",
        description=f"{__app_name__} — multi-protocol latency / jitter / loss analyzer.",
    )
    p.add_argument("targets", nargs="*",
                   help="host[:port][/proto] (proto = tcp|udp|icmp). "
                        "e.g. example.com:443  1.2.3.4:27015/udp  host/icmp")
    p.add_argument("-c", "--count", type=int, default=10, help="probes per target (default 10)")
    p.add_argument("-i", "--interval", type=float, default=0.5,
                   help="seconds between probes (default 0.5)")
    p.add_argument("-t", "--timeout", type=float, default=1.0,
                   help="per-probe timeout in seconds (default 1.0)")
    p.add_argument("-p", "--protocol", choices=[x.value for x in Protocol], default="tcp",
                   help="default protocol when a target omits /proto (default tcp)")
    p.add_argument("--port", type=int, default=443, help="default TCP/UDP port (default 443)")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--gui", action="store_true", help="launch the graphical interface instead")
    p.add_argument("-V", "--version", action="version", version=f"{__app_name__} {__version__}")
    return p


def _print_live(target, seq, rtt):
    status = f"{rtt:.1f} ms" if rtt is not None else "timeout"
    print(f"  [{target.label}] seq={seq} {status}", file=sys.stderr)


def _result_row(r: TargetResult) -> str:
    st = r.stats
    if r.error:
        return f"{r.target.label:<34} ERROR: {r.error}"
    ip = r.resolved_ip or "?"
    if st.received == 0:
        return f"{r.target.label:<34} ({ip:<15}) unreachable  loss=100%"
    return (f"{r.target.label:<34} ({ip:<15}) "
            f"avg={st.avg_ms:6.1f}ms min={st.min_ms:6.1f} max={st.max_ms:6.1f} "
            f"jitter={st.jitter_ms:5.1f} loss={st.loss_pct:4.0f}%  "
            f"{st.quality.upper()} ({st.score})")


def run(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.gui or not args.targets:
        # No targets given -> fall back to the GUI (also what the windowed exe does).
        from .gui import launch
        launch()
        return 0

    try:
        targets = [parse_target(s, default_protocol=Protocol(args.protocol),
                                 default_port=args.port) for s in args.targets]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    results: list[TargetResult] = []
    for tgt in targets:
        if not args.json:
            print(f"Probing {tgt.label} ...", file=sys.stderr)
        results.append(ping_target(
            tgt, count=args.count, interval=args.interval, timeout=args.timeout,
            on_sample=None if args.json else _print_live,
        ))

    ranked = rank_targets(results)

    if args.json:
        print(json.dumps({
            "app": __app_name__,
            "version": __version__,
            "results": [{
                "target": r.target.label,
                "resolved_ip": r.resolved_ip,
                "error": r.error,
                "stats": r.stats.as_dict(),
            } for r in ranked],
        }, indent=2))
        return 0

    print("\n=== Results (best first) ===")
    for i, r in enumerate(ranked, 1):
        print(f"{i:>2}. {_result_row(r)}")
    best = next((r for r in ranked if r.stats.received > 0 and not r.error), None)
    if best:
        print(f"\nBest endpoint: {best.target.label} "
              f"({best.stats.avg_ms:.1f} ms, {best.stats.quality})")
    else:
        print("\nNo endpoint responded.")
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
