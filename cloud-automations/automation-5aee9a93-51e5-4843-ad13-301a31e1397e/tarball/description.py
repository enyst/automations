"""Replace only Jev's section in a shared GitHub pull-request description."""

import re

START = "<!-- jev-fast-audit:start -->"
END = "<!-- jev-fast-audit:end -->"
HEADING = "## Jev-Fast-Audit"
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,2})(?:[ \t]+|$)(.*)$")
_MARKER = re.compile(r"<!--\s*jev-fast-audit\b")


def _lines(body):
    """Yield source offsets and structural lines, ignoring fenced code."""
    offset = 0
    fence = None
    for line in body.splitlines(keepends=True):
        text = line.rstrip("\r\n")
        match = _FENCE.match(text)
        if fence:
            if (match and match[1][0] == fence[0]
                    and len(match[1]) >= fence[1] and not match[2].strip()):
                fence = None
        elif match and not (match[1][0] == "`" and "`" in match[2]):
            fence = (match[1][0], len(match[1]))
        else:
            yield offset, offset + len(line), text
        offset += len(line)
    if fence:
        raise ValueError("Unclosed Markdown fence; cannot safely place audit section")


def _audit_spans(body):
    lines = list(_lines(body))
    marked = []
    start = None
    for low, high, text in lines:
        if not _MARKER.search(text):
            continue
        marker = text.strip()
        if marker == START:
            if start is not None:
                raise ValueError("Nested Jev-Fast-Audit start markers")
            start = low
        elif marker == END:
            if start is None:
                raise ValueError("Jev-Fast-Audit end marker has no start")
            marked.append((start, high))
            start = None
        else:
            raise ValueError("Malformed Jev-Fast-Audit marker")
    if start is not None:
        raise ValueError("Jev-Fast-Audit start marker has no end")

    # Legacy sections have no markers. Their next level-one/two heading is
    # the boundary; headings in fenced examples or owned blocks do not count.
    headings = []
    for low, _, text in lines:
        if any(begin <= low < end for begin, end in marked):
            continue
        match = _HEADING.match(text)
        if match:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2]).strip()
            headings.append((low, len(match[1]) == 2 and title == "Jev-Fast-Audit"))
    spans = marked[:]
    for index, (low, owned) in enumerate(headings):
        if owned:
            high = headings[index + 1][0] if index + 1 < len(headings) else len(body)
            spans.append((low, high))
    merged = []
    for low, high in sorted(spans):
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def strip_audit(body: str) -> str:
    """Remove owned sections; malformed markers raise before returning text.

    Everything outside the removed ranges is preserved byte for byte, including
    separator whitespace previously inserted before an appended section.
    """
    pieces = []
    cursor = 0
    for low, high in _audit_spans(body):
        pieces.append(body[cursor:low])
        cursor = high
    pieces.append(body[cursor:])
    return "".join(pieces)


def upsert_audit(body: str, summary: str) -> str:
    """Put one owned section at its first existing position, or append it.

    ``summary`` is Markdown content without this section's heading or markers.
    Existing foreign content is untouched. Generated lines use the body's first
    newline style (LF for an empty or single-line body).
    """
    if _audit_spans(summary) or _MARKER.search(summary):
        raise ValueError("Summary must omit Jev-Fast-Audit headings and markers")
    spans = _audit_spans(body)
    first_newline = re.search(r"\r?\n", body)
    newline = first_newline[0] if first_newline else "\n"
    content = summary.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    content = content.replace("\n", newline)
    block = START + newline + HEADING + newline + newline
    if content:
        block += content + newline
    block += END + newline
    if not spans:
        separator = ""
        if body and not body.endswith(newline + newline):
            separator = newline if body.endswith(newline) else newline + newline
        return body + separator + block
    pieces = []
    cursor = 0
    for index, (low, high) in enumerate(spans):
        pieces.append(body[cursor:low])
        if index == 0:
            pieces.append(block)
        cursor = high
    pieces.append(body[cursor:])
    return "".join(pieces)
