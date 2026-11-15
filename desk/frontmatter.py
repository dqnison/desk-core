"""Frontmatter of a rule file: a small, strict YAML subset, stdlib only.

Supported: block mappings and sequences by indentation, sequences of mappings
(`- tool: Bash` with continuation keys under it), flow lists `[a, b]` and flow
maps `{remote: 'x'}` (nestable), quoted scalars ('…' with '' escape, "…" with
backslash escapes), block scalars `|` and `>` (blank lines inside are dropped),
`true`/`false`, `null`/`~`, integers, `#` comments.
Not supported (and reported as an error): anchors, aliases, tags, multi-document
streams. The old desk parser only knew flat `key: [list]`; rule
triggers and guards are nested data now, hence a real (if small) parser.
"""
import re
from pathlib import Path


class FrontmatterError(ValueError):
    def __init__(self, msg, line=None):
        super().__init__(f"line {line}: {msg}" if line else msg)
        self.line = line


_KEY_RE = re.compile(r"^([A-Za-z_][\w.-]*)\s*:(?:\s+(.*))?$")


def split(text):
    """(frontmatter text or None, body, number of the first body line)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text, 1
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:]), i + 2
    return None, text, 1


def parse(text):
    """Frontmatter of a markdown text as a dict; no frontmatter — {}.
    Raises FrontmatterError on syntax the subset does not cover."""
    fm, _, _ = split(text)
    if fm is None:
        return {}
    value = loads(fm, first_line=2)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FrontmatterError("frontmatter is not a mapping", 2)
    return value


def read(path):
    """(frontmatter, error text or None) of a file; unreadable file — ({}, error)."""
    try:
        return parse(Path(path).read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError) as e:
        return {}, str(e)
    except FrontmatterError as e:
        return {}, str(e)


def body(text):
    """Text without frontmatter, stripped."""
    return split(text)[1].strip()


# ------------------------------------------------------------------ prose
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def prose_lines(text, skip_frontmatter=False):
    """(line number, line) of a markdown text outside ``` fences (and the frontmatter)."""
    lines = (text or "").splitlines()
    start = 0
    if skip_frontmatter and lines[:1] == ["---"]:
        start = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), -1) + 1
    fence = False
    for n, line in enumerate(lines[start:], start + 1):
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence:
            yield n, line


def prose(line):
    """What a reader reads in a line: inline code and link targets dropped."""
    return re.sub(r"`[^`]*`", "`", re.sub(r"\]\([^)]*\)", "]", line))


def links(line):
    """Link targets of a line, inline code aside."""
    return LINK_RE.findall(re.sub(r"`[^`]*`", "", line))


# ------------------------------------------------------------------ block YAML
def _strip_comment(s):
    """Drop a trailing ` # comment` outside quotes."""
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == "\\" and quote == '"':
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def loads(text, first_line=1):
    lines = []
    for n, raw in enumerate(text.splitlines(), first_line):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise FrontmatterError("tab in indentation", n)
        content = _strip_comment(raw.strip())
        if content:
            lines.append([len(raw) - len(raw.lstrip()), content, n, raw.strip()])
    if not lines:
        return None
    value, i = _block(lines, 0, lines[0][0])
    if i < len(lines):
        raise FrontmatterError("unexpected indentation", lines[i][2])
    return value


def _is_item(content):
    return content == "-" or content.startswith("- ")


def _block(lines, i, indent):
    if _is_item(lines[i][1]):
        return _seq(lines, i, indent)
    return _map(lines, i, indent)


def _map(lines, i, indent):
    out = {}
    while i < len(lines) and lines[i][0] == indent and not _is_item(lines[i][1]):
        _, content, n, _ = lines[i]
        m = _KEY_RE.match(content)
        if not m:
            raise FrontmatterError(f"expected `key: value`, got {content!r}", n)
        key, rest = m.group(1), (m.group(2) or "").strip()
        if key in out:
            raise FrontmatterError(f"duplicate key {key!r}", n)
        i += 1
        if rest[:1] in "|>" and rest.rstrip("+-") in ("|", ">"):
            block = []
            while i < len(lines) and lines[i][0] > indent:
                block.append(lines[i][3])
                i += 1
            out[key] = ("\n" if rest[0] == "|" else " ").join(block)
        elif rest:
            out[key] = scalar(rest, n)
        elif i < len(lines) and lines[i][0] > indent:
            out[key], i = _block(lines, i, lines[i][0])
        elif i < len(lines) and lines[i][0] == indent and _is_item(lines[i][1]):
            out[key], i = _seq(lines, i, indent)   # `key:` then `- a` at the same column
        else:
            out[key] = None
    if i < len(lines) and lines[i][0] > indent:
        raise FrontmatterError("unexpected indentation", lines[i][2])
    return out, i


def _seq(lines, i, indent):
    out = []
    while i < len(lines) and lines[i][0] == indent and _is_item(lines[i][1]):
        _, content, n, raw = lines[i]
        rest = content[1:].lstrip()
        if not rest:
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                item, i = _block(lines, i, lines[i][0])
            else:
                item = None
            out.append(item)
            continue
        col = indent + (len(content) - len(rest))
        if _KEY_RE.match(rest) and rest[0] not in "[{'\"":
            lines[i] = [col, rest, n, raw[len(content) - len(rest):]]   # `- key: v` opens a mapping
            item, i = _map(lines, i, col)
        else:
            item = scalar(rest, n)
            i += 1
        out.append(item)
    return out, i


# ------------------------------------------------------------------- scalars
_INT_RE = re.compile(r"^[-+]?\d+$")


def scalar(s, line=None):
    s = s.strip()
    if s[:1] in "[{":
        value, pos = _flow(s, 0, line)
        if s[pos:].strip():
            raise FrontmatterError(f"trailing text after flow value: {s[pos:]!r}", line)
        return value
    if s[:1] in "'\"":
        value, pos = _quoted(s, 0, line)
        if s[pos:].strip():
            raise FrontmatterError(f"trailing text after quoted value: {s[pos:]!r}", line)
        return value
    if s[:1] in "|>&*!" or s.startswith("---"):
        raise FrontmatterError(f"unsupported YAML syntax {s[:1]!r}", line)
    return _plain(s)


def _plain(s):
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if _INT_RE.match(s):
        return int(s)
    return s


def _quoted(s, pos, line):
    q = s[pos]
    out, i = [], pos + 1
    while i < len(s):
        ch = s[i]
        if q == "'" and ch == "'":
            if s[i + 1:i + 2] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        if q == '"' and ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        if q == '"' and ch == '"':
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise FrontmatterError("unterminated quoted string", line)


def _skip(s, pos):
    while pos < len(s) and s[pos] in " \t":
        pos += 1
    return pos


def _flow(s, pos, line):
    """Flow value starting at s[pos]: list, map, quoted or plain token."""
    pos = _skip(s, pos)
    if pos >= len(s):
        raise FrontmatterError("unexpected end of flow value", line)
    ch = s[pos]
    if ch in "'\"":
        return _quoted(s, pos, line)
    if ch == "[":
        out, pos = [], _skip(s, pos + 1)
        if s[pos:pos + 1] == "]":
            return out, pos + 1
        while True:
            item, pos = _flow(s, pos, line)
            out.append(item)
            pos = _skip(s, pos)
            if s[pos:pos + 1] == ",":
                pos = _skip(s, pos + 1)
                if s[pos:pos + 1] == "]":        # trailing comma
                    return out, pos + 1
                continue
            if s[pos:pos + 1] == "]":
                return out, pos + 1
            raise FrontmatterError("expected `,` or `]` in flow list", line)
    if ch == "{":
        out, pos = {}, _skip(s, pos + 1)
        if s[pos:pos + 1] == "}":
            return out, pos + 1
        while True:
            m = re.compile(r"([A-Za-z_][\w.-]*)\s*:\s*").match(s, pos)
            if not m:
                raise FrontmatterError("expected `key:` in flow map", line)
            value, pos = _flow(s, m.end(), line)
            out[m.group(1)] = value
            pos = _skip(s, pos)
            if s[pos:pos + 1] == ",":
                pos = _skip(s, pos + 1)
                continue
            if s[pos:pos + 1] == "}":
                return out, pos + 1
            raise FrontmatterError("expected `,` or `}` in flow map", line)
    end = pos
    while end < len(s) and s[end] not in ",]}":
        end += 1
    return _plain(s[pos:end].strip()), end


def as_list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]
