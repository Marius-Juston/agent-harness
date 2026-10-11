"""The result contract every check returns. A check that examined nothing fails: an empty selection must never
read as a pass (a moved directory, a bad glob or a deselected test run would otherwise print "passed")."""

from dataclasses import dataclass, field


@dataclass
class Ran:
    name: str
    checked: int = 0
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    allow_empty: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems and (self.checked > 0 or self.allow_empty)

    def line(self) -> str:
        if self.checked == 0 and not self.allow_empty and not self.problems:
            return f"FAIL {self.name}: checked nothing (a check that ran nothing is not a pass)"
        state = "ok  " if self.ok else "FAIL"
        extra = f", {len(self.warnings)} warning(s)" if self.warnings else ""
        return f"{state} {self.name}: {self.checked} checked, {len(self.problems)} problem(s){extra}"
