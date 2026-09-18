# -*- coding: utf-8 -*-
"""Extract body text in reading order from a text-layer PDF (also when the PDF
has no space glyphs, as in many AMS/JCLI layouts).

Usage:
  python -X utf8 extract_text.py SOURCE.pdf OUT_DIR [options]

Options:
  --mid-x PT        column boundary in PDF points (auto-detected when omitted)
  --span-pages LIST comma separated 1-based pages to treat as single column
                    (title/abstract pages that span both columns)
  --header-y PT     ignore content above this y (default 53)
  --footer-y PT     ignore content below this y (default: page height - 29)
  --space-ratio R   force the synthetic word-space threshold to R x the page's
                    median font size (default: derive it from the page's own
                    gap histogram, which also copes with letterspaced titles)

Outputs into OUT_DIR: readable_body.txt, source_text_by_page.txt,
source_lines.json (line coordinates, handy for figure/formula cropping).
"""
import argparse
import json
import pathlib
import re

import pdfplumber

SMALL_GAP = 0.5
WORD_GAP_MIN = 1.2
LINE_TOL = 2.2


def split_row(row, mid, col_gap=8.0, col_tol=12.0):
    """Split a row that holds glyphs of both columns.

    A full-width line (figure caption) crosses the boundary without a wide gap
    and stays whole; two columns that merely share a baseline are separated by
    a wide gap near the boundary.
    """
    prev = None
    for c in row:
        x0 = float(c["x0"])
        if prev is not None and (x0 - prev) >= col_gap and abs((x0 + prev) / 2 - mid) <= col_tol:
            left = [x for x in row if float(x["x0"]) < prev]
            right = [x for x in row if float(x["x0"]) >= x0]
            return [r for r in (left, right) if r]
        prev = float(c["x1"])
    return [row]


def rows_of(page, header_y, footer_y):
    chars = [c for c in page.chars if header_y < float(c["top"]) < footer_y]
    chars.sort(key=lambda c: (round(float(c["top"]), 1), float(c["x0"])))
    rows, cur, last = [], [], None
    for c in chars:
        top = float(c["top"])
        if cur and last is not None and abs(top - last) > LINE_TOL:
            rows.append(cur)
            cur = []
        cur.append(c)
        last = top
    if cur:
        rows.append(cur)
    return [sorted(r, key=lambda c: float(c["x0"])) for r in rows]


def row_text(row, threshold):
    parts, buf, prev = [], [], None
    for c in row:
        x0 = float(c["x0"])
        if threshold is not None and prev is not None and buf and (x0 - prev) >= threshold:
            parts.append("".join(buf))
            buf = []
        buf.append(c["text"])
        prev = float(c["x1"])
    if buf:
        parts.append("".join(buf))
    return " ".join(parts)


def word_gap_threshold(rows, override_ratio=None):
    """Return the gap size (pt) above which a synthetic space is inserted.

    ``None`` means the text already carries space glyphs, so no synthetic space
    is needed. Without an override the threshold comes from the page's own gap
    histogram: letter joins stay below 0.6 pt, so the smallest 2% of the larger
    gaps approximates the narrowest real word gap.
    """
    if override_ratio:
        sizes = sorted(float(c.get("size", 9.0)) for row in rows for c in row)
        em = sizes[len(sizes) // 2] if sizes else 9.0
        return max(0.9, em * override_ratio)
    gaps = []
    for row in rows:
        prev = None
        for c in row:
            if prev is not None:
                gap = float(c["x0"]) - prev
                if gap > 0.6:
                    gaps.append(gap)
            prev = float(c["x1"])
    if len(gaps) < 10:
        return None
    gaps.sort()
    return max(0.9, gaps[max(0, int(len(gaps) * 0.02) - 1)] * 0.8)


def detect_mid_x(page, header_y, footer_y):
    rows = rows_of(page, header_y, footer_y)
    if not rows:
        return float(page.width) / 2
    width = float(page.width)
    scored = []
    for x in range(int(width * 0.30), int(width * 0.70)):
        empty = sum(1 for r in rows if not any(abs(float(c["x0"]) - x) <= 3 for c in r))
        scored.append((empty / len(rows), x))
    # first sustained empty corridor; its left edge sits between the two columns
    run_start = None
    for frac, x in scored:
        if frac >= 0.85:
            if run_start is None:
                run_start = x
            if x - run_start >= 5:
                return float(run_start) + 1.0
        else:
            run_start = None
    return width / 2


def gap_profile(rows):
    letter = narrow = word = 0
    for row in rows:
        prev = None
        for c in row:
            if prev is not None:
                gap = float(c["x0"]) - prev
                if 0 <= gap <= SMALL_GAP:
                    letter += 1
                elif SMALL_GAP < gap < WORD_GAP_MIN:
                    narrow += 1
                else:
                    word += 1
            prev = float(c["x1"])
    return {
        "letter_join": letter,
        "narrow": narrow,
        "word_gap": word,
        # space-glyph-free PDFs show geometric word gaps and almost no
        # intermediate widths
        "space_less_likely": narrow <= max(5, 0.01 * word) and word > 100,
    }


def join_rows(rows):
    merged = ""
    for txt in rows:
        txt = txt.strip()
        if not txt:
            continue
        if not merged:
            merged = txt
        elif merged.endswith("-"):
            merged = merged[:-1] + txt
        else:
            merged = merged + " " + txt
    return re.sub(r"[ ]{2,}", " ", merged).strip()


def paragraphs(pairs, indent_step=6.0):
    items = [(x0, txt) for x0, txt in pairs if txt.strip()]
    if not items:
        return []
    base = min(x0 for x0, _t in items)
    groups, cur = [], []
    for x0, txt in items:
        if cur and x0 >= base + indent_step:
            groups.append(join_rows(cur))
            cur = []
        cur.append(txt)
    if cur:
        groups.append(join_rows(cur))
    return [g for g in groups if g]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("out_dir")
    ap.add_argument("--mid-x", type=float)
    ap.add_argument("--span-pages", default="")
    ap.add_argument("--header-y", type=float, default=53.0)
    ap.add_argument("--footer-y", type=float)
    ap.add_argument("--space-ratio", type=float)
    args = ap.parse_args()

    span = {int(p) for p in args.span_pages.split(",") if p.strip()}
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks, line_dump, raw_dump = [], [], []
    report = []
    with pdfplumber.open(args.source) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            footer = args.footer_y if args.footer_y else float(page.height) - 29.0
            rows = rows_of(page, args.header_y, footer)
            if not rows:
                chunks.append(f"\n========== PAGE {pno} ==========\n")
                continue
            profile = gap_profile(rows)
            threshold = word_gap_threshold(rows, args.space_ratio)
            mid = args.mid_x or detect_mid_x(page, args.header_y, footer)
            single = pno in span
            if not single:
                # columns can share a baseline in the source PDF; keep them apart
                split = []
                for row in rows:
                    split.extend(split_row(row, mid))
                rows = split
            left_pairs, right_pairs = [], []
            for row in rows:
                txt = row_text(row, threshold)
                x0 = round(float(row[0]["x0"]), 1)
                line_dump.append(
                    {"page": pno, "x0": x0, "x1": round(float(row[-1]["x1"]), 1),
                     "top": round(float(row[0]["top"]), 1), "text": txt}
                )
                if single or float(row[0]["x0"]) < mid:
                    left_pairs.append((x0, txt))
                else:
                    right_pairs.append((x0, txt))
            paras = paragraphs(left_pairs)
            if right_pairs:
                paras += paragraphs(right_pairs)
            chunks.append(f"\n========== PAGE {pno} ==========\n" + "\n\n".join(paras))
            raw_dump.append(f"\n########## PAGE {pno} ##########\n{page.extract_text() or ''}")
            report.append(
                {"page": pno, "rows": len(rows), "mid_x": round(mid, 1), "gaps": profile,
                 "word_gap_threshold_pt": None if threshold is None else round(threshold, 2),
                 "single_column": single}
            )
    (out_dir / "readable_body.txt").write_text("\n".join(chunks), encoding="utf-8")
    (out_dir / "source_text_by_page.txt").write_text("".join(raw_dump), encoding="utf-8")
    (out_dir / "source_lines.json").write_text(
        json.dumps(line_dump, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
