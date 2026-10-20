"""Markdown raporları ajans kimliğiyle sunuma hazır biçimlere çevirir: PDF (16:9 sunum), Excel, PowerPoint, Word, HTML.

Ajanların yazdığı Markdown alt kümesi desteklenir: başlıklar, paragraflar, madde ve numaralı listeler,
tablolar, alıntılar, yatay çizgi; satır içinde **kalın**, `kod` ve [bağlantı](adres).
"""

from __future__ import annotations

import html
import os
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FONT_DIR = Path(__file__).parent / "fonts"
AGENCY_NAME = os.getenv("AGENCY_NAME", "NABIZ")
AGENCY_TAGLINE = os.getenv("AGENCY_TAGLINE", "Sosyal Medya Komuta Merkezi")
BRAND = "8A6514"          # koyu altın
BRAND_SOFT = "F6ECD2"
INK = "1F1D2B"
MUTED = "5F5C6D"
FORMATS = {
    "pdf": ("application/pdf", ".pdf"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "html": ("text/html; charset=utf-8", ".html"),
}


# ---------------------------------------------------------------- Markdown ayrıştırma


@dataclass
class Block:
    kind: str                      # h, p, ul, ol, table, quote, hr
    text: str = ""
    level: int = 0
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Doc:
    title: str
    subtitle: str
    blocks: list[Block]
    intro: list[str] = field(default_factory=list)   # ilk başlıktan önceki kısa metin: kapakta gösterilir


def parse(markdown: str, fallback_title: str) -> Doc:
    blocks: list[Block] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block("p", " ".join(para).strip()))
            para.clear()

    for raw in markdown.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        s = line.strip()
        if not s:
            flush()
            continue
        if m := re.match(r"^(#{1,6})\s+(.*)$", s):
            flush()
            blocks.append(Block("h", m.group(2).strip(), level=len(m.group(1))))
        elif re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush()
            blocks.append(Block("hr"))
        elif s.startswith("|") and s.endswith("|") and len(s) > 1:
            flush()
            if re.match(r"^\|?[\s:|-]+\|?$", s) and "-" in s:
                continue  # ayırıcı satır
            cells = [c.strip() for c in s.strip("|").split("|")]
            if blocks and blocks[-1].kind == "table":
                blocks[-1].rows.append(cells)
            else:
                blocks.append(Block("table", rows=[cells]))
        elif m := re.match(r"^\s*[-*+]\s+(.*)$", line):
            flush()
            if blocks and blocks[-1].kind == "ul":
                blocks[-1].items.append(m.group(1))
            else:
                blocks.append(Block("ul", items=[m.group(1)]))
        elif m := re.match(r"^\s*\d+[.)]\s+(.*)$", line):
            flush()
            if blocks and blocks[-1].kind == "ol":
                blocks[-1].items.append(m.group(1))
            else:
                blocks.append(Block("ol", items=[m.group(1)]))
        elif s.startswith(">"):
            flush()
            text = s.lstrip(">").strip()
            if blocks and blocks[-1].kind == "quote":
                blocks[-1].text += " " + text
            else:
                blocks.append(Block("quote", text))
        else:
            para.append(s)
    flush()

    title, subtitle = fallback_title, ""
    if blocks and blocks[0].kind == "h" and blocks[0].level == 1:
        title = blocks.pop(0).text
    if blocks and blocks[0].kind == "quote":
        subtitle = blocks.pop(0).text
    # Ajan çıktılarında başlık bazen iki kez yazılır: aynı H1 tekrar ederse at.
    if blocks and blocks[0].kind == "h" and plain(blocks[0].text) == plain(title):
        blocks.pop(0)
    intro: list[str] = []
    while blocks and blocks[0].kind in ("p", "quote") and sum(len(x) for x in intro) + len(blocks[0].text) < 500:
        intro.append(blocks.pop(0).text)
    return Doc(plain(title), plain(subtitle), blocks, intro)


def plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    return re.sub(r"\*\*(.+?)\*\*|__(.+?)__|`([^`]+)`|\*(.+?)\*", lambda m: next(g for g in m.groups() if g), text).strip()


def runs(text: str) -> list[tuple[str, bool]]:
    """Satır içi metni (parça, kalın mı) listesine böler."""
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    out: list[tuple[str, bool]] = []
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if part:
            out.append((re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", part), i % 2 == 1))
    return out


def sections(doc: Doc) -> list[tuple[str, list[Block]]]:
    """H2 başlıklarına göre bölümler (her bölüm bir slayt grubu)."""
    out: list[tuple[str, list[Block]]] = [("", [])]
    for b in doc.blocks:
        if b.kind == "h" and b.level <= 2:
            out.append((plain(b.text), []))
        else:
            out[-1][1].append(b)
    return [(t, bl) for t, bl in out if t or bl]


def _number(value: str) -> float | int | str:
    """'164.083', '%4,24', '13,17 TL' gibi Türkçe sayıları Excel için sayıya çevirir; olmazsa metin döner."""
    v = plain(value).replace(" ", " ").strip()
    m = re.fullmatch(r"(-?\d{1,3}(?:\.\d{3})+|-?\d+)(?:,(\d+))?", v)
    if not m:
        return v
    whole = m.group(1).replace(".", "")
    return float(f"{whole}.{m.group(2)}") if m.group(2) else int(whole)


# ---------------------------------------------------------------- PDF (16:9 sunum)


def _register_fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold")
    return "DejaVu", "DejaVu-Bold"


def _rl(text: str) -> str:
    return "".join(f"<b>{html.escape(t)}</b>" if bold else html.escape(t) for t, bold in runs(text))


def to_pdf(doc: Doc) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, Frame, ListFlowable, ListItem, NextPageTemplate,
                                    PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)

    font, bold = _register_fonts()
    W, H = 338.7 * mm, 190.5 * mm  # 16:9 (13,33 x 7,5 inç)
    brand, soft, ink, muted = (colors.HexColor("#" + c) for c in (BRAND, BRAND_SOFT, INK, MUTED))
    st = {
        "body": ParagraphStyle("body", fontName=font, fontSize=14, leading=19, textColor=ink, spaceAfter=7),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=24, leading=29, textColor=ink, spaceAfter=10),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=16, leading=21, textColor=brand, spaceBefore=6, spaceAfter=6),
        "quote": ParagraphStyle("quote", fontName=font, fontSize=12, leading=16, textColor=muted, leftIndent=10,
                                borderPadding=(4, 0, 4, 8), spaceAfter=8),
        "cell": ParagraphStyle("cell", fontName=font, fontSize=10, leading=13, textColor=ink),
        "head": ParagraphStyle("head", fontName=bold, fontSize=10, leading=13, textColor=colors.white),
        "ctitle": ParagraphStyle("ctitle", fontName=bold, fontSize=34, leading=41, textColor=ink),
        "csub": ParagraphStyle("csub", fontName=font, fontSize=15, leading=21, textColor=muted),
    }
    margin = 18 * mm
    stamp = datetime.now().strftime("%d.%m.%Y")

    def chrome(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(brand)
        canvas.rect(0, H - 6, W, 6, fill=1, stroke=0)
        canvas.setFont(bold, 10)
        canvas.drawString(margin, 9 * mm, AGENCY_NAME)
        canvas.setFont(font, 9)
        canvas.setFillColor(muted)
        canvas.drawString(margin + canvas.stringWidth(AGENCY_NAME, bold, 10) + 10, 9 * mm, doc.title[:110])
        canvas.drawRightString(W - margin, 9 * mm, f"{canvas.getPageNumber()}")
        canvas.restoreState()

    def cover(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(soft)
        canvas.rect(0, 0, W, H, fill=1, stroke=0)
        canvas.setFillColor(brand)
        canvas.rect(0, 0, 14 * mm, H, fill=1, stroke=0)
        canvas.setFont(bold, 22)
        canvas.drawString(margin + 10 * mm, H - 30 * mm, " ".join(AGENCY_NAME))
        canvas.setFont(font, 11)
        canvas.setFillColor(muted)
        canvas.drawString(margin + 10 * mm, 14 * mm, f"{AGENCY_NAME} · {AGENCY_TAGLINE} · {stamp}")
        canvas.restoreState()

    frame = Frame(margin, 16 * mm, W - 2 * margin, H - 32 * mm, id="body")
    cover_frame = Frame(margin + 10 * mm, 30 * mm, W - 2 * margin - 20 * mm, H - 75 * mm, id="cover")
    buf = io.BytesIO()
    tpl = BaseDocTemplate(buf, pagesize=(W, H), title=doc.title, author=AGENCY_NAME,
                          pageTemplates=[PageTemplate("cover", [cover_frame], onPage=cover),
                                         PageTemplate("slide", [frame], onPage=chrome)])

    def table(rows: list[list[str]]):
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        data = [[Paragraph(_rl(c), st["head"] if i == 0 else st["cell"]) for c in r] for i, r in enumerate(rows)]
        avail = W - 2 * margin
        lens = [max(len(plain(r[c])) for r in rows) + 4 for c in range(width)]
        total = sum(min(x, 60) for x in lens)
        t = Table(data, colWidths=[avail * min(x, 60) / total for x in lens], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), brand),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F5F0")]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E3DDD1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    story: list = [Paragraph(html.escape(doc.title), st["ctitle"]), Spacer(1, 8)]
    for line in ([doc.subtitle] if doc.subtitle else []) + doc.intro:
        story.append(Paragraph(_rl(line), st["csub"]))
    story += [NextPageTemplate("slide"), PageBreak()]
    for n, (title, blocks) in enumerate(sections(doc)):
        if n and story and not isinstance(story[-1], PageBreak):
            story.append(PageBreak())
        if title:
            story.append(Paragraph(html.escape(title), st["h2"]))
        for b in blocks:
            if b.kind == "h":
                story.append(Paragraph(_rl(b.text), st["h3"]))
            elif b.kind == "p":
                story.append(Paragraph(_rl(b.text), st["body"]))
            elif b.kind == "quote":
                story.append(Paragraph(_rl(b.text), st["quote"]))
            elif b.kind in ("ul", "ol"):
                story.append(ListFlowable([ListItem(Paragraph(_rl(i), st["body"])) for i in b.items],
                                          bulletType="bullet" if b.kind == "ul" else "1", start="•" if b.kind == "ul" else 1,
                                          bulletFontName=font, bulletColor=brand, leftIndent=14))
            elif b.kind == "table":
                story += [table(b.rows), Spacer(1, 10)]
            elif b.kind == "hr":
                story.append(Spacer(1, 10))
    if isinstance(story[-1], PageBreak):
        story.pop()
    tpl.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------- Excel


def to_xlsx(doc: Doc) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Rapor"
    ws.column_dimensions["A"].width = 120
    ws.sheet_view.showGridLines = False
    head_fill, soft_fill = PatternFill("solid", fgColor=BRAND), PatternFill("solid", fgColor=BRAND_SOFT)
    thin = Border(bottom=Side(style="thin", color="E3DDD1"))
    wrap = Alignment(wrap_text=True, vertical="top")
    ws.append([doc.title]); ws["A1"].font = Font(bold=True, size=18, color=INK)
    if doc.subtitle:
        ws.append([doc.subtitle]); ws.cell(ws.max_row, 1).font = Font(italic=True, color=MUTED)
    ws.append([])
    tables: list[tuple[str, list[list[str]]]] = []
    current = doc.title
    for b in doc.blocks:
        if b.kind == "h":
            current = plain(b.text)
            ws.append([current])
            c = ws.cell(ws.max_row, 1)
            c.font = Font(bold=True, size=14 if b.level <= 2 else 12, color=BRAND if b.level > 2 else INK)
            if b.level <= 2:
                c.fill = soft_fill
        elif b.kind in ("p", "quote"):
            ws.append([plain(b.text)]); ws.cell(ws.max_row, 1).alignment = wrap
        elif b.kind in ("ul", "ol"):
            for i, item in enumerate(b.items, 1):
                ws.append([f"{'•' if b.kind == 'ul' else f'{i}.'} {plain(item)}"]); ws.cell(ws.max_row, 1).alignment = wrap
        elif b.kind == "table":
            tables.append((current, b.rows))
            ws.append([f"→ Tablo {len(tables)}: ayrı sayfada ('T{len(tables)} …')"])
            ws.cell(ws.max_row, 1).font = Font(italic=True, color=MUTED)
    used: set[str] = {"Rapor"}
    for n, (name, rows) in enumerate(tables, 1):
        title = re.sub(r"[\[\]:*?/\\]", "", f"T{n} {name}")[:31].strip() or f"T{n}"
        while title in used:
            title = title[:28] + f"_{n}"
        used.add(title)
        t = wb.create_sheet(title)
        width = max(len(r) for r in rows)
        for i, row in enumerate(rows):
            t.append([plain(c) if i == 0 else _number(c) for c in row] + [""] * (width - len(row)))
        for c in t[1]:
            c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), head_fill, wrap
        for row in t.iter_rows(min_row=2):
            for c in row:
                c.border, c.alignment = thin, wrap
        for col in range(1, width + 1):
            longest = max(len(str(t.cell(r, col).value or "")) for r in range(1, t.max_row + 1))
            t.column_dimensions[get_column_letter(col)].width = min(max(10, longest + 2), 60)
        t.freeze_panes = "A2"
        t.auto_filter.ref = t.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- PowerPoint


def to_pptx(doc: Doc) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    brand, ink, muted, soft = (RGBColor.from_string(c) for c in (BRAND, INK, MUTED, BRAND_SOFT))

    def textbox(slide, x, y, w, h):
        tf = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        tf.word_wrap = True
        return tf

    def add_runs(p, text, size, color, bold_all=False):
        for t, b in runs(text):
            r = p.add_run()
            r.text, r.font.size, r.font.bold, r.font.color.rgb = t, Pt(size), b or bold_all, color

    def new_slide(title: str):
        s = prs.slides.add_slide(blank)
        bar = s.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.12))
        bar.fill.solid(); bar.fill.fore_color.rgb = brand; bar.line.fill.background()
        add_runs(textbox(s, 0.6, 0.35, 12.1, 0.9).paragraphs[0], title or doc.title, 28, ink, True)
        foot = textbox(s, 0.6, 7.0, 12.1, 0.35).paragraphs[0]
        add_runs(foot, f"{AGENCY_NAME} · {doc.title}", 10, muted)
        return s

    cover = prs.slides.add_slide(blank)
    bg = cover.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
    bg.fill.solid(); bg.fill.fore_color.rgb = soft; bg.line.fill.background()
    side = cover.shapes.add_shape(1, 0, 0, Inches(0.5), prs.slide_height)
    side.fill.solid(); side.fill.fore_color.rgb = brand; side.line.fill.background()
    add_runs(textbox(cover, 1.1, 0.8, 11, 0.6).paragraphs[0], " ".join(AGENCY_NAME), 22, brand, True)
    add_runs(textbox(cover, 1.1, 2.6, 11, 2).paragraphs[0], doc.title, 40, ink, True)
    sub = textbox(cover, 1.1, 4.5, 11, 2.2)
    for i, line in enumerate(([doc.subtitle] if doc.subtitle else []) + doc.intro):
        add_runs(sub.paragraphs[0] if i == 0 else sub.add_paragraph(), line, 16, muted)

    max_lines = 11

    def text_slides(title: str, lines: list[tuple[str, str]]) -> None:
        while lines:
            chunk, lines = lines[:max_lines], lines[max_lines:]
            tf = textbox(new_slide(title), 0.7, 1.35, 11.9, 5.5)
            for i, (kind, text) in enumerate(chunk):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.space_after = Pt(6)
                if kind == "h":
                    add_runs(p, text, 18, brand, True)
                elif kind == "li":
                    add_runs(p, "•  " + text, 16, ink)
                else:
                    add_runs(p, text, 16, muted if kind == "quote" else ink)

    def table_slides(title: str, rows: list[list[str]], lead: list[tuple[str, str]]):
        head, body = rows[0], rows[1:]
        width = max(len(r) for r in rows)
        slide = None
        for start in range(0, max(len(body), 1), 9):
            part = [head] + body[start:start + 9]
            slide = new_slide(title + (" (devam)" if start else ""))
            top = 1.35
            if lead and not start:  # tablodan önceki kısa açıklama aynı slaytta, tablonun üstünde
                tf = textbox(slide, 0.6, 1.25, 12.1, 0.5 * len(lead))
                for i, (_, text) in enumerate(lead):
                    add_runs(tf.paragraphs[0] if i == 0 else tf.add_paragraph(), text, 14, muted)
                top += 0.45 * len(lead)
            shape = slide.shapes.add_table(len(part), width, Inches(0.6), Inches(top), Inches(12.1), Inches(0.4) * len(part))
            for r, row in enumerate(part):
                for c in range(width):
                    cell = shape.table.cell(r, c)
                    cell.text = ""
                    add_runs(cell.text_frame.paragraphs[0], row[c] if c < len(row) else "", 12,
                             RGBColor(0xFF, 0xFF, 0xFF) if r == 0 else ink, r == 0)
                    if r == 0:
                        cell.fill.solid(); cell.fill.fore_color.rgb = brand
        return slide, len(part)

    for title, blocks in sections(doc):
        lines: list[tuple[str, str]] = []
        last_table = None   # (slide, satır sayısı): tablodan sonra gelen kısa metin altına yazılır
        for b in blocks:
            if b.kind == "table":
                lead = lines if 0 < len(lines) <= 2 else []
                if not lead:
                    text_slides(title, lines)
                lines = []
                last_table = table_slides(title, b.rows, lead)
                continue
            if b.kind == "h":
                lines.append(("h", b.text))
            elif b.kind in ("p", "quote"):
                lines.append((b.kind, b.text))
            elif b.kind in ("ul", "ol"):
                lines += [("li" if b.kind == "ul" else "p", x if b.kind == "ul" else f"{i}. {x}") for i, x in enumerate(b.items, 1)]
        if last_table and lines and len(lines) <= 3:
            slide, n = last_table
            tf = textbox(slide, 0.6, 1.9 + 0.45 * n, 12.1, 1.5)
            for i, (_, text) in enumerate(lines):
                add_runs(tf.paragraphs[0] if i == 0 else tf.add_paragraph(), text, 14, muted)
            lines = []
        text_slides(title, lines)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- Word


def to_docx(doc: Doc) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    d = Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(11)
    d.add_heading(doc.title, 0)
    if doc.subtitle:
        d.add_paragraph().add_run(doc.subtitle).italic = True

    def fill(p, text):
        for t, b in runs(text):
            p.add_run(t).bold = b

    for b in doc.blocks:
        if b.kind == "h":
            d.add_heading(plain(b.text), min(max(b.level - 1, 1), 4))
        elif b.kind == "p":
            fill(d.add_paragraph(), b.text)
        elif b.kind == "quote":
            p = d.add_paragraph(style="Intense Quote" if "Intense Quote" in [s.name for s in d.styles] else None)
            fill(p, b.text)
        elif b.kind in ("ul", "ol"):
            for item in b.items:
                fill(d.add_paragraph(style="List Bullet" if b.kind == "ul" else "List Number"), item)
        elif b.kind == "table":
            width = max(len(r) for r in b.rows)
            t = d.add_table(rows=len(b.rows), cols=width)
            t.style = "Light Grid Accent 1" if "Light Grid Accent 1" in [s.name for s in d.styles] else "Table Grid"
            for r, row in enumerate(b.rows):
                for c in range(width):
                    p = t.cell(r, c).paragraphs[0]
                    fill(p, row[c] if c < len(row) else "")
                    if r == 0:
                        for run in p.runs:
                            run.bold = True
            d.add_paragraph()
        elif b.kind == "hr":
            d.add_paragraph()
    for h in d.paragraphs:
        if h.style.name.startswith("Heading") or h.style.name == "Title":
            for r in h.runs:
                r.font.color.rgb = RGBColor.from_string(BRAND if h.style.name != "Title" else INK)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- HTML


def to_html(doc: Doc, markdown_html: str) -> bytes:
    page = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(doc.title)}</title><style>
body{{font-family:system-ui,'Segoe UI',Roboto,Arial,sans-serif;color:#{INK};max-width:960px;margin:40px auto;padding:0 20px;line-height:1.6}}
header{{border-left:8px solid #{BRAND};padding:8px 16px;margin-bottom:24px;background:#{BRAND_SOFT}}}
h1{{margin:0}} h2{{border-bottom:2px solid #{BRAND_SOFT};padding-bottom:4px;margin-top:32px}} h3{{color:#{BRAND}}}
table{{border-collapse:collapse;width:100%;margin:12px 0;font-size:.92rem}} th{{background:#{BRAND};color:#fff;text-align:left}}
th,td{{padding:6px 10px;border-bottom:1px solid #E3DDD1;vertical-align:top}} tr:nth-child(even) td{{background:#F7F5F0}}
blockquote{{color:#{MUTED};border-left:3px solid #{BRAND};margin:0;padding-left:12px}}
@media print{{h2{{break-before:page}} body{{margin:0}}}}
</style></head><body><header><small>{html.escape(AGENCY_NAME)} · {html.escape(AGENCY_TAGLINE)}</small><h1>{html.escape(doc.title)}</h1>
{f"<p>{html.escape(doc.subtitle)}</p>" if doc.subtitle else ""}</header>{markdown_html}</body></html>"""
    return page.encode()


def _md_to_html(doc: Doc) -> str:
    def inline(text: str) -> str:
        return "".join(f"<b>{html.escape(t)}</b>" if b else html.escape(t) for t, b in runs(text))

    out = []
    for b in doc.blocks:
        if b.kind == "h":
            out.append(f"<h{min(b.level, 4)}>{inline(b.text)}</h{min(b.level, 4)}>")
        elif b.kind == "p":
            out.append(f"<p>{inline(b.text)}</p>")
        elif b.kind == "quote":
            out.append(f"<blockquote>{inline(b.text)}</blockquote>")
        elif b.kind in ("ul", "ol"):
            tag = "ul" if b.kind == "ul" else "ol"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(i)}</li>" for i in b.items) + f"</{tag}>")
        elif b.kind == "table":
            head, *body = b.rows
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>")
        elif b.kind == "hr":
            out.append("<hr>")
    return "\n".join(out)


def export(markdown: str, name: str, fmt: str) -> tuple[bytes, str, str]:
    """(içerik, media type, dosya adı) döndürür."""
    if fmt not in FORMATS:
        raise ValueError(f"Desteklenmeyen biçim: {fmt}")
    doc = parse(markdown, Path(name).stem.replace("-", " ").strip())
    data = {"pdf": to_pdf, "xlsx": to_xlsx, "pptx": to_pptx, "docx": to_docx}.get(fmt)
    content = data(doc) if data else to_html(doc, _md_to_html(doc))
    media_type, ext = FORMATS[fmt]
    return content, media_type, Path(name).stem + ext
