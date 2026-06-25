#!/usr/bin/env python3
"""Program-adjudicable acceptance gate: run the 10 signal groups, emit JSON pass/fail.

Used by self-evolve as a regression gate. Exit 0 iff every signal passes.
"""
import contextlib
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_signals as T  # noqa: E402

SIGNALS = [
    ("S1_refresh_logic", T.S1Refresh),
    ("S2_idempotency", T.S2Idempotency),
    ("S3_multi_host_consistency", T.S3Consistency),
    ("S4_config_validation", T.S4Config),
    ("S5_dry_run_no_side_effects", T.S5DryRun),
    ("S6_secret_hygiene", T.S6Hygiene),
    ("S7_probe_classification", T.S7Classify),
    ("S8_antipattern_guard", T.S8AntiPattern),
    ("S9_atomic_write", T.S9Atomic),
    ("S10_cli_contract", T.S10CLI),
]


def main():
    out, all_ok = [], True
    for name, cls in SIGNALS:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(cls)
        # Suppress the tested CLI's own stdout so this gate emits clean, parseable JSON.
        with contextlib.redirect_stdout(io.StringIO()):
            res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        ok = res.wasSuccessful()
        all_ok = all_ok and ok
        detail = [str(t) for t, _ in (res.failures + res.errors)]
        out.append({"signal": name, "tests": res.testsRun,
                    "failures": len(res.failures), "errors": len(res.errors),
                    "pass": ok, "failed_tests": detail})
    print(json.dumps({"gate": "box-binder", "signals": out,
                      "passed": sum(s["pass"] for s in out), "total": len(out),
                      "all_pass": all_ok}, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
