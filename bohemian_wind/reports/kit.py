"""Word documents in the Bohemian Renewables house template.

template.docx beside this file carries the house styles, the header with the logo and the footer; this module
fills it: a cover, document control, numbered headings, tables with the teal header row, figures with captions,
and the notice every estimate carries. Needs the python-docx package.
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from .. import ORG, __version__

TEMPLATE = Path(__file__).with_name("template.docx")
LOGO = Path(__file__).resolve().parents[1] / "app" / "static" / "img" / "bohemian-logo.png"
NAVY, TEAL, TINT, GREY, AMBER_FILL, AMBER = "222952", "008781", "E6F3F2", "7F7F7F", "FFF6DD", "EDA100"
BULLET_NUM_ID = 8                     # a round-bullet list of the template
TEXT_W_CM = 15.9                      # A4 less the template's margins
# [COMPLIANCE] Printed on the cover and under every estimate. Do not remove or soften.
NOTICE = ("Developer estimate prepared by Bohemian Renewables with open-source tools. It is not an accredited or "
          "bankable energy yield assessment and must not be used for financing decisions.")


class ReportError(RuntimeError):
    pass


def available() -> bool:
    try:
        import docx  # noqa: F401, PLC0415
    except ImportError:
        return False
    return TEMPLATE.is_file()


class Report:
    def __init__(self, kind: str, project: str, doc_id: str, lines: list | None = None, date: datetime | None = None):
        """kind: the report's name on the cover ("Wind Resource Analysis"); project: the project's name;
        lines: further cover lines (mast, place)."""
        try:
            from docx import Document  # noqa: PLC0415
        except ImportError as e:
            raise ReportError("The Word reports need the python-docx package (pip install python-docx).") from e
        if not TEMPLATE.is_file():
            raise ReportError(f"The report template is missing: {TEMPLATE}")
        self.doc = Document(str(TEMPLATE))
        self.kind, self.project, self.doc_id = kind, project, doc_id
        self.date = date or datetime.now()
        self.n_fig = self.n_tab = 0
        self._foot()
        self.doc.core_properties.title = f"{kind}: {project}"
        self.doc.core_properties.author = ORG
        self.doc.core_properties.created = self.doc.core_properties.modified = self.date
        self._cover(lines or [])

    # ---------- pieces of the page ----------

    def _foot(self) -> None:
        text = f"{self.doc_id}  ·  {self.date:%d %B %Y}"
        for sec in self.doc.sections:
            for part in (sec.footer, sec.first_page_footer, sec.header, sec.first_page_header):
                for p in part.paragraphs:
                    if "[Month Year]" in p.text:
                        for r in p.runs[1:]:
                            r.text = ""
                        p.runs[0].text = text
                for tb in part.tables:
                    for cell in tb._cells:
                        for p in cell.paragraphs:
                            if "[Month Year]" in p.text:
                                for r in p.runs[1:]:
                                    r.text = ""
                                p.runs[0].text = text

    def _run(self, p, text: str, *, bold=False, italic=False, size=None, colour=None):
        from docx.shared import Pt, RGBColor  # noqa: PLC0415
        r = p.add_run(text)
        r.bold, r.italic = bold or None, italic or None
        if size:
            r.font.size = Pt(size)
        if colour:
            r.font.color.rgb = RGBColor.from_string(colour)
        return r

    def _para(self, text: str = "", *, align=None, before=0, after=6, keep=False, **run):
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: PLC0415
        from docx.shared import Pt  # noqa: PLC0415
        p = self.doc.add_paragraph()
        f = p.paragraph_format
        f.space_before, f.space_after, f.keep_with_next = Pt(before), Pt(after), keep or None
        if align:
            p.alignment = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT}[align]
        if text:
            self._run(p, text, **run)
        return p

    def _cover(self, lines: list) -> None:
        from docx.shared import Cm  # noqa: PLC0415
        self._para(after=30)
        self._para(self.kind, align="center", bold=True, size=20, colour=NAVY, after=8)
        self._para(self.project, align="center", size=14, colour=NAVY, after=4)
        for ln in lines:
            self._para(ln, align="center", size=11, colour=GREY, after=2)
        self._para(f"{self.doc_id}  ·  Rev 00", align="center", size=9, colour=TEAL, before=6, after=40)
        if LOGO.is_file():
            p = self._para(align="center", after=40)
            p.add_run().add_picture(str(LOGO), width=Cm(9.5))
        self._para(f"{self.date:%B %Y}", align="center", italic=True, size=11, colour=GREY, after=60)
        self.flag(NOTICE)
        self.page_break()

    def control(self, prepared: str = "Bohemian Wind", basis: list | None = None) -> None:
        """Document control and what the document rests on."""
        self.title("Document control")
        self.table(["Rev", "Date", "Description", "Prepared with", "Reviewed", "Approved"],
                   [["00", f"{self.date:%d/%m/%Y}", "First issue, generated from the project's results", prepared, "", ""]],
                   widths=[1.2, 2.3, 5.6, 2.9, 1.9, 2.0], caption=None)
        self.small(f"{ORG}. Generated by Bohemian Wind v{__version__}, open-source software. A person named under "
                   "Reviewed and Approved takes the document as their own; until then it is a machine-made draft.")
        if basis:
            self.title("What this document rests on")
            self.kv(basis, caption=None)

    def contents(self, titles: list) -> None:
        self.title("Contents")
        for i, t in enumerate(titles, 1):
            self._para(f"{i}    {t}", after=2)
        self.page_break()

    def page_break(self) -> None:
        from docx.enum.text import WD_BREAK  # noqa: PLC0415
        self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    def title(self, text: str) -> None:
        """A heading outside the numbering (document control, contents)."""
        self._para(text, bold=True, size=12, colour=NAVY, before=10, after=6, keep=True)

    def h1(self, text: str) -> None:
        self.doc.add_paragraph(text, style="Heading 1")

    def h2(self, text: str) -> None:
        self.doc.add_paragraph(text, style="Heading 2")

    def p(self, text: str) -> None:
        self._para(text, after=6)

    def small(self, text: str) -> None:
        self._para(text, size=8, colour=GREY, after=6)

    def note(self, text: str) -> None:
        self._para(text, italic=True, size=9, colour=GREY, after=6)

    def bullets(self, items: list) -> None:
        from docx.oxml import OxmlElement  # noqa: PLC0415
        from docx.oxml.ns import qn  # noqa: PLC0415
        for it in items:
            p = self.doc.add_paragraph(str(it), style="List Paragraph")
            num = OxmlElement("w:numPr")
            for tag, val in (("w:ilvl", "0"), ("w:numId", str(BULLET_NUM_ID))):
                e = OxmlElement(tag)
                e.set(qn("w:val"), val)
                num.append(e)
            p._p.get_or_add_pPr().append(num)

    def _shade(self, cell, fill: str) -> None:
        from docx.oxml import OxmlElement  # noqa: PLC0415
        from docx.oxml.ns import qn  # noqa: PLC0415
        sh = OxmlElement("w:shd")
        sh.set(qn("w:val"), "clear")
        sh.set(qn("w:color"), "auto")
        sh.set(qn("w:fill"), fill)
        cell._tc.get_or_add_tcPr().append(sh)

    def _borders(self, table, colour: str = "BFBFBF", left: str | None = None) -> None:
        from docx.oxml import OxmlElement  # noqa: PLC0415
        from docx.oxml.ns import qn  # noqa: PLC0415
        pr = table._tbl.tblPr
        b = OxmlElement("w:tblBorders")
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            e = OxmlElement(f"w:{side}")
            thick = left and side == "left"
            e.set(qn("w:val"), "single")
            e.set(qn("w:sz"), "24" if thick else "4")
            e.set(qn("w:space"), "0")
            e.set(qn("w:color"), left if thick else colour)
            b.append(e)
        pr.append(b)

    def _widths(self, table, widths_cm: list, header: bool = True) -> None:
        """Fixed column widths (set on the grid and on every cell: Word reads one, LibreOffice the other), rows that
        do not break across pages, and a header row that repeats."""
        from docx.oxml import OxmlElement  # noqa: PLC0415
        from docx.oxml.ns import qn  # noqa: PLC0415
        from docx.shared import Cm  # noqa: PLC0415
        lay = OxmlElement("w:tblLayout")
        lay.set(qn("w:type"), "fixed")
        table._tbl.tblPr.append(lay)
        for i, col in enumerate(table.columns):
            col.width = Cm(widths_cm[i])
        for n, row in enumerate(table.rows):
            pr = row._tr.get_or_add_trPr()
            pr.append(OxmlElement("w:cantSplit"))
            if header and n == 0:
                pr.append(OxmlElement("w:tblHeader"))
            for i, c in enumerate(row.cells):
                c.width = Cm(widths_cm[i])

    def _cell(self, cell, text, *, bold=False, colour=None, size=8.5, align=None) -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: PLC0415
        from docx.shared import Pt  # noqa: PLC0415
        p = cell.paragraphs[0]
        p.paragraph_format.space_before = p.paragraph_format.space_after = Pt(2)
        if align == "right":
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        self._run(p, "" if text is None else str(text), bold=bold, colour=colour, size=size)

    def caption(self, kind: str, text: str) -> None:
        if kind == "Figure":
            self.n_fig += 1
            n = self.n_fig
        else:
            self.n_tab += 1
            n = self.n_tab
        self._para(f"{kind} {n}: {text}", italic=True, size=8.5, colour=GREY, align="center", before=3, after=8,
                   keep=kind == "Table")

    def table(self, head: list, rows: list, widths: list | None = None, caption: str | None = "", numeric_from: int | None = 1,
              size: float = 8.5) -> None:
        """A table with the template's teal header row. widths in cm (they are scaled to the text width);
        numeric_from: columns from this one on are set to the right when their text is a number."""
        from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: PLC0415
        if caption:
            self.caption("Table", caption)
        n = len(head)
        widths = widths or [TEXT_W_CM / n] * n
        k = TEXT_W_CM / sum(widths)
        t = self.doc.add_table(rows=1, cols=n)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        self._borders(t)
        for i, h in enumerate(head):
            c = t.rows[0].cells[i]
            self._shade(c, TEAL)
            self._cell(c, h, bold=True, colour="FFFFFF", size=size)
        for r in rows:
            cells = t.add_row().cells
            for i, v in enumerate(list(r)[:n]):
                s = "" if v is None else str(v)
                num = numeric_from is not None and i >= numeric_from and s.replace(",", "").replace(".", "").replace("-", "").replace("+", "").replace("%", "").replace(" ", "").isdigit()
                self._cell(cells[i], s, size=size, align="right" if num else None)
        self._widths(t, [w * k for w in widths])
        self._para(after=4)

    def kv(self, rows: list, caption: str | None = "", label_cm: float = 5.4) -> None:
        """Label and value, the label on the template's pale teal."""
        from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: PLC0415
        if caption:
            self.caption("Table", caption)
        t = self.doc.add_table(rows=0, cols=2)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        self._borders(t)
        for a, b in rows:
            c = t.add_row().cells
            self._shade(c[0], TINT)
            self._cell(c[0], a, bold=True, colour=NAVY)
            self._cell(c[1], b)
        self._widths(t, [label_cm, TEXT_W_CM - label_cm], header=False)
        self._para(after=4)

    def flag(self, text: str, label: str = "[COMPLIANCE]") -> None:
        """A boxed notice: the compliance flag, or a list of things to check."""
        from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: PLC0415
        from docx.shared import Cm, Pt  # noqa: PLC0415
        t = self.doc.add_table(rows=1, cols=1)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        self._borders(t, colour=AMBER_FILL, left=AMBER)
        c = t.rows[0].cells[0]
        c.width = Cm(TEXT_W_CM)
        self._shade(c, AMBER_FILL)
        items = text if isinstance(text, (list, tuple)) else [text]
        for i, it in enumerate(items):
            p = c.paragraphs[0] if i == 0 else c.add_paragraph()
            p.paragraph_format.space_before = p.paragraph_format.space_after = Pt(3)
            if i == 0 and label:
                self._run(p, label + " ", bold=True, size=9)
            self._run(p, str(it), size=9)
        self._para(after=4)

    def figure(self, image, caption: str, width_cm: float = 15.0) -> None:
        """image: PNG bytes, or the path of a picture."""
        from docx.shared import Cm  # noqa: PLC0415
        if image is None:
            return
        if isinstance(image, (str, Path)):
            if not Path(image).is_file():
                return
            src = str(image)
        else:
            src = io.BytesIO(image)
        p = self._para(align="center", after=0, keep=True)
        p.add_run().add_picture(src, width=Cm(min(width_cm, TEXT_W_CM)))
        self.caption("Figure", caption)

    def save(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(path))
        return path
