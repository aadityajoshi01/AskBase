"""Matplotlib PNG chart service for the gateway.

The client never sends rows. An analysis caches its exact restricted result set
(columns + rows as the permission policy produced them) under a one-ticket id;
`/api/chart?ticket=...` renders a PNG from that cache only, so chart input is by
construction permission-filtered. Nothing here executes code — the LLM's or the
client's — a spec of {type, x, y} against real result rows is all that is needed.

Rendering reuses matplotlib's Agg backend directly (same pattern Agent 1 proved
in make_charts.py). Supported: bar, line, scatter, histogram, box.
"""
from __future__ import annotations

import io
import json
import math
import re
import threading
import time
import uuid
from collections import OrderedDict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

CHART_TYPES = ("bar", "line", "scatter", "histogram", "box", "pie")
MAX_TICKETS = 32
TICKET_TTL_S = 30 * 60
MAX_CHART_ROWS = 5000
MAX_PIE_SLICES = 12

PALETTE = ["#1565c0", "#2e7d32", "#f9a825", "#c62828", "#6a1b9a", "#00838f"]

_cache: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()


class ChartError(ValueError):
    """A rejected request. `status` is the HTTP code the route should return."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def cache_result(columns: list, rows: list, chart: dict | None) -> str:
    """Register one analysis result set and return its rendering ticket."""
    ticket = uuid.uuid4().hex
    with _lock:
        _cache[ticket] = {"columns": list(columns), "rows": [list(r) for r in rows],
                          "chart": chart, "created": time.monotonic()}
        _cache.move_to_end(ticket)
        cutoff = time.monotonic() - TICKET_TTL_S
        while len(_cache) > MAX_TICKETS or (
                _cache and next(iter(_cache.values()))["created"] < cutoff):
            _cache.popitem(last=False)
    return ticket


def get_cached(ticket: str) -> dict:
    entry = _cache.get(ticket)
    if entry is None:
        raise ChartError("unknown or expired chart ticket — rerun the question", 404)
    return entry


def _numeric(value):
    """Result-row value as float, or None. Bools and NaN/Inf are not plottable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    try:
        f = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _index(columns: list, name) -> int:
    key = str(name or "")
    lowered = {str(c).lower(): i for i, c in enumerate(columns)}
    if key in columns:
        return columns.index(key)
    return lowered.get(key.lower(), -1)


def validate_spec(payload: dict, columns: list, rows: list) -> tuple[str, int, int]:
    """(chart_type, x_index, y_index) or ChartError. Rows here are the cached
    restricted rows, already permission-filtered."""
    ctype = str(payload.get("type") or "")
    if ctype not in CHART_TYPES:
        raise ChartError(f"unsupported chart type {ctype!r} — allowed: {', '.join(CHART_TYPES)}")
    if not columns:
        raise ChartError("the cached result has no columns")
    if len(rows) > MAX_CHART_ROWS:
        raise ChartError(f"result has {len(rows)} rows, the chart limit is {MAX_CHART_ROWS}")
    x = _index(columns, payload.get("x"))
    y = _index(columns, payload.get("y"))
    if x < 0:
        raise ChartError(f"column {payload.get('x')!r} is not in the query result")
    if y < 0:
        raise ChartError(f"column {payload.get('y')!r} is not in the query result")
    return ctype, x, y


def _column_pairs(rows: list, x: int, y: int) -> tuple[list, list]:
    """(xs, ys) keeping x/y aligned; a None y drops that pair (nulls never plot
    as zero)."""
    xs, ys = [], []
    for row in rows:
        vy = _numeric(row[y]) if len(row) > y else None
        if vy is None:
            continue
        xs.append(row[x] if len(row) > x else None)
        ys.append(vy)
    return xs, ys


def _single_series(rows: list, idx: int) -> list:
    out = []
    for row in rows:
        v = _numeric(row[idx]) if len(row) > idx else None
        if v is not None:
            out.append(v)
    return out


def _style(ax, title, xlabel, ylabel, legend: bool):
    ax.set_title(title, fontsize=11)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.tick_params(labelsize=8)
    if legend:
        ax.legend(fontsize=8)


def render_png(ctype: str, columns: list, rows: list, x: int, y: int,
               title: str = "") -> bytes:
    if not rows:
        raise ChartError("the query returned zero rows — nothing to plot")
    xname, yname = str(columns[x]), str(columns[y])
    xs, ys = _column_pairs(rows, x, y)
    if not ys:
        raise ChartError(f"column {yname!r} has no numeric values to plot")

    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=130)
    fig.patch.set_facecolor("white")
    legend = False

    if ctype == "bar":
        labels = [str(v) for v in xs]
        ax.bar(range(len(ys)), ys, color=PALETTE[0])
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        _style(ax, title or f"{yname} by {xname}", xname, yname, False)
    elif ctype == "line":
        pairs = sorted(zip(xs, ys), key=lambda p: (p[0] is None, p[0]))
        px = [p[0] for p in pairs]
        try:
            pn = [float(v) for v in px]
            px = pn
            ax.plot(px, [p[1] for p in pairs], marker="o", ms=3,
                    color=PALETTE[0])
            if all(v.is_integer() for v in pn):
                ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        except (TypeError, ValueError):
            ax.plot(range(len(px)), [p[1] for p in pairs], marker="o", ms=3,
                    color=PALETTE[0])
            ax.set_xticks(range(len(px)))
            ax.set_xticklabels([str(v) for v in px], rotation=45, ha="right")
        _style(ax, title or f"{yname} over {xname}", xname, yname, False)
    elif ctype == "scatter":
        xv = [_numeric(v) for v in xs]
        mask = [v is not None for v in xv]
        if not any(mask):
            raise ChartError(f"column {xname!r} has no numeric values to plot")
        ax.scatter([v for v, m in zip(xv, mask) if m],
                   [y for y, m in zip(ys, mask) if m],
                   s=14, color=PALETTE[0], alpha=0.75)
        _style(ax, title or f"{yname} vs {xname}", xname, yname, False)
    elif ctype == "histogram":
        ax.hist(ys, bins=min(40, max(8, int(math.sqrt(len(ys))))),
                color=PALETTE[0], edgecolor="white")
        _style(ax, title or f"Distribution of {yname}", yname, "count", False)
    elif ctype == "pie":
        negative = [v for v in ys if v < 0]
        if negative:
            raise ChartError(f"a pie chart cannot show negative values ({len(negative)} "
                             f"{'row has' if len(negative) == 1 else 'rows have'} {yname} < 0)")
        pairs = [(str(label), value) for label, value in zip(xs, ys) if value > 0]
        if not pairs:
            raise ChartError(f"{yname} sums to zero — nothing to divide into slices")
        pairs.sort(key=lambda p: p[1], reverse=True)
        if len(pairs) > MAX_PIE_SLICES:
            rest = pairs[MAX_PIE_SLICES - 1:]
            pairs = pairs[:MAX_PIE_SLICES - 1] + [(f"Other ({len(rest)})", sum(v for _, v in rest))]
        ax.pie([v for _, v in pairs], labels=[label for label, _ in pairs],
               autopct="%1.1f%%", startangle=90, counterclock=False,
               colors=[PALETTE[i % len(PALETTE)] for i in range(len(pairs))],
               wedgeprops={"edgecolor": "white"}, textprops={"fontsize": 8})
        ax.axis("equal")
        ax.set_title(title or f"Share of {yname} by {xname}", fontsize=11)
    elif ctype == "box":
        groups: "dict[str, list]" = {}
        order: list = []
        for gx, gy in zip(xs, ys):
            key = str(gx)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(gy)
        if x == y or len(order) == 1 or len(order) == len(ys):
            # no grouping column (or every group has one value): one overall box
            ax.boxplot([ys], tick_labels=[yname])
        else:
            ax.boxplot([groups[k] for k in order[:12]],
                       tick_labels=[k for k in order[:12]])
        ax.set_xticklabels([t.get_text() for t in ax.get_xticklabels()],
                           rotation=45, ha="right")
        _style(ax, title or f"{yname} by {xname}", xname, yname, False)

    fig.tight_layout()
    try:
        import io
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
    finally:
        plt.close(fig)
    return buf.getvalue()


def handle_chart_request(payload: dict) -> tuple[int, str, bytes]:
    """Route-facing entry: returns (status, media_type, body).

    Input is a small JSON spec {ticket, type?, x?, y?} — rows always come from
    the server-side cache of the restricted result, never from the client.
    """
    ticket = str(payload.get("ticket") or "")
    if not ticket:
        raise ChartError("missing chart ticket — run the question first")
    entry = get_cached(ticket)
    spec = payload
    if not payload.get("type"):
        if not entry["chart"]:
            raise ChartError("no suggested chart for this result — supply type, x and y")
        spec = {"type": entry["chart"]["type"], "x": entry["chart"]["x"],
                "y": entry["chart"]["y"]}
    ctype, x, y = validate_spec(spec, entry["columns"], entry["rows"])
    png = render_png(ctype, entry["columns"], entry["rows"], x, y)
    return 200, "image/png", png


def error_json(exc: ChartError) -> str:
    return json.dumps({"status": "error", "message": str(exc)})


# Plain-language chart requests. Only explicit chart words count, so "sales trend"
# or "distribution of orders" stay data questions instead of becoming chart orders.
_KIND_WORDS = [
    ("pie", r"pie|donut|doughnut"),
    ("histogram", r"histogram"),
    ("box", r"box(?:\s*-?\s*and\s*-?\s*whisker)?|boxplot"),
    ("scatter", r"scatter"),
    ("line", r"line|time\s*series"),
    ("bar", r"bar|column"),
]
_NOUN = r"(?:chart|graph|plot|diagram|visuali[sz]ation)s?"
_KIND_ANY = "|".join(f"(?:{pattern})" for _, pattern in _KIND_WORDS)
_CHART_PHRASE = re.compile(
    rf"\b(?:(?:as|in|into|using|with|via|on)\s+)?(?:a|an|the)?\s*"
    rf"(?:(?:{_KIND_ANY})\s*-?\s*)?(?:{_NOUN}|boxplot|histogram)"
    rf"(?:\s+(?:of|for|showing|that\s+shows|with))?\b", re.I)
_VERB_ONLY = re.compile(r"^\s*(?:please\s+)?(?:plot|graph|chart|visuali[sz]e|draw)\b\s*", re.I)
_LEAD_VERB = re.compile(r"^\s*(?:please\s+)?(?:show|draw|make|create|give|generate|build|display)"
                        r"(?:\s+me)?\s*$", re.I)
_RAW_ROW_KINDS = ("histogram", "box", "scatter")


def detect_chart_request(question: str) -> tuple[str | None, str]:
    """(requested chart kind | "auto" | None, question with the chart wording removed).

    The cleaned question is what the SQL generator sees, so the model is asked for
    data, never for a picture.
    """
    text = str(question or "")
    match = _CHART_PHRASE.search(text)
    verb = _VERB_ONLY.match(text)
    if not match and not verb:
        return None, text
    phrase = match.group(0) if match else verb.group(0)
    kind = "auto"
    for name, pattern in _KIND_WORDS:
        if re.search(rf"\b(?:{pattern})", phrase, re.I):
            kind = name
            break
    if match:
        head, tail = text[:match.start()], text[match.end():]
        if _LEAD_VERB.match(head) or not head.strip():
            cleaned = f"show {tail.strip()}" if tail.strip() else head
        else:
            cleaned = f"{head.rstrip()} {tail.lstrip()}"
    else:
        cleaned = f"show {text[verb.end():].strip()}"
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.;:")
    if kind in _RAW_ROW_KINDS:
        # these charts show a spread, so the SQL must return records, not one total
        # per group: "sales by segment" -> "sales and segment for each individual record"
        cleaned = re.sub(r"^show\s+", "", cleaned, flags=re.I)
        split = re.match(r"(.+?)\s+(?:by|per|across|vs\.?|versus|against)\s+(.+)$", cleaned, re.I)
        cleaned = (f"show {split.group(1)} and {split.group(2)} for each individual record"
                   if split else f"show {cleaned} for each individual record")
    return kind, cleaned or text


def _is_numeric_column(rows: list, idx: int) -> bool:
    values = [row[idx] for row in rows if len(row) > idx and row[idx] is not None]
    return bool(values) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)


_ID_NAME = re.compile(r"(?:^|_)(?:id|pk|key|rowid|row_id)$|^id_|_id$", re.I)


def spec_for_request(kind: str, columns: list, rows: list) -> tuple[dict | None, str | None]:
    """Pick columns for an explicitly requested chart and prove it renders.

    Returns (spec, None) or (None|fallback spec, reason). The reason is shown to the
    user — a requested chart that cannot honestly be drawn is said so, not faked.
    """
    if not rows:
        return None, "No chart drawn: the query returned zero rows."
    numeric_all = [i for i in range(len(columns)) if _is_numeric_column(rows, i)]
    # an identifier is a label, never a measure: plotting row_id is meaningless
    numeric = [i for i in numeric_all if not _ID_NAME.search(str(columns[i]))]
    labels = ([i for i in range(len(columns)) if i not in numeric_all] +
              [i for i in numeric_all if i not in numeric])
    if not numeric:
        return None, "No chart drawn: the result has no numeric column to plot."
    if kind == "auto":
        if len(numeric) >= 2 and not labels and len(rows) > 12:
            kind = "scatter"
        elif not labels:
            kind = "histogram"
        else:
            kind = "bar"
    if kind == "scatter":
        if len(numeric) < 2:
            return None, "No scatter plot drawn: it needs two numeric columns and the result has one."
        x, y = numeric[0], numeric[1]
    elif kind in ("histogram",):
        x = y = numeric[0]
    elif labels:
        x, y = labels[0], numeric[0]
    elif len(numeric) >= 2:
        x, y = numeric[0], numeric[1]  # e.g. SELECT year, SUM(sales)
    else:
        x = y = numeric[0]
    spec = {"type": kind, "x": str(columns[x]), "y": str(columns[y])}
    try:
        validate_spec(spec, columns, rows)
        render_png(kind, columns, rows, x, y)
    except ChartError as exc:
        if kind == "pie":
            fallback = {"type": "bar", "x": spec["x"], "y": spec["y"]}
            return fallback, f"Pie chart not drawn: {exc}. Showing a bar chart of the same rows instead."
        return None, f"No {kind} chart drawn: {exc}."
    return spec, None
