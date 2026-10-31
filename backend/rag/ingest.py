"""Split the rule documents in rag/docs/ into citable rules.

Each "## <ID> <title>" section is one rule, e.g. "AL-2 Agent error: purchase breaks the
mandate". The ID is what a verdict cites.
"""
import re
from functools import lru_cache
from pathlib import Path

DOCS_DIR = Path(__file__).with_name("docs")
_HEADING = re.compile(r"^## ([A-Z]{2}-\d+) (.+)$")


def parse(text: str, doc: str) -> list[dict[str, str]]:
    rules, current = [], None
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            current = {"id": m.group(1), "title": m.group(2).strip(), "doc": doc, "text": ""}
            rules.append(current)
        elif current is not None and line.strip():
            current["text"] = f"{current['text']} {line.strip()}".strip()
    return rules


@lru_cache
def load_rules() -> tuple[dict[str, str], ...]:
    rules = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        rules.extend(parse(path.read_text(encoding="utf-8"), path.stem))
    ids = [r["id"] for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate rule ids in {DOCS_DIR}")
    return tuple(rules)


def get_rule(rule_id: str) -> dict[str, str] | None:
    return next((r for r in load_rules() if r["id"] == rule_id), None)
