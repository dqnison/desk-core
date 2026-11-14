"""Session header: one block the model shows first in its first answer.

The core fills the title (context → repository) and the «подача» row (bytes in the
session and behind triggers); extensions add rows and signals through `ev.header`
in their SessionStart handler (`ev.header.row(...)`, `ev.header.signal(...)`).
Rendered twice — a monospace frame (CLI and as a fallback) and ready HTML for a
widget tool (desktop) — with one instruction which to use. Off with `header: false`
in desk.yml.
"""
import datetime
import html
import textwrap
from dataclasses import dataclass, field

from . import home as homelib, messages as msg

FRAME_W = 76          # a chat line of the desktop app
FRAME_LABEL = 10
WIDGET_TITLE = "desk_session_header"


@dataclass
class Line:
    label: str
    value: str
    emoji: str = "▫"      # frame (emoji with a stable width: no U+FE0F variants)
    icon: str = "point"   # widget: Tabler outline icon name


@dataclass
class Header:
    title: str
    rows: list = field(default_factory=list)
    signals: list = field(default_factory=list)

    def row(self, label, value, emoji="▫", icon="point"):
        self.rows.append(Line(label, value, emoji, icon))

    def signal(self, label, value, emoji="▫", icon="point"):
        self.signals.append(Line(label, value, emoji, icon))


def plural(n, one, few, many):
    """«за 1 триггером», «за 2 триггерами», «за 14 триггерами»."""
    n = abs(n) % 100
    if 5 <= n <= 20:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def _kb(n):
    return f"{n / 1024:.0f} КБ"


def weights(rules, session):
    """(bytes in the session, triggers that have rules here, bytes behind them):
    import + session tier; action rules in scope, grouped by their `when:` names."""
    from .hook import session_rules
    here = sum(r.path.stat().st_size for r in rules if r.tier == "import")
    here += sum(r.path.stat().st_size for r in session_rules(rules, session))
    keys, waiting = set(), 0
    for r in rules:
        if r.tier == "action" and homelib.in_scope(r, session):
            keys |= {w if isinstance(w, str) else r.rel for w in r.when}
            waiting += r.path.stat().st_size
    return here, len(keys), waiting


def new(rules, session):
    """The core part: title and the «подача» row."""
    title = (session.context_name or "—") + (f" → {session.repo}" if session.repo else "")
    h = Header(title)
    here, n, waiting = weights(rules, session)
    behind = (f"за {n} {plural(n, 'триггером', 'триггерами', 'триггерами')} ещё {_kb(waiting)}"
              if n else "за триггерами ничего")
    h.row("подача", f"{_kb(here)} в сессии · {behind}", "📦", "package")
    return h


def frame(h, now=None):
    """Monospace frame in ```text: identical in desktop and CLI; the model copies
    it as is. A title too wide moves into the first row. No right wall: emoji are
    wider than a letter, an even right edge is impossible with them."""
    now = now or datetime.datetime.now()
    inner = FRAME_W - 6 - FRAME_LABEL
    head = f"╭─ 💻 desk · {h.title} · {now:%d.%m %H:%M} "
    rows = list(h.rows)
    if len(head) > FRAME_W - 2:
        head = f"╭─ 💻 desk · {now:%d.%m %H:%M} "
        rows = [Line("контекст", h.title, "🧭", "compass"), *rows]

    def lines(items):
        out = []
        for ln in items:
            for i, part in enumerate(textwrap.wrap(ln.value, inner) or [""]):
                lead = f"{ln.emoji} {ln.label:<{FRAME_LABEL}}" if i == 0 else " " * (FRAME_LABEL + 3)
                out.append(f"│ {lead}{part}")
        return out

    body = lines(rows)
    if h.signals:
        sep = "├─ сигналы "
        body += [sep + "─" * (FRAME_W - len(sep)), *lines(h.signals)]
    return ["```text", head + "─" * (FRAME_W - len(head)), *body, "╰" + "─" * (FRAME_W - 1), "```"]


def widget(h, now=None):
    """The same header as ready HTML for a widget tool: theme variables only,
    Tabler icons; a signal is a short chip in the warning pair."""
    now = now or datetime.datetime.now()

    def chips(value, bg, fg):
        return "".join(f'<span style="background:{bg};color:{fg};border-radius:var(--radius);'
                       f'padding:2px 8px">{html.escape(part)}</span>' for part in value.split(" · "))

    def line(ln, bg, fg):
        return ('<div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap">'
                f'<span style="color:var(--text-secondary);min-width:88px">'
                f'<i class="ti ti-{ln.icon}" style="font-size:15px;vertical-align:-2px;'
                f'margin-right:4px" aria-hidden="true"></i>{html.escape(ln.label)}</span>'
                f'<span style="display:flex;gap:6px;flex-wrap:wrap">{chips(ln.value, bg, fg)}</span></div>')

    body = [line(r, "var(--surface-1)", "var(--text-primary)") for r in h.rows]
    body += [line(s, "var(--bg-warning)", "var(--text-warning)") for s in h.signals]
    return (f'<h2 class="sr-only">Шапка сессии desk: {html.escape(h.title)}</h2>'
            '<div style="background:var(--surface-2);border:0.5px solid var(--border);'
            'border-radius:12px;padding:10px 14px;font-size:13px;display:flex;'
            'flex-direction:column;gap:6px">'
            '<div style="display:flex;justify-content:space-between;gap:12px;'
            'align-items:baseline;flex-wrap:wrap"><span style="font-weight:500;font-size:14px">'
            '<i class="ti ti-compass" style="font-size:16px;vertical-align:-2px;'
            f'margin-right:6px" aria-hidden="true"></i>{html.escape(h.title)}</span>'
            f'<span style="color:var(--text-muted)">desk · {now:%d.%m %H:%M}</span></div>'
            + "".join(body) + "</div>")


def render(h, now=None):
    """The instruction plus both forms."""
    now = now or datetime.datetime.now()
    return "\n".join([msg.header_instruction(WIDGET_TITLE), *frame(h, now), "",
                      "```html", widget(h, now), "```"])


def enabled(home):
    return homelib.config(home).get("header", True) is not False
