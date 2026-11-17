"""Render a laid-out resume to PDF (reportlab) and Word (python-docx).

Both are single-column, standard headings and real text, the format ATS parsers
read most reliably. Links (email, LinkedIn, GitHub, project repos) are clickable.

One-page fitting (fit_one_page) never drops a section. When the PDF runs over a page it
removes, one at a time and re-checking after each:
  1. certifications whose `tracks:` don't include this resume's track,
  2. achievements, lowest `priority:` first,
then shrinks the font down to MIN_SIZE. If that still isn't one page it raises
OnePageError instead of producing a two-page resume. The Word file is written from the
same trimmed content at the same font size, so both files match.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ACCENT_HEX = "1F4E79"
ACCENT = colors.HexColor("#" + ACCENT_HEX)
MUTED = colors.HexColor("#555555")
MAX_SIZE, MIN_SIZE, SIZE_STEP = 10.0, 8.5, 0.25


class OnePageError(RuntimeError):
    """The resume is longer than one page even after every allowed removal at the smallest font."""


@dataclass
class Fitted:
    doc: dict[str, Any]          # the content that fits (a copy; the input is never changed)
    size: float                  # body font size in points
    pdf: bytes
    removed: list[str] = field(default_factory=list)  # names dropped to fit, in the order dropped


def _display(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").rstrip("/")


# =============================================================================
# PDF
# =============================================================================
def _pdf_story(doc: dict[str, Any], size: float) -> list:
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=size, leading=size * 1.25)
    name = ParagraphStyle("name", parent=base, fontName="Helvetica-Bold", fontSize=size * 2.1,
                          leading=size * 2.5, alignment=TA_CENTER)
    center = ParagraphStyle("center", parent=base, alignment=TA_CENTER)
    contact_style = ParagraphStyle("contact", parent=center, fontSize=size * 0.88, leading=size * 1.15)
    head = ParagraphStyle("head", parent=base, fontName="Helvetica-Bold", fontSize=size * 1.1,
                          textColor=ACCENT, spaceBefore=size * 0.6, spaceAfter=1)
    bold = ParagraphStyle("bold", parent=base, fontName="Helvetica-Bold")
    right = ParagraphStyle("right", parent=base, alignment=2, textColor=MUTED, fontSize=size * 0.92)
    bullet = ParagraphStyle("bullet", parent=base)
    c = doc["contact"]
    link = lambda url, text: f'<link href="{escape(url)}" color="#{ACCENT_HEX}">{escape(text)}</link>'  # noqa: E731

    story: list = [
        Paragraph(escape(c["name"].upper()), name),
        Paragraph(f"<i>{escape(doc['headline'])}</i>", center),
        Paragraph(" &#183; ".join([
            escape(c["location"]), link(f"mailto:{c['email']}", c["email"]), escape(c["phone"]),
            link(c["linkedin"], _display(c["linkedin"])), link(c["github"], _display(c["github"]))]), contact_style),
    ]
    width = A4[0] - 1.0 * inch

    def section(title: str) -> None:
        story.append(Paragraph(escape(title), head))
        story.append(HRFlowable(width="100%", thickness=0.6, color=ACCENT, spaceBefore=0, spaceAfter=size * 0.3))

    def row(left: str, right_text: str, split: float = 0.78) -> None:
        t = Table([[Paragraph(left, bold), Paragraph(escape(right_text), right)]],
                  colWidths=[width * split, width * (1 - split)])
        t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)

    def bullets(items: list[str]) -> None:
        story.append(ListFlowable([ListItem(Paragraph(escape(b), bullet), leftIndent=10)
                                   for b in items], bulletType="bullet", start="•", leftIndent=10,
                                  bulletFontSize=size * 0.8))

    section("Experience")
    for e in doc["experience"]:
        row(escape(f"{e['title']} | {e['org']}"), e["dates"])
        bullets(e["bullets"])
    section("Projects")
    for p in doc["projects"]:
        title = link(p["url"], p["name"]) if p["url"] else escape(p["name"])
        row(title, " | ".join(p["stack"]), split=0.42)
        bullets(p["bullets"])
    section("Skills")
    for title, names in doc["skills"]:
        story.append(Paragraph(f"<b>{escape(title)}:</b> {escape(', '.join(names))}", base))
    section("Education")
    for e in doc["education"]:
        row(escape(e["school"]), e["dates"])
        story.append(Table([[Paragraph(escape(e["degree"]), base), Paragraph(escape(e.get("score", "")), right)]],
                           colWidths=[width * 0.75, width * 0.25],
                           style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                  ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    section("Certifications & Achievements")
    certs = [c["name"] for c in doc["certifications"]]
    half = (len(certs) + 1) // 2
    rows = [[Paragraph(f"<b>{escape(certs[i])}</b>", base),
             Paragraph(f"<b>{escape(certs[i + half])}</b>", base) if i + half < len(certs) else ""]
            for i in range(half)]
    story.append(Table(rows, colWidths=[width * 0.5, width * 0.5],
                       style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    story.append(Spacer(1, 1))
    return story


def _build_pdf(doc: dict[str, Any], size: float) -> bytes:
    buffer = io.BytesIO()
    template = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                                 topMargin=0.4 * inch, bottomMargin=0.4 * inch,
                                 title=f"{doc['contact']['name']} - Resume", author=doc["contact"]["name"])
    template.build(_pdf_story(doc, size))
    return buffer.getvalue()


def _pages(data: bytes) -> int:
    return len(PdfReader(io.BytesIO(data)).pages)


def removal_order(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Certifications/achievements one-page fitting may drop, first to go first.

    Off-track certifications come first, then every achievement. Within each group a lower
    `priority` goes first; entries without one go last, and ties go bottom of the list first.
    Certifications with no `tracks:`, or whose tracks include this resume's, are never dropped.
    """
    track = doc.get("track")
    indexed = list(enumerate(doc["certifications"]))

    def key(pair: tuple[int, dict[str, Any]]) -> tuple:
        index, item = pair
        priority = item.get("priority")
        return (priority is None, priority if priority is not None else 0, -index)

    off_track = [p for p in indexed if p[1]["kind"] == "certification" and p[1]["tracks"]
                 and track not in p[1]["tracks"]]
    achievements = [p for p in indexed if p[1]["kind"] == "achievement"]
    return [item for _, item in sorted(off_track, key=key) + sorted(achievements, key=key)]


def fit_one_page(doc: dict[str, Any]) -> Fitted:
    """Trim less relevant items, then shrink the font, until the PDF is one page. See the module docstring."""
    current = dict(doc, certifications=list(doc["certifications"]))
    removed: list[str] = []
    data = _build_pdf(current, MAX_SIZE)
    for item in removal_order(doc):
        if _pages(data) == 1:
            break
        remaining = [c for c in current["certifications"] if c is not item]
        if item["kind"] == "certification" and not any(c["kind"] == "certification" for c in remaining):
            continue  # never empty the Certifications section
        current["certifications"] = remaining
        removed.append(item["name"])
        data = _build_pdf(current, MAX_SIZE)
    size = MAX_SIZE
    while _pages(data) > 1 and size - SIZE_STEP >= MIN_SIZE:
        size -= SIZE_STEP
        data = _build_pdf(current, size)
    if _pages(data) > 1:
        dropped = f"after removing {', '.join(removed)} " if removed else "with nothing left that may be removed "
        raise OnePageError(
            f"The resume is still {_pages(data)} pages {dropped}and shrinking the font to {MIN_SIZE}pt. "
            "Nothing was saved. Shorten some bullets or choose fewer of them for this track in "
            "profile/baseline.yaml.")
    return Fitted(current, size, data, removed)


def write_pdf(doc: dict[str, Any], path: Path) -> int:
    """Fit to one page (raises OnePageError if impossible) and write the PDF. Returns the page count."""
    fitted = fit_one_page(doc)
    Path(path).write_bytes(fitted.pdf)
    return _pages(fitted.pdf)


def write_resume(doc: dict[str, Any], pdf_path: Path, docx_path: Path) -> Fitted:
    """Fit once and write both files from the same trimmed content, so the PDF and Word file match.

    Raises OnePageError, and writes neither file, if the resume can't fit on one page.
    """
    fitted = fit_one_page(doc)
    Path(pdf_path).write_bytes(fitted.pdf)
    _write_docx(fitted.doc, docx_path, fitted.size)
    return fitted


# =============================================================================
# Word
# =============================================================================
def _hyperlink(paragraph, url: str, text: str, bold: bool = False) -> None:
    part = paragraph.part
    rel_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                            is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), ACCENT_HEX)
    props.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(underline)
    if bold:
        props.append(OxmlElement("w:b"))
    run.append(props)
    text_el = OxmlElement("w:t")
    text_el.text = text
    text_el.set(qn("xml:space"), "preserve")
    run.append(text_el)
    link.append(run)
    paragraph._p.append(link)


def _rule_below(paragraph) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", ACCENT_HEX)):
        bottom.set(qn(key), value)
    borders.append(bottom)
    p_pr.append(borders)


def write_docx(doc: dict[str, Any], path: Path) -> None:
    """Write the Word file with the same one-page fitting as the PDF (raises OnePageError if impossible)."""
    fitted = fit_one_page(doc)
    _write_docx(fitted.doc, path, fitted.size)


def _write_docx(doc: dict[str, Any], path: Path, size: float = MAX_SIZE) -> None:
    scale = size / MAX_SIZE
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.27), Inches(11.69)
    section.left_margin = section.right_margin = Inches(0.5)
    section.top_margin = section.bottom_margin = Inches(0.4)
    right_tab = section.page_width - section.left_margin - section.right_margin

    normal = document.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(size)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.space_before = Pt(0)

    def para(align=None):
        p = document.add_paragraph()
        if align is not None:
            p.alignment = align
        return p

    c = doc["contact"]
    p = para(WD_ALIGN_PARAGRAPH.CENTER)
    run = p.add_run(c["name"].upper())
    run.bold, run.font.size = True, Pt(20 * scale)
    p = para(WD_ALIGN_PARAGRAPH.CENTER)
    p.add_run(doc["headline"]).italic = True
    p = para(WD_ALIGN_PARAGRAPH.CENTER)
    p.add_run(f"{c['location']}  ·  ")
    _hyperlink(p, f"mailto:{c['email']}", c["email"])
    p.add_run(f"  ·  {c['phone']}  ·  ")
    _hyperlink(p, c["linkedin"], _display(c["linkedin"]))
    p.add_run("  ·  ")
    _hyperlink(p, c["github"], _display(c["github"]))

    def heading(title: str) -> None:
        h = para()
        h.paragraph_format.space_before = Pt(7)
        h.paragraph_format.space_after = Pt(2)
        r = h.add_run(title)
        r.bold, r.font.size = True, Pt(11 * scale)
        r.font.color.rgb = RGBColor.from_string(ACCENT_HEX)
        _rule_below(h)

    def row(left: str, right_text: str, url: str = "") -> None:
        r_p = para()
        r_p.paragraph_format.tab_stops.add_tab_stop(right_tab, WD_TAB_ALIGNMENT.RIGHT)
        r_p.paragraph_format.space_before = Pt(2)
        if url:
            _hyperlink(r_p, url, left, bold=True)
        else:
            r_p.add_run(left).bold = True
        tail = r_p.add_run(f"\t{right_text}")
        tail.font.size = Pt(9 * scale)
        tail.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    def bullets(items: list[str]) -> None:
        for item in items:
            b = document.add_paragraph(item, style="List Bullet")
            b.paragraph_format.space_after = Pt(0)

    heading("Experience")
    for e in doc["experience"]:
        row(f"{e['title']} | {e['org']}", e["dates"])
        bullets(e["bullets"])
    heading("Projects")
    for pr in doc["projects"]:
        row(pr["name"], " | ".join(pr["stack"]), pr["url"])
        bullets(pr["bullets"])
    heading("Skills")
    for title, names in doc["skills"]:
        s = para()
        s.add_run(f"{title}: ").bold = True
        s.add_run(", ".join(names))
    heading("Education")
    for e in doc["education"]:
        row(e["school"], e["dates"])
        d = para()
        d.paragraph_format.tab_stops.add_tab_stop(right_tab, WD_TAB_ALIGNMENT.RIGHT)
        d.add_run(f"{e['degree']}\t{e.get('score', '')}")
    heading("Certifications & Achievements")
    bullets([c["name"] for c in doc["certifications"]])
    document.save(str(path))
