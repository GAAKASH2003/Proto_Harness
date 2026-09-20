from __future__ import annotations

FENCE = "---"


def split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        raise ValueError("file must start with a '---' YAML frontmatter block")
    for index in range(1, len(lines)):
        if lines[index].strip() == FENCE:
            frontmatter = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            return frontmatter, body
    raise ValueError("No closing '---' fence found")
