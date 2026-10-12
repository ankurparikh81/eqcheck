"""
Comprehensive automated tests for eqcheck semantic equivalence pipeline.
"""

from pathlib import Path
import unittest

from eqcheck.cli import run_equivalence_check
from eqcheck.callgraph import FunctionNotFoundError


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestSemanticEquivalence(unittest.TestCase):

    def test_formatting_and_comments(self):
        ref = FIXTURES_DIR / "formatting" / "ref.cpp"
        impl = FIXTURES_DIR / "formatting" / "impl.cpp"
        # Test default whole-translation-unit check (no top required)
        result = run_equivalence_check(ref, impl)
        self.assertTrue(result.is_equivalent, f"Expected EQUIVALENT, got: {result.verdict}")
        self.assertEqual(len(result.function_diffs), 1)
        self.assertTrue(result.function_diffs[0].is_match)

    def test_algebraic_canonicalization(self):
        ref = FIXTURES_DIR / "algebraic" / "ref.cpp"
        impl = FIXTURES_DIR / "algebraic" / "impl.cpp"
        result = run_equivalence_check(ref, impl)
        self.assertTrue(result.is_equivalent, f"Expected EQUIVALENT, got: {result.verdict}")
        self.assertTrue(result.function_diffs[0].is_match)

    def test_loops_equivalence(self):
        ref = FIXTURES_DIR / "loops" / "ref.cpp"
        impl = FIXTURES_DIR / "loops" / "impl.cpp"
        result = run_equivalence_check(ref, impl)
        self.assertTrue(result.is_equivalent, f"Expected EQUIVALENT, got: {result.verdict}")
        self.assertTrue(result.function_diffs[0].is_match)

    def test_whole_tu_unmatched_warnings(self):
        ref = FIXTURES_DIR / "scoping" / "ref.cpp"
        impl = FIXTURES_DIR / "scoping" / "impl.cpp"
        result = run_equivalence_check(ref, impl)  # Whole TU
        self.assertTrue(result.is_equivalent, f"Expected EQUIVALENT, got: {result.verdict}")
        self.assertTrue(len(result.warnings) > 0)
        self.assertTrue(any("dead_uncalled_fn" in w for w in result.warnings))
        self.assertTrue(any("different_uncalled_fn" in w for w in result.warnings))

    def test_optional_top_scoping_and_pruning(self):
        ref = FIXTURES_DIR / "scoping" / "ref.cpp"
        impl = FIXTURES_DIR / "scoping" / "impl.cpp"
        result = run_equivalence_check(ref, impl, top="compute_all", no_inline=True)
        self.assertTrue(result.is_equivalent, f"Expected EQUIVALENT, got: {result.verdict}")
        # Active matched functions: compute_all, helper_add, helper_mul
        self.assertEqual(len(result.function_diffs), 3)
        for fd in result.function_diffs:
            self.assertTrue(fd.is_match)
        # Unreached functions should be reported in warnings
        self.assertTrue(any("dead_uncalled_fn" in w for w in result.warnings))
        self.assertTrue(any("different_uncalled_fn" in w for w in result.warnings))

    def test_tier1_match_file(self):
        ref = FIXTURES_DIR / "match_file" / "ref.cpp"
        impl = FIXTURES_DIR / "match_file" / "impl.cpp"
        match_file = FIXTURES_DIR / "match_file" / "matches.json"
        result = run_equivalence_check(ref, impl, match_file=match_file)
        self.assertTrue(result.is_equivalent, f"Expected EQUIVALENT, got: {result.verdict}")
        # Find match for helper
        helper_match = [fd for fd in result.function_diffs if "compute_step_v1" in fd.ref_name]
        self.assertEqual(len(helper_match), 1)
        self.assertIn("Tier 1", helper_match[0].tier)
        self.assertTrue(helper_match[0].is_match)

    def test_tier3_heuristic_matching(self):
        ref = FIXTURES_DIR / "heuristic" / "ref.cpp"
        impl = FIXTURES_DIR / "heuristic" / "impl.cpp"
        # No match file provided!
        result = run_equivalence_check(ref, impl)
        self.assertTrue(result.is_equivalent, f"Expected EQUIVALENT, got: {result.verdict}")
        stage_match = [fd for fd in result.function_diffs if "stage1" in fd.ref_name]
        self.assertEqual(len(stage_match), 1)
        self.assertIn("Tier 3", stage_match[0].tier)
        self.assertTrue(stage_match[0].is_match)

    def test_unmatched_redundant_functions(self):
        ref = FIXTURES_DIR / "redundant" / "ref.cpp"
        impl = FIXTURES_DIR / "redundant" / "impl.cpp"
        result = run_equivalence_check(ref, impl)
        self.assertTrue(result.is_equivalent, f"Expected EQUIVALENT, got: {result.verdict}")
        self.assertTrue(any("uncalled_redundant_helper" in w for w in result.warnings))
        self.assertEqual(len(result.unmatched_affecting), 0)

    def test_semantic_diff_detection(self):
        ref = FIXTURES_DIR / "semantic_diff" / "ref.cpp"
        impl = FIXTURES_DIR / "semantic_diff" / "impl.cpp"
        result = run_equivalence_check(ref, impl)
        self.assertFalse(result.is_equivalent, "Expected DIFFERENT for buggy implementation")
        self.assertEqual(result.verdict, "DIFFERENT")
        self.assertFalse(result.function_diffs[0].is_match)
        self.assertTrue(len(result.function_diffs[0].block_mismatches) > 0)
        # Verify source code mapping
        self.assertTrue(len(result.function_diffs[0].source_mismatches) > 0)
        sm = result.function_diffs[0].source_mismatches[0]
        self.assertEqual(sm.ref_line, 2)
        self.assertEqual(sm.impl_line, 3)
        self.assertIn("/ 100", sm.ref_snippet)
        self.assertIn("/ 10", sm.impl_snippet)

    def test_missing_top_function(self):
        ref = FIXTURES_DIR / "formatting" / "ref.cpp"
        impl = FIXTURES_DIR / "formatting" / "impl.cpp"
        with self.assertRaises(FunctionNotFoundError):
            run_equivalence_check(ref, impl, top="non_existent_function")


if __name__ == "__main__":
    unittest.main()
