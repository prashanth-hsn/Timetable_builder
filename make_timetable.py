#!/usr/bin/env python3
"""
make_timetable.py - turn a plain-text weekly schedule into a printable,
black-and-white, grid-style timetable PDF (A4 landscape).

Usage:
    python make_timetable.py schedule.txt                 # -> schedule.pdf
    python make_timetable.py schedule.txt -o timetable.pdf
    python make_timetable.py schedule.txt --letter        # US Letter paper

Requires:  pip install reportlab

---------------------------------------------------------------------------
INPUT FORMAT  (see schedule.txt for a full example)
---------------------------------------------------------------------------
Blank lines and lines starting with '#' are ignored.

Settings (optional):
    title: Leo's Weekly Timetable
    name:  Leo                      (shown under the title)
    range: 07:30-19:00              (visible time range; default = automatic)
    days:  Mon-Sun                  (columns to show; default Mon-Sun)
    squeeze: 08:00-15:30            (draw this long stretch, e.g. school, at
                                     reduced height so busy hours get more room)
    note:  Minecraft on Sunday if 7 of 8 tasks are done   (repeatable)

Events, one per line, fields separated by '|':
    DAYS | START-END | LABEL | STYLE | ICON

    DAYS   Mon | Mon,Wed | Mon-Fri | Weekdays | Weekend | Daily
    STYLE  (optional, default 'fixed')
             fixed  - light grey block  (school, sports, classes)
             task   - white block with a bold border (homework, practice)
             reward - white block with a thick border (game time, treat)
             plain  - white block with a thin border
    ICON   (optional) football | swimming | tv | school | piano | homework | gaming.
           Added automatically when the label contains football/soccer, swim...,
           TV/television/screen, school, piano, homework, or minecraft/game/gaming.

Example:
    Mon-Fri | 08:00-15:30 | School
    Mon,Wed | 16:45-18:15 | Football
    Mon-Thu | 16:00-16:30 | Homework | task
    Sun     | 15:00-16:00 | Minecraft | reward
"""

import argparse
import re
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4, LETTER, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]
STYLES = {"fixed", "task", "reward", "plain"}


class ScheduleError(Exception):
    pass


def day_index(token):
    t = token.strip().lower()
    for i, (short, full) in enumerate(zip(DAY_NAMES, DAY_FULL)):
        if t in (short.lower(), full.lower(), short.lower() + "."):
            return i
    raise ValueError(f"unknown day '{token}'")


def parse_days(spec):
    """'Mon', 'Mon,Wed', 'Mon-Fri', 'Weekdays', 'Weekend', 'Daily' -> [0..6]"""
    result = []
    for part in re.split(r"[,&/]|\band\b", spec):
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        if low in ("daily", "everyday", "every day", "all"):
            result += range(7)
        elif low in ("weekdays", "weekday"):
            result += range(5)
        elif low in ("weekend", "weekends"):
            result += [5, 6]
        elif re.search(r"[-\u2013]", part):
            pieces = re.split(r"\s*[-\u2013]\s*", part)
            if len(pieces) != 2:
                raise ValueError(f"bad day range '{part}'")
            a, b = pieces
            i, j = day_index(a), day_index(b)
            result += range(i, j + 1) if i <= j else \
                list(range(i, 7)) + list(range(0, j + 1))
        else:
            result.append(day_index(part))
    seen, ordered = set(), []
    for d in result:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


def parse_time(text):
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", text)
    if not m:
        raise ValueError(f"bad time '{text}' (use HH:MM)")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 24 and 0 <= mi < 60):
        raise ValueError(f"time out of range '{text}'")
    return h * 60 + mi


def parse_time_range(text):
    parts = re.split(r"\s*[-\u2013]\s*", text.strip())
    if len(parts) != 2:
        raise ValueError(f"bad time range '{text}' (use HH:MM-HH:MM)")
    start, end = parse_time(parts[0]), parse_time(parts[1])
    if end <= start:
        raise ValueError(f"end must be after start in '{text}'")
    return start, end


def fmt_time(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_schedule(path):
    cfg = {"title": "Weekly Timetable", "name": "", "range": None,
           "days": list(range(7)), "squeeze": None, "notes": []}
    events = []
    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if "|" in line:
                fields = [f.strip() for f in line.split("|")]
                if len(fields) < 3:
                    raise ValueError("expected: DAYS | START-END | LABEL [| STYLE]")
                days = parse_days(fields[0])
                start, end = parse_time_range(fields[1])
                label = fields[2]
                style = (fields[3].lower() if len(fields) > 3 and fields[3] else "fixed")
                if style not in STYLES:
                    raise ValueError(f"unknown style '{style}' (use {', '.join(sorted(STYLES))})")
                icon = detect_icon(label, fields[4] if len(fields) > 4 and fields[4] else None)
                if icon and icon not in ICON_DRAWERS:
                    raise ValueError(f"unknown icon '{icon}' (use {', '.join(ICON_DRAWERS)})")
                for d in days:
                    events.append({"day": d, "start": start, "end": end,
                                   "label": label, "style": style, "icon": icon})
            else:
                m = re.match(r"(\w+)\s*:\s*(.*)$", line)
                if not m:
                    raise ValueError("not a setting or an event")
                key, val = m.group(1).lower(), m.group(2).strip()
                if key in ("title", "name"):
                    cfg[key] = val
                elif key == "range":
                    cfg["range"] = parse_time_range(val)
                elif key == "squeeze":
                    cfg["squeeze"] = parse_time_range(val)
                elif key == "days":
                    cfg["days"] = sorted(parse_days(val))
                elif key == "note":
                    cfg["notes"].append(val)
                else:
                    raise ValueError(f"unknown setting '{key}'")
        except ValueError as e:
            raise ScheduleError(f"line {lineno}: {e}\n    > {raw}") from None
    if not events:
        raise ScheduleError("no events found in the schedule file")
    return cfg, events


# --------------------------------------------------------------------------
# Layout helpers
# --------------------------------------------------------------------------
def assign_lanes(day_events):
    """Place overlapping events side by side. Adds 'lane' and 'lanes' keys."""
    day_events.sort(key=lambda e: (e["start"], e["end"]))
    cluster, cluster_end = [], -1

    def finish(cl):
        if not cl:
            return
        lane_ends = []
        for ev in cl:
            for i, end in enumerate(lane_ends):
                if end <= ev["start"]:
                    ev["lane"], lane_ends[i] = i, ev["end"]
                    break
            else:
                ev["lane"] = len(lane_ends)
                lane_ends.append(ev["end"])
        for ev in cl:
            ev["lanes"] = len(lane_ends)

    for ev in day_events:
        if cluster and ev["start"] >= cluster_end:
            finish(cluster)
            cluster, cluster_end = [], -1
        cluster.append(ev)
        cluster_end = max(cluster_end, ev["end"])
    finish(cluster)


def wrap(text, font, size, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if stringWidth(trial, font, size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------
BLACK, WHITE, GREY = 0.0, 1.0, 0.88


# --------------------------------------------------------------------------
# Icons (drawn as vector shapes, so no special fonts are needed)
# --------------------------------------------------------------------------
ICON_KEYWORDS = {
    "football": ("football", "soccer"),
    "swimming": ("swim",),
    "tv": ("tv", "television", "screen"),
    "school": ("school",),
    "piano": ("piano",),
    "homework": ("homework",),
    "gaming": ("minecraft", "game", "gaming", "videogame"),
}


def detect_icon(label, explicit=None):
    if explicit:
        return explicit.lower()
    words = re.findall(r"[a-z]+", label.lower())
    for icon, keys in ICON_KEYWORDS.items():
        if any(w.startswith(k) for w in words for k in keys):
            return icon
    return None


def icon_football(c, x, y, s):
    """Ball: circle, centre pentagon, short spokes. (x, y) = bottom-left."""
    import math
    cx, cy, r = x + s / 2, y + s / 2, s / 2
    c.circle(cx, cy, r, stroke=1, fill=0)
    pr = r * 0.38
    pts = [(cx + pr * math.cos(math.radians(90 + 72 * i)),
            cy + pr * math.sin(math.radians(90 + 72 * i))) for i in range(5)]
    path = c.beginPath()
    path.moveTo(*pts[0])
    for px, py in pts[1:]:
        path.lineTo(px, py)
    path.close()
    c.drawPath(path, stroke=1, fill=1)
    for i, (px, py) in enumerate(pts):
        a = math.radians(90 + 72 * i)
        c.line(px, py, cx + r * 0.95 * math.cos(a), cy + r * 0.95 * math.sin(a))


def icon_swimming(c, x, y, s):
    """Swimmer's head and arm above two waves."""
    c.circle(x + s * 0.68, y + s * 0.72, s * 0.13, stroke=1, fill=1)
    p = c.beginPath()                       # arm reaching forward
    p.moveTo(x + s * 0.15, y + s * 0.52)
    p.curveTo(x + s * 0.3, y + s * 0.85, x + s * 0.5, y + s * 0.85,
              x + s * 0.6, y + s * 0.62)
    c.drawPath(p, stroke=1, fill=0)
    for yy in (0.34, 0.12):                 # waves
        p = c.beginPath()
        p.moveTo(x, y + s * yy)
        for i in range(4):
            x0 = x + s * i / 4
            p.curveTo(x0 + s * 0.06, y + s * (yy + 0.14), x0 + s * 0.19,
                      y + s * (yy + 0.14), x0 + s * 0.25, y + s * yy)
        c.drawPath(p, stroke=1, fill=0)


def icon_tv(c, x, y, s):
    """Television with antenna and screen."""
    c.roundRect(x, y + s * 0.1, s, s * 0.62, s * 0.08, stroke=1, fill=0)
    c.rect(x + s * 0.1, y + s * 0.2, s * 0.62, s * 0.42, stroke=1, fill=0)
    c.circle(x + s * 0.86, y + s * 0.5, s * 0.04, stroke=1, fill=1)
    c.line(x + s * 0.5, y + s * 0.72, x + s * 0.25, y + s * 0.95)   # antenna
    c.line(x + s * 0.5, y + s * 0.72, x + s * 0.75, y + s * 0.95)
    c.line(x + s * 0.2, y + s * 0.1, x + s * 0.15, y)               # legs
    c.line(x + s * 0.8, y + s * 0.1, x + s * 0.85, y)


def icon_school(c, x, y, s):
    """Schoolhouse with roof, flag, door and two windows."""
    c.rect(x + s * 0.08, y, s * 0.84, s * 0.55, stroke=1, fill=0)       # body
    p = c.beginPath()                                                   # roof
    p.moveTo(x, y + s * 0.55)
    p.lineTo(x + s * 0.5, y + s * 0.85)
    p.lineTo(x + s, y + s * 0.55)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    c.line(x + s * 0.5, y + s * 0.85, x + s * 0.5, y + s)               # flag pole
    p = c.beginPath()
    p.moveTo(x + s * 0.5, y + s)
    p.lineTo(x + s * 0.72, y + s * 0.93)
    p.lineTo(x + s * 0.5, y + s * 0.86)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.rect(x + s * 0.42, y, s * 0.16, s * 0.3, stroke=0, fill=1)        # door
    c.rect(x + s * 0.16, y + s * 0.3, s * 0.15, s * 0.14, stroke=0, fill=1)
    c.rect(x + s * 0.69, y + s * 0.3, s * 0.15, s * 0.14, stroke=0, fill=1)


def icon_piano(c, x, y, s):
    """Keyboard: white keys with black keys on top."""
    top, h = y + s * 0.85, s * 0.7
    base = top - h
    c.rect(x, base, s, h, stroke=1, fill=0)
    for i in range(1, 5):                                               # key gaps
        c.line(x + s * i / 5, base, x + s * i / 5, top)
    for i in (1, 2, 4):                                                 # black keys
        c.rect(x + s * i / 5 - s * 0.06, top - h * 0.6, s * 0.12, h * 0.6,
               stroke=0, fill=1)


def icon_homework(c, x, y, s):
    """Notebook page with text lines and a pencil."""
    c.rect(x, y, s * 0.68, s, stroke=1, fill=0)
    for yy in (0.72, 0.5, 0.28):
        c.line(x + s * 0.1, y + s * yy, x + s * 0.58, y + s * yy)
    p = c.beginPath()                                                   # pencil
    p.moveTo(x + s * 0.55, y + s * 0.1)
    p.lineTo(x + s * 0.86, y + s * 0.62)
    p.lineTo(x + s * 0.98, y + s * 0.55)
    p.lineTo(x + s * 0.67, y + s * 0.03)
    p.close()
    c.setFillGray(1.0)
    c.drawPath(p, stroke=1, fill=1)
    c.line(x + s * 0.55, y + s * 0.1, x + s * 0.5, y)                   # tip
    c.line(x + s * 0.5, y, x + s * 0.67, y + s * 0.03)


def icon_gaming(c, x, y, s):
    """Game controller: body, d-pad on the left, two buttons on the right."""
    c.roundRect(x, y + s * 0.15, s, s * 0.6, s * 0.28, stroke=1, fill=0)
    # d-pad (plus sign)
    cx, cy = x + s * 0.27, y + s * 0.45
    c.setLineWidth(max(1.0, s * 0.11))
    c.line(cx - s * 0.12, cy, cx + s * 0.12, cy)
    c.line(cx, cy - s * 0.12, cx, cy + s * 0.12)
    # action buttons
    c.circle(x + s * 0.68, y + s * 0.38, s * 0.07, stroke=0, fill=1)
    c.circle(x + s * 0.82, y + s * 0.52, s * 0.07, stroke=0, fill=1)
    # little shoulder bumps
    c.setLineWidth(0.9)
    c.line(x + s * 0.18, y + s * 0.75, x + s * 0.18, y + s * 0.84)
    c.line(x + s * 0.82, y + s * 0.75, x + s * 0.82, y + s * 0.84)


ICON_DRAWERS = {"football": icon_football, "swimming": icon_swimming, "tv": icon_tv,
                "school": icon_school, "piano": icon_piano, "homework": icon_homework,
                "gaming": icon_gaming}


def draw_event(c, ev, x, y_top, w, h):
    style = ev["style"]
    pad = 3

    # background + border
    if style == "fixed":
        c.setFillGray(GREY)
    else:
        c.setFillGray(WHITE)
    c.setStrokeGray(BLACK)
    c.setLineWidth({"task": 1.3, "reward": 2.4}.get(style, 0.8))
    c.rect(x, y_top - h, w, h, stroke=1, fill=1)

    text_gray = BLACK
    c.setFillGray(text_gray)

    icon = ev.get("icon")
    icon_s = 24 if h >= 50 else 16 if h >= 28 else 12 if h >= 17 else 10
    if icon not in ICON_DRAWERS or h < icon_s + 3 or w < icon_s * 3:
        icon = None
    avail_w = w - 2 * pad - (icon_s + 4 if icon else 0)
    time_str = f"{fmt_time(ev['start'])}-{fmt_time(ev['end'])}"
    bold, norm = "Helvetica-Bold", "Helvetica"

    # choose the largest font size at which the label fits the block; add the
    # time line only if there is still room for it at a readable size
    chosen = None
    for fs in (10, 9, 8, 7.5, 7, 6.5, 6):
        lead = fs + 1.5
        lines = wrap(ev["label"], bold, fs, avail_w)
        if any(stringWidth(l, bold, fs) > avail_w for l in lines):
            continue
        need_label = pad * 2 + len(lines) * lead - 1.5
        if need_label > h:
            continue
        tfs = min(fs - 1, 8)
        show_time = (tfs >= 6.5 and need_label + tfs + 1 <= h
                     and stringWidth(time_str, norm, tfs) <= w - 2 * pad)
        chosen = (fs, lines, show_time)
        break
    if chosen is None:
        fs = 6
        lines = wrap(ev["label"], bold, fs, avail_w)[: max(1, int((h - 2 * pad) // 7.5))]
        chosen = (fs, lines, False)

    fs, lines, show_time = chosen
    lead = fs + 1.5
    ty = y_top - pad - fs + 1
    c.setFont(bold, fs)
    for line in lines:
        c.drawString(x + pad, ty, line)
        ty -= lead
    if show_time:
        c.setFont(norm, min(fs - 1, 8))
        c.drawString(x + pad, ty, time_str)

    if icon:
        ix = x + w - pad - icon_s
        c.setStrokeGray(text_gray)
        c.setFillGray(text_gray)
        c.setLineWidth(0.9)
        ICON_DRAWERS[icon](c, ix, y_top - pad - icon_s, icon_s)


def draw_legend(c, x, y, styles_used):
    items = []
    if "fixed" in styles_used:
        items.append(("fixed", "Fixed activity"))
    if "task" in styles_used:
        items.append(("task", "Task"))
    if "reward" in styles_used:
        items.append(("reward", "Reward (thick border)"))
    c.setFont("Helvetica", 8)
    for style, text in items:
        sw = 12
        c.setStrokeGray(BLACK)
        c.setLineWidth({"task": 1.2, "reward": 2.4}.get(style, 0.8))
        c.setFillGray({"fixed": GREY, "task": WHITE, "reward": WHITE}[style])
        c.rect(x, y - 2, sw * 1.5, sw * 0.8, stroke=1, fill=1)
        c.setFillGray(BLACK)
        c.drawString(x + sw * 1.5 + 5, y, text)
        x += sw * 1.5 + 5 + stringWidth(text, "Helvetica", 8) + 18
    return x


def build_pdf(cfg, events, out_path, pagesize):
    W, H = landscape(pagesize)
    margin = 28
    days = cfg["days"]
    ncols = len(days)

    # visible time range
    if cfg["range"]:
        t0, t1 = cfg["range"]
    else:
        shown = [e for e in events if e["day"] in days]
        t0 = min(e["start"] for e in shown) // 60 * 60
        t1 = -(-max(e["end"] for e in shown) // 60) * 60
    if t1 <= t0:
        raise ScheduleError("visible time range is empty")

    # layout
    time_w = 38
    head_h = 22
    notes = cfg["notes"]
    footer_h = 26 + 11 * len(notes)
    top = H - margin - 44
    bottom = margin + footer_h
    body_h = top - head_h - bottom
    col_w = (W - 2 * margin - time_w) / ncols
    grid_x = margin + time_w

    # Vertical scale: every minute counts 1, except inside the 'squeeze' span
    # where minutes count less - unless some event (on any day) other than one
    # covering the whole span (e.g. school) occupies them, so short activities
    # such as a Saturday class stay readable.
    SQ_FACTOR = 0.25
    sq = cfg["squeeze"]
    protected = set()
    if sq:
        for e in events:
            if e["day"] in days and not (e["start"] <= sq[0] and e["end"] >= sq[1]):
                protected.update(range(max(e["start"], sq[0]), min(e["end"], sq[1])))
    cum = [0.0]
    for m in range(t0, t1):
        squeezed = sq and sq[0] <= m < sq[1] and m not in protected
        cum.append(cum[-1] + (SQ_FACTOR if squeezed else 1.0))
    total_w = cum[-1]

    def y_at(t):
        return top - head_h - cum[int(round(t)) - t0] / total_w * body_h

    c = canvas.Canvas(str(out_path), pagesize=(W, H))
    c.setTitle(cfg["title"])
    c.setAuthor("make_timetable.py")

    # title block
    c.setFillGray(BLACK)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(margin, H - margin - 16, cfg["title"])
    if cfg["name"]:
        c.setFont("Helvetica", 11)
        c.drawString(margin, H - margin - 32, cfg["name"])
    c.setFont("Helvetica", 11)
    label = "Week of:"
    lx = W - margin - 150
    c.drawString(lx, H - margin - 16, label)
    c.setLineWidth(0.8)
    c.line(lx + stringWidth(label, "Helvetica", 11) + 6, H - margin - 18,
           W - margin, H - margin - 18)

    # header row
    c.setStrokeGray(BLACK)
    c.setFillGray(BLACK)
    c.setFont("Helvetica-Bold", 12)
    for i, d in enumerate(days):
        cx = grid_x + i * col_w + col_w / 2
        c.drawCentredString(cx, top - head_h + 7, DAY_FULL[d])

    # horizontal time lines + labels (skip lines that would be too crowded)
    last_label_y = None
    last_line_y = None
    for t in range((t0 // 30) * 30, t1 + 1, 30):
        if t < t0:
            continue
        y = y_at(t)
        on_hour = t % 60 == 0
        if not on_hour and last_line_y is not None and last_line_y - y < 9:
            continue
        last_line_y = y
        c.setLineWidth(0.6 if on_hour else 0.3)
        c.setStrokeGray(0.35 if on_hour else 0.65)
        c.setDash([] if on_hour else [1.5, 2.5])
        c.line(margin + time_w - 4 if on_hour else grid_x, y, grid_x + ncols * col_w, y)
        c.setDash([])
        if on_hour and (last_label_y is None or last_label_y - y >= 11):
            c.setFillGray(BLACK)
            c.setFont("Helvetica", 9)
            c.drawRightString(margin + time_w - 7, y - 3, fmt_time(t))
            last_label_y = y

    # vertical day lines
    c.setStrokeGray(BLACK)
    c.setLineWidth(0.8)
    for i in range(ncols + 1):
        x = grid_x + i * col_w
        c.line(x, top - head_h, x, y_at(t1))
    # header underline and outer frame
    c.setLineWidth(1.5)
    c.line(grid_x, top - head_h, grid_x + ncols * col_w, top - head_h)
    c.line(grid_x, top, grid_x + ncols * col_w, top)
    c.line(grid_x, y_at(t1), grid_x + ncols * col_w, y_at(t1))

    # events
    styles_used = set()
    for ci, d in enumerate(days):
        day_events = [dict(e) for e in events if e["day"] == d]
        for e in day_events:
            e["start"], e["end"] = max(e["start"], t0), min(e["end"], t1)
        day_events = [e for e in day_events if e["end"] > e["start"]]
        assign_lanes(day_events)
        for e in day_events:
            styles_used.add(e["style"])
            lane_w = col_w / e["lanes"]
            inset = 1.5
            x = grid_x + ci * col_w + e["lane"] * lane_w + inset
            y_top = y_at(e["start"]) - 1
            y_bot = y_at(e["end"]) + 1
            draw_event(c, e, x, y_top, lane_w - 2 * inset, y_top - y_bot)

    # footer: legend + notes
    fy = margin + footer_h - 12
    draw_legend(c, margin, fy, styles_used)
    c.setFillGray(BLACK)
    c.setFont("Helvetica", 9)
    for i, note in enumerate(notes):
        c.drawString(margin, fy - 14 - i * 11, note)

    c.showPage()
    c.save()


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Schedule text file -> printable timetable PDF")
    ap.add_argument("schedule", help="path to the schedule text file")
    ap.add_argument("-o", "--output", help="output PDF (default: same name as input)")
    ap.add_argument("--letter", action="store_true", help="use US Letter instead of A4")
    args = ap.parse_args()

    out = Path(args.output) if args.output else Path(args.schedule).with_suffix(".pdf")
    try:
        cfg, events = parse_schedule(args.schedule)
        build_pdf(cfg, events, out, LETTER if args.letter else A4)
    except (ScheduleError, FileNotFoundError) as e:
        sys.exit(f"Error: {e}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
