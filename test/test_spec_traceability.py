"""Traceability audit: keep @pytest.mark.spec tags lined up with SPEC.md.

Parses SPEC.md for requirement IDs and the test suite for spec tags, then checks
they agree — no tag cites a missing requirement, and every requirement is either
tested or explicitly listed as not-yet-tested.
"""

import re
from pathlib import Path

_TEST_DIR = Path(__file__).parent
_SPEC = _TEST_DIR.parent / "SPEC.md"
_ID = r"[A-Z]{2,4}-[A-Z]+"
_SELF = Path(__file__).name

# Requirements with no unit test yet (UI/manual/constraint behaviour). Shrink
# this as tests are added. A newly uncovered requirement must either get a test
# or be added here as a conscious "not tested yet", or the coverage test fails.
# Requirements genuinely not unit-testable here: environment/platform facts,
# browser-side UI/interaction, best-effort audio, and network-dependent fetch.
# The behavioural logic (filters, selection modes, scoring) is tested above.
KNOWN_UNTESTED = {
    "ENV-LOCAL", "ENV-STORE", "ENV-ENGINE", "ENV-SOURCE", "ENV-SOLO",
    "IMP-ANLZ",
    "DASH-TABLE", "DASH-ENDST",
    "REV-BROWSE", "REV-SIDE", "REV-GLOSS",
    "TRN-NOHINT", "TRN-INPUT", "TRN-ALTS", "TRN-ARROW",
    "TRN-TALLY", "TRN-SOUND",
    "SCT-FETCH",
    "FLT-ONE",
    "NFR-LIVE", "NFR-CLOCK", "NFR-WIN",
}


def _spec_ids() -> set[str]:
    """Bold requirement IDs defined in SPEC.md (e.g. **TRN-UNIQ**)."""
    return set(re.findall(rf"\*\*({_ID})\*\*", _SPEC.read_text(encoding="utf-8")))


def _tagged_ids() -> set[str]:
    """IDs named by @pytest.mark.spec(...) across the suite (excluding this file)."""
    ids: set[str] = set()
    for path in _TEST_DIR.glob("test_*.py"):
        if path.name == _SELF:
            continue
        for args in re.findall(r"@pytest\.mark\.spec\(([^)]*)\)",
                               path.read_text(encoding="utf-8")):
            ids.update(re.findall(_ID, args))
    return ids


class TestSpecTraceability:
    """The tests-to-spec mapping is complete and free of drift."""

    def test_spec_defines_requirements(self):
        """Sanity: SPEC.md yields a plausible number of requirement IDs."""
        assert len(_spec_ids()) > 20

    def test_every_tag_names_a_real_requirement(self):
        """No spec tag may cite an ID that isn't defined in SPEC.md."""
        stale = _tagged_ids() - _spec_ids()
        assert not stale, f"spec tags cite unknown requirement IDs: {sorted(stale)}"

    def test_coverage_matches_the_declared_untested_set(self):
        """Each requirement is tested, or explicitly declared not-yet-tested."""
        uncovered = _spec_ids() - _tagged_ids()
        newly_uncovered = sorted(uncovered - KNOWN_UNTESTED)
        now_covered = sorted(KNOWN_UNTESTED - uncovered)
        assert uncovered == KNOWN_UNTESTED, (
            "requirement coverage drifted — add tests or update KNOWN_UNTESTED.\n"
            f"  newly uncovered (add a test or list here): {newly_uncovered}\n"
            f"  now covered (remove from KNOWN_UNTESTED): {now_covered}")
