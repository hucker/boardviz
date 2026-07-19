"""Traceability audit + matrix generation: keep tests, SPEC.md, and the matrix aligned.

Parses SPEC.md for requirement IDs and the test suite for spec tags, then checks
they agree — no tag cites a missing requirement, every requirement is either
tested or explicitly listed as not-yet-tested, and the committed ``TRACEABILITY.md``
matches what the parse produces.

Regenerate the matrix after changing tests or SPEC.md::

    uv run python test/test_spec_traceability.py
"""

import re
from pathlib import Path

_TEST_DIR = Path(__file__).parent
_SPEC = _TEST_DIR.parent / "SPEC.md"
_MATRIX = _TEST_DIR.parent / "TRACEABILITY.md"
_ID = r"[A-Z]{2,4}-[A-Z]+"
_SELF = Path(__file__).name

# Requirements genuinely not unit-testable here: environment/platform facts,
# browser-side UI/interaction, best-effort audio, and network-dependent fetch.
# The behavioral logic (filters, selection modes, scoring) is tested elsewhere.
KNOWN_UNTESTED = {
    "ENV-LOCAL",
    "ENV-STORE",
    "ENV-ENGINE",
    "ENV-SOURCE",
    "ENV-SOLO",
    "IMP-ANLZ",
    "DASH-TABLE",
    "DASH-ENDST",
    "REV-BROWSE",
    "REV-SIDE",
    "REV-GLOSS",
    "TRN-NOHINT",
    "TRN-INPUT",
    "TRN-ALTS",
    "TRN-ARROW",
    "TRN-TALLY",
    "TRN-SOUND",
    "SCT-FETCH",
    "FLT-ONE",
    "NFR-LIVE",
    "NFR-CLOCK",
    "NFR-WIN",
}

# Section prefix -> human name, for grouping the matrix.
_AREA_NAMES = {
    "ENV": "Environment & constraints",
    "IMP": "Import & analysis",
    "DASH": "Dashboard",
    "REV": "Review",
    "TRN": "Trainer",
    "SCT": "Scout",
    "FLT": "Filters",
    "NFR": "Non-functional",
}


def _spec_requirements() -> list[tuple[str, str]]:
    """Bold requirement IDs in SPEC.md order, each with its one-line text."""
    out: list[tuple[str, str]] = []
    for line in _SPEC.read_text(encoding="utf-8").splitlines():
        m = re.search(rf"\*\*({_ID})\*\*\s*(.*)", line)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def _spec_ids() -> set[str]:
    """The set of requirement IDs defined in SPEC.md."""
    return {i for i, _ in _spec_requirements()}


def _tests_by_id() -> dict[str, list[str]]:
    """Map each requirement ID to the test functions tagging it (excluding this file)."""
    by: dict[str, list[str]] = {}
    for path in sorted(_TEST_DIR.glob("test_*.py")):
        if path.name == _SELF:
            continue
        src = path.read_text(encoding="utf-8")
        # Associate each @pytest.mark.spec(...) with the test def that follows it.
        for args, name in re.findall(
            r"@pytest\.mark\.spec\(([^)]*)\).*?\n(?:\s*@.*\n)*\s*def (test_\w+)",
            src,
            re.S,
        ):
            for i in set(re.findall(_ID, args)):
                by.setdefault(i, []).append(name)
    return by


def _tagged_ids() -> set[str]:
    """IDs named by any @pytest.mark.spec(...) across the suite."""
    return set(_tests_by_id())


def render_matrix() -> str:
    """Render the SPEC-ID -> tests traceability matrix as GitHub-flavored markdown."""
    reqs = _spec_requirements()
    by = _tests_by_id()
    tested = [i for i, _ in reqs if by.get(i)]
    out = [
        "# Traceability matrix",
        "",
        "> Generated from `SPEC.md` + `test/` by "
        "`test/test_spec_traceability.py` — do not edit by hand. Regenerate with "
        "`uv run python test/test_spec_traceability.py`.",
        "",
        f"**{len(reqs)} requirements — {len(tested)} tested, "
        f"{len(reqs) - len(tested)} not unit-tested** "
        "(environment facts, browser-side UI, audio, and network fetch).",
    ]
    area = None
    for rid, text in reqs:
        a = rid.split("-")[0]
        if a != area:
            area = a
            out += [
                "",
                f"## {a} — {_AREA_NAMES.get(a, a)}",
                "",
                "| Requirement | Behavior | Tests |",
                "|---|---|---|",
            ]
        tests = by.get(rid)
        cell = (
            ", ".join(f"`{t}`" for t in sorted(set(tests)))
            if tests
            else "— _not unit-tested_"
        )
        out.append(f"| **{rid}** | {text.replace('|', chr(92) + '|')} | {cell} |")
    return "\n".join(out) + "\n"


class TestSpecTraceability:
    """The tests-to-spec mapping is complete, free of drift, and rendered to disk."""

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
            f"  now covered (remove from KNOWN_UNTESTED): {now_covered}"
        )

    def test_traceability_matrix_is_current(self):
        """TRACEABILITY.md matches the current parse (regenerate if this fails)."""
        current = _MATRIX.read_text(encoding="utf-8") if _MATRIX.exists() else ""
        assert current == render_matrix(), (
            "TRACEABILITY.md is stale — regenerate with "
            "`uv run python test/test_spec_traceability.py`"
        )


if __name__ == "__main__":  # regenerate the matrix on disk
    _MATRIX.write_text(render_matrix(), encoding="utf-8")
    print(f"wrote {_MATRIX.relative_to(_TEST_DIR.parent)}")
