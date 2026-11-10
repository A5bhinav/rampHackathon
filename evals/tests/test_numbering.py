"""Spec 0048, D1: the lab's spec and question numbers collide with nothing else in the repo.

A spec number belongs to one file in `specs/`. A question is defined where a list item in a spec opens
with it ("- **Q150 (resolved):**", "- Q62: ..."); each lab spec defines exactly the questions listed
here, and no other spec defines any of them. A reference to a lab question from elsewhere (FINDINGS, or
another spec citing a decision) is not a definition and is fine. Collisions with other branches are
checked by hand when each PR opens (D1)."""
from __future__ import annotations

import re
from pathlib import Path

SPECS = Path(__file__).resolve().parents[2] / "specs"

# Each lab spec on main, with the questions it defines. A later landing PR adds its spec here.
LAB = {
    "0017-attack-lab-voice-slice.md": {150, 140, 141},
    "0083-generative-selection-pressure.md": {142, 143, 62, 63, 64, 144},
    "0046-guided-search-on-selection.md": {145, 146, 147, 148, 149},
    "0048-honest-lab-on-main.md": {101, 102, 103, 104, 105},
}

_DEFINES = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(?:\*\*)?Q(\d+)\b", re.MULTILINE)


def defined(path: Path) -> set[int]:
    """The question numbers a spec defines."""
    return {int(q) for q in _DEFINES.findall(path.read_text(encoding="utf-8"))}


def collisions(specs: Path, lab: dict[str, set[int]]) -> list[str]:
    problems = []
    for name, questions in lab.items():
        number = name.split("-", 1)[0]
        shared = sorted(p.name for p in specs.glob(f"{number}-*.md") if p.name != name)
        if not (specs / name).is_file():
            problems.append(f"{name} is not in specs/")
            continue
        if shared:
            problems.append(f"spec {number} is also {', '.join(shared)}")
        if (found := defined(specs / name)) != questions:
            problems.append(f"{name} defines {sorted(found)}, not {sorted(questions)}")
    ours = set().union(*lab.values())
    for path in sorted(specs.glob("*.md")):
        if path.name not in lab and (both := sorted(defined(path) & ours)):
            problems.append(f"{path.name} also defines {', '.join(f'Q{q}' for q in both)}")
    return problems


def test_lab_numbers_do_not_collide_with_main():
    assert collisions(SPECS, LAB) == []


def test_a_collision_is_reported(tmp_path):
    # The check can fail: a second spec under a lab number, and another spec defining a lab question.
    (tmp_path / "0017-attack-lab-voice-slice.md").write_text("## Open questions\n\n- **Q150 (resolved):** a\n")
    (tmp_path / "0017-another.md").write_text("text\n")
    (tmp_path / "0090-other.md").write_text("- **Q150 (Sami):** b\n- Carried: **Q140** is not a definition\n")
    found = collisions(tmp_path, {"0017-attack-lab-voice-slice.md": {150}})
    assert found == ["spec 0017 is also 0017-another.md", "0090-other.md also defines Q150"]
