"""Unit tests for the TrueNorth enforcement model — the guarantees that matter:

  - the pipeline STOPS on a required check's build errors (FR-71: never overridable)
  - the pipeline STOPS on a blocking check's build errors, unless a declared,
    unexpired exception releases it (FR-13)
  - the pipeline CONTINUES on an advisory check's build errors, and the errors
    are surfaced as flags on the error budget (FR-59/60)

Run:  python3 -m unittest discover -s tests -p 'test_*.py' -v
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / ".github"))
import truenorth_report as tn


def cfg():
    return {
        "pii-scan":          {"enabled": True, "mode": "required", "tier": "fast", "plane": "build"},
        "private-key-check": {"enabled": True, "mode": "required", "tier": "fast", "plane": "build"},
        "lint":              {"enabled": True, "mode": "blocking", "tier": "fast", "plane": "build"},
        "build":             {"enabled": True, "mode": "blocking", "tier": "fast", "plane": "build"},
        "test":              {"enabled": True, "mode": "blocking", "tier": "fast", "plane": "test"},
        "format":            {"enabled": True, "mode": "advisory", "tier": "fast", "plane": "build"},
    }


def ok(check, dur=100):
    return {"check": check, "status": "pass", "duration_ms": dur,
            "exit_code": 0, "mode": cfg()[check]["mode"]}


def bad(check, rc=1, findings=None, dur=100):
    r = {"check": check, "status": "fail", "duration_ms": dur,
         "exit_code": rc, "mode": cfg()[check]["mode"]}
    if findings is not None:
        r["findings"] = findings
    return r


def all_results(**overrides):
    r = {k: ok(k) for k in cfg()}
    r.update(overrides)
    return r


class RequiredStops(unittest.TestCase):
    def test_required_build_error_stops_pipeline(self):
        rep = tn.classify(cfg(), all_results(
            **{"pii-scan": bad("pii-scan", rc=2, findings=["pii: email@corp.com"])}))
        self.assertEqual(rep["conclusion"], "blocked")
        self.assertEqual([b["type"] for b in rep["blocks"]], ["required_failure"])
        b = rep["blocks"][0]
        self.assertEqual(b["check"], "pii-scan")
        self.assertEqual(b["exit_code"], 2)  # the build errors ride the block entry
        self.assertEqual(b["findings"], ["pii: email@corp.com"])

    def test_required_stops_even_with_exception(self):
        exc = [{"check": "pii-scan", "until": "2999-01-01", "approver": "q", "reason": "nope"}]
        rep = tn.classify(cfg(), all_results(**{"pii-scan": bad("pii-scan")}), exceptions=exc)
        self.assertEqual(rep["conclusion"], "blocked")
        self.assertTrue(any(b["type"] == "required_failure" for b in rep["blocks"]))
        self.assertTrue(any(f["type"] == "ignored_exception" for f in rep["flags"]))

    def test_required_disabled_is_hard_stop(self):
        c = cfg()
        c["pii-scan"]["enabled"] = False
        rep = tn.classify(c, all_results())
        self.assertEqual(rep["conclusion"], "blocked")
        self.assertEqual(rep["blocks"][0]["type"], "disabled_required")
        self.assertEqual(rep["error_budget"]["disabled_required"], 1)

    def test_missing_required_result_blocks(self):
        results = {k: v for k, v in all_results().items() if k != "pii-scan"}
        rep = tn.classify(cfg(), results)
        self.assertEqual(rep["conclusion"], "blocked")
        self.assertEqual(rep["blocks"][0]["type"], "missing_required")

    def test_required_failure_fails_the_leg(self):
        self.assertEqual(tn.harness_exit_code("required", 2), 2)


class BlockingStops(unittest.TestCase):
    def test_blocking_build_error_stops_pipeline(self):
        rep = tn.classify(cfg(), all_results(
            **{"lint": bad("lint", rc=1, findings=["lint: E501 line too long"])}))
        self.assertEqual(rep["conclusion"], "blocked")
        b = [x for x in rep["blocks"] if x["check"] == "lint"][0]
        self.assertEqual(b["type"], "blocking_failure")
        self.assertEqual(b["exit_code"], 1)
        self.assertEqual(b["findings"], ["lint: E501 line too long"])

    def test_blocking_leg_exits_nonzero(self):
        self.assertEqual(tn.harness_exit_code("blocking", 1), 1)

    def test_valid_exception_releases_blocking_failure(self):
        exc = [{"check": "lint", "until": "2999-01-01", "approver": "snoby", "reason": "known debt"}]
        rep = tn.classify(cfg(), all_results(**{"lint": bad("lint")}), exceptions=exc)
        self.assertEqual(rep["blocks"], [])
        self.assertEqual(rep["conclusion"], "flagged")
        self.assertEqual(rep["error_budget"]["out_of_policy_exceptions"], 1)

    def test_expired_exception_does_not_release(self):
        exc = [{"check": "lint", "until": "2020-01-01", "approver": "snoby", "reason": "stale"}]
        rep = tn.classify(cfg(), all_results(**{"lint": bad("lint")}), exceptions=exc)
        self.assertEqual(rep["conclusion"], "blocked")


class AdvisoryContinues(unittest.TestCase):
    def test_advisory_build_error_flags_but_pipeline_continues(self):
        rep = tn.classify(cfg(), all_results(
            **{"format": bad("format", rc=3, findings=["fmt: 2 files need reformat"])}))
        self.assertEqual(rep["blocks"], [])  # pipeline does NOT stop
        self.assertEqual(rep["conclusion"], "flagged")
        f = [x for x in rep["flags"] if x["check"] == "format"][0]
        self.assertEqual(f["type"], "advisory_finding")
        self.assertEqual(f["exit_code"], 3)  # build errors surfaced on the flag
        self.assertEqual(f["findings"], ["fmt: 2 files need reformat"])
        self.assertEqual(rep["error_budget"]["advisory_findings"], 1)

    def test_advisory_leg_exits_green_despite_errors(self):
        # the harness decision that keeps the run green when an advisory
        # check finds build errors (FR-59/60)
        self.assertEqual(tn.harness_exit_code("advisory", 3), 0)

    def test_advisory_disabled_is_silent(self):
        c = cfg()
        c["format"]["enabled"] = False
        rep = tn.classify(c, all_results())
        self.assertEqual(rep["conclusion"], "clean")
        self.assertEqual(rep["error_budget"]["total"], 0)


class PipelineTruth(unittest.TestCase):
    def test_all_pass_is_clean(self):
        rep = tn.classify(cfg(), all_results())
        self.assertEqual(rep["conclusion"], "clean")
        self.assertEqual(rep["blocks"], [])
        self.assertEqual(rep["flags"], [])
        self.assertEqual(len(rep["clean"]), 6)

    def test_no_results_blocks(self):
        rep = tn.classify(cfg(), {})
        self.assertEqual(rep["conclusion"], "blocked")
        self.assertEqual(rep["blocks"][0]["type"], "no_results")

    def test_budget_total_sums(self):
        c = cfg()
        c["pii-scan"]["enabled"] = False  # disabled_required: 1
        results = {k: v for k, v in all_results(**{"format": bad("format")}).items()
                   if k != "pii-scan"}
        rep = tn.classify(c, results)
        self.assertEqual(rep["error_budget"]["total"], 2)  # 1 advisory + 1 disabled_required

    def test_contract_shape(self):
        r = tn.build_result("lint", {"mode": "blocking", "tier": "fast"}, 1, 1234,
                            run_id=42, commit="abc", job="build/lint", findings=["x"])
        for k in ("check", "status", "mode", "tier", "score", "findings",
                  "duration_ms", "exit_code", "commit", "run_id", "job", "details"):
            self.assertIn(k, r)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
