"""
WordTOCGenerator - Generates TOC documents using the Synergetics template.

Cross-platform: works on Windows, macOS, and Linux.
No subprocess calls, no external tools, no OS-specific paths.
Only stdlib dependencies: zipfile, re, io, pathlib, datetime.

Usage:
    generator = WordTOCGenerator()
    output_path = generator.generate(toc_data, client_name="Acme Corp")

toc_data schema (ALL fields are REQUIRED):
    {
        "title": str,                        # course name
        "duration": str,                     # e.g. "4 Days"
        "description": str,                  # course overview
        "objectives": [str, ...],            # learning outcomes
        "entry_profile": [str, ...],         # prerequisites
        "methodology": [str, ...],           # training approach steps
        "hardware_requirements": [str, ...], # COURSE-SPECIFIC hardware
        "software_requirements": [str, ...], # COURSE-SPECIFIC software
        "course_contents": [
            {
                "day": int | str,
                "sections": [
                    {"title": str, "topics": [str, ...]}
                ]
            }
        ]
    }

IMPORTANT - hardware_requirements and software_requirements are COURSE-SPECIFIC.
Every training has different needs. Examples:
    React course  -> hardware: ["8 GB RAM", "i5 processor"]
                     software: ["Node.js 18+", "VS Code", "Chrome"]
    Power BI      -> hardware: ["4 GB RAM", "Internet connection"]
                     software: ["Power BI Desktop", "Excel 2016+"]
    Java / Spring -> hardware: ["8 GB RAM", "50 GB HDD"]
                     software: ["JDK 17+", "IntelliJ IDEA / Eclipse"]
    Docker/DevOps -> hardware: ["16 GB RAM", "Virtualisation enabled in BIOS"]
                     software: ["Docker Desktop", "WSL2", "VS Code"]

Never reuse the same hardware/software list across different courses.
The AI generating toc_data must always produce these based on the actual course content.
"""

import io
import re
import zipfile
from datetime import datetime
from pathlib import Path


class WordTOCGenerator:

    def __init__(self):

        BASE_DIR = Path(__file__).resolve().parent.parent

        self.TEMPLATE_PATH = BASE_DIR / "document" / "synergetics_template.docx"
        self.OUTPUT_DIR = BASE_DIR / "storage" / "generated"

        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ==========================================================================
    # XML HELPERS
    # ==========================================================================

    @staticmethod
    def _esc(text: str) -> str:
        """Escape characters that are special inside XML text nodes."""
        return (
            text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    @staticmethod
    def _para_text(xml_fragment: str) -> str:
        """Concatenate all <w:t> inner text found in an XML fragment."""
        return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml_fragment))

    # --------------------------------------------------------------------------
    # Paragraph / run factories
    # --------------------------------------------------------------------------

    def _run(self, text: str, font: str = "Segoe UI", color: str = "000000",
             sz: int = 20, bold: bool = False) -> str:
        b = "<w:b/><w:bCs/>" if bold else ""
        return (
            f"<w:r>"
            f"<w:rPr>"
            f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>'
            f"{b}"
            f'<w:color w:val="{color}"/>'
            f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>'
            f"</w:rPr>"
            f'<w:t xml:space="preserve">{self._esc(text)}</w:t>'
            f"</w:r>"
        )

    def _plain_para(self, text: str, color: str = "0D0D0D", sz: int = 20) -> str:
        """Simple paragraph with 120/120 spacing, Segoe UI font."""
        return (
            "<w:p>"
            '<w:pPr><w:spacing w:before="120" w:after="120"/></w:pPr>'
            f"{self._run(text, color=color, sz=sz)}"
            "</w:p>"
        )

    def _list_para(self, text: str, num_id: str = "1", ilvl: int = 0,
                   color: str = "0D0D0D", sz: int = 20) -> str:
        """Bullet-list paragraph matching the template's ListParagraph style."""
        return (
            "<w:p>"
            "<w:pPr>"
            '<w:pStyle w:val="ListParagraph"/>'
            f'<w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="{num_id}"/></w:numPr>'
            '<w:spacing w:before="120" w:after="120"/>'
            "</w:pPr>"
            f"{self._run(text, color=color, sz=sz)}"
            "</w:p>"
        )

    def _bold_label_para(self, text: str) -> str:
        """Bold section label (e.g. 'Hardware', 'Software [Installed]')."""
        return (
            "<w:p>"
            '<w:pPr><w:spacing w:before="120" w:after="120"/></w:pPr>'
            f'{self._run(text, font="Segoe UI Semibold", color="000000", sz=20, bold=True)}'
            "</w:p>"
        )

    def _title_para(self, text: str) -> str:
        """Large blue dotted-border title paragraph (top of document)."""
        run = (
            "<w:r>"
            "<w:rPr>"
            '<w:rFonts w:ascii="Segoe UI Light" w:hAnsi="Segoe UI Light"/>'
            '<w:color w:val="0070C0"/>'
            '<w:sz w:val="36"/>'
            "</w:rPr>"
            f"<w:t>{self._esc(text)}</w:t>"
            "</w:r>"
        )
        return (
            "<w:p>"
            "<w:pPr>"
            "<w:pBdr>"
            '<w:bottom w:val="dotted" w:color="0070C0" w:sz="4" w:space="1"/>'
            "</w:pBdr>"
            "</w:pPr>"
            f"{run}"
            "</w:p>"
        )

    def _day_header(self, label: str) -> str:
        """Blue dotted-border 'Day N' heading matching the template style."""
        run = (
            "<w:r>"
            "<w:rPr>"
            '<w:rFonts w:ascii="Segoe UI Semibold" w:hAnsi="Segoe UI Semibold"/>'
            '<w:color w:val="0070C0"/>'
            '<w:sz w:val="28"/><w:szCs w:val="28"/>'
            "</w:rPr>"
            f"<w:t>{self._esc(label)}</w:t>"
            "</w:r>"
        )
        return (
            "<w:p>"
            "<w:pPr>"
            "<w:pBdr>"
            '<w:bottom w:val="dotted" w:color="7F7F7F" w:themeColor="text1"'
            ' w:themeTint="80" w:sz="4" w:space="1"/>'
            "</w:pBdr>"
            "</w:pPr>"
            f"{run}"
            "</w:p>"
        )

    def _section_title(self, text: str) -> str:
        """Dark-blue section/module heading inside a day block."""
        run = (
            "<w:r>"
            "<w:rPr>"
            '<w:rFonts w:ascii="Segoe UI Semibold" w:hAnsi="Segoe UI Semibold" w:cs="Segoe UI"/>'
            '<w:color w:val="003399"/>'
            '<w:sz w:val="20"/><w:szCs w:val="20"/>'
            "</w:rPr>"
            f"<w:t>{self._esc(text)}</w:t>"
            "</w:r>"
        )
        return f"<w:p><w:pPr/>{run}</w:p>"

    # ==========================================================================
    # CORE: regex-based XML surgery
    # ==========================================================================

    _PARA_RE = re.compile(r"<w:p[ >].*?</w:p>", re.DOTALL)
    _ROW_RE  = re.compile(r"<w:tr[ >].*?</w:tr>",  re.DOTALL)
    _CELL_RE = re.compile(r"<w:tc>.*?</w:tc>",      re.DOTALL)

    def _replace_right_cell(self, xml: str, left_marker: str,
                             new_paragraphs: str) -> str:
        """
        Find the <w:tr> whose LEFT cell contains *left_marker* in its merged text,
        strip all <w:p> elements from the RIGHT cell, and insert *new_paragraphs*.

        NOTE: cell match offsets are relative to the *row* string (not the doc),
        so we must NOT subtract the row's start position when slicing.
        """
        def patch_row(m: re.Match) -> str:
            row = m.group(0)
            cells = list(self._CELL_RE.finditer(row))
            if len(cells) < 2:
                return row

            left_text = self._para_text(cells[0].group(0))
            if left_marker not in left_text:
                return row

            right_orig = cells[1].group(0)

            # Strip existing paragraphs from the right cell
            right_new = re.sub(r"<w:p[ >].*?</w:p>", "", right_orig, flags=re.DOTALL)

            # Insert the new paragraphs just before the closing </w:tc>
            right_new = right_new.replace("</w:tc>", new_paragraphs + "</w:tc>", 1)

            # Reassemble the row  ← cell offsets are row-relative (no subtraction needed)
            start = cells[1].start()
            end   = cells[1].end()
            return row[:start] + right_new + row[end:]

        return self._ROW_RE.sub(patch_row, xml)

    # --------------------------------------------------------------------------

    def _sanitize(self, toc_data: dict) -> dict:
        """
        Clean AI-generated toc_data before processing.

        AIs sometimes return list fields as a single comma-separated string
        instead of a proper list, e.g.:
            "methodology": "Interactive lectures, hands-on exercises"
        instead of:
            "methodology": ["Interactive lectures", "hands-on exercises"]

        This method fixes that automatically so the generator always gets
        clean lists regardless of how the AI formatted the response.
        """
        LIST_FIELDS = [
            "objectives",
            "entry_profile",
            "methodology",
            "hardware_requirements",
            "software_requirements",
        ]

        cleaned = dict(toc_data)

        for field in LIST_FIELDS:
            value = cleaned.get(field)

            # If AI returned a plain string instead of a list → split it
            if isinstance(value, str):
                # Split on comma or newline, strip whitespace, drop empty items
                items = [
                    item.strip().lstrip("-•·").strip()
                    for item in value.replace("\n", ",").split(",")
                    if item.strip()
                ]
                cleaned[field] = items

            # If it's already a list, clean each item
            elif isinstance(value, list):
                cleaned[field] = [
                    str(item).strip().lstrip("-•·").strip()
                    for item in value
                    if str(item).strip()
                ]

        return cleaned

    # Required fields – every course must supply these
    REQUIRED_FIELDS = [
        "title", "duration", "description", "objectives",
        "entry_profile", "methodology",
        "hardware_requirements", "software_requirements",
        "course_contents",
    ]

    def _validate(self, toc_data: dict) -> None:
        """Raise ValueError if any required field is missing or empty."""
        missing = [f for f in self.REQUIRED_FIELDS if not toc_data.get(f)]
        if missing:
            raise ValueError(
                f"toc_data is missing required fields: {missing}\n"
                f"Note: hardware_requirements and software_requirements must be "
                f"specific to this course (e.g. Power BI needs Power BI Desktop, "
                f"not a generic list)."
            )

    def _rebuild_document_xml(self, raw_xml: str, toc_data: dict) -> str:
        title           = toc_data.get("title", "")
        duration        = toc_data.get("duration", "")
        description     = toc_data.get("description", "")
        objectives      = toc_data.get("objectives", [])
        entry_profile   = toc_data.get("entry_profile", [])
        methodology     = toc_data.get("methodology", [])
        hardware        = toc_data.get("hardware_requirements", [])
        software        = toc_data.get("software_requirements", [])
        course_contents = toc_data.get("course_contents", [])

        xml = raw_xml

        # ── 1. Course title paragraph (large blue font, dotted border) ───────
        _title_replaced = [False]

        def replace_title_para(m: re.Match) -> str:
            if _title_replaced[0]:
                return m.group(0)
            p = m.group(0)
            if 'w:val="0070C0"' in p and 'w:val="36"' in p:
                _title_replaced[0] = True
                return self._title_para(title)
            return p

        xml = self._PARA_RE.sub(replace_title_para, xml)

        # ── 2. Duration ──────────────────────────────────────────────────────
        xml = self._replace_right_cell(xml, "Duration:", self._plain_para(duration))

        # ── 3. Description ───────────────────────────────────────────────────
        xml = self._replace_right_cell(xml, "Description:", self._plain_para(description))

        # ── 4. Objectives ────────────────────────────────────────────────────
        obj_xml = (
            "".join(self._plain_para(o) for o in objectives)
            or self._plain_para("")
        )
        xml = self._replace_right_cell(xml, "Objectives:", obj_xml)

        # ── 5. Participants' Entry Profile ───────────────────────────────────
        ep_intro   = self._plain_para(
            "Participants attending this course must have development experience on:"
        )
        ep_bullets = "".join(self._list_para(i, num_id="1") for i in entry_profile)
        xml = self._replace_right_cell(
            xml, "Participants\u2019 Entry Profile:", ep_intro + ep_bullets
        )

        # ── 6. Training Methodology ──────────────────────────────────────────
        meth_intro  = self._plain_para(
            "The workshop will follow Synergetics methodology of"
        )
        meth_items  = "".join(self._list_para(m, num_id="2") for m in methodology)
        meth_footer = self._plain_para(
            "The workshop will be 100% Hands-On with each participant "
            "having access to system during the session"
        )
        xml = self._replace_right_cell(
            xml, "Training Methodology:", meth_intro + meth_items + meth_footer
        )

        # ── 7. Hardware & Software Requirements ──────────────────────────────
        hw_intro   = self._plain_para(
            "Participant\u2019s as well as Trainer\u2019s Machine are required to have:"
        )
        hw_section = self._bold_label_para("Hardware") + "".join(
            self._list_para(i, num_id="1") for i in hardware
        )
        sw_section = self._bold_label_para("Software [Installed]") + "".join(
            self._list_para(i, num_id="1") for i in software
        )
        xml = self._replace_right_cell(
            xml,
            "Hardware and Software Requirements:",
            hw_intro + hw_section + sw_section,
        )

        # ── 8. Course Contents ───────────────────────────────────────────────
        content_xml = ""
        for day_data in course_contents:
            day_num = day_data.get("day", "")
            content_xml += self._day_header(f"Day {day_num}")

            sections = day_data.get("sections", [])
            if sections:
                for sec in sections:
                    sec_title = sec.get("title", "")
                    topics    = sec.get("topics", [])
                    if sec_title:
                        content_xml += self._section_title(sec_title)
                    for topic in topics:
                        content_xml += self._list_para(topic, num_id="3", color="000000")
            else:
                # Legacy flat "topics" list
                for topic in day_data.get("topics", []):
                    content_xml += self._list_para(topic, num_id="3", color="000000")

        # Locate "Course Contents:" paragraph; replace everything after it
        cc_match = re.search(
            r"<w:p[ >](?:(?!</w:p>).)*?Course Contents:(?:(?!</w:p>).)*?</w:p>",
            xml, re.DOTALL,
        )
        if cc_match:
            cut_at   = cc_match.end()
            body_end = xml.rfind("</w:body>")
            if body_end > cut_at:
                tail = xml[cut_at:body_end]
                # Preserve <w:sectPr> (page/margin settings) if present
                sect_match = re.search(r"<w:sectPr[ >].*?</w:sectPr>", tail, re.DOTALL)
                sect_pr    = sect_match.group(0) if sect_match else ""
                xml = xml[:cut_at] + content_xml + sect_pr + xml[body_end:]

        return xml

    # --------------------------------------------------------------------------

    def _rebuild_header_xml(self, raw_xml: str, title: str) -> str:
        """
        Replace the course-name text in the running page header.
        The template splits the title across two runs; we strip all plain text runs
        in the first paragraph and insert a fresh single run with the new title,
        leaving the tab stop and logo image intact.
        """
        new_text_run = (
            "<w:r>"
            '<w:rPr><w:rFonts w:cstheme="minorHAnsi"/></w:rPr>'
            f"<w:t>{self._esc(title)}</w:t>"
            "</w:r>"
        )

        _done = [False]

        def patch_first_para(m: re.Match) -> str:
            if _done[0]:
                return m.group(0)
            p = m.group(0)
            # Skip the empty style-only paragraph
            if not self._para_text(p).strip():
                return p
            _done[0] = True
            # Remove all <w:r> elements that do NOT contain a <w:drawing>
            p_new = re.sub(
                r"<w:r>(?:(?!<w:drawing>)(?!</w:r>).)*?</w:r>",
                "",
                p,
                flags=re.DOTALL,
            )
            # Insert the new title run right after the closing </w:pPr>
            p_new = p_new.replace("</w:pPr>", "</w:pPr>" + new_text_run, 1)
            return p_new

        return self._PARA_RE.sub(patch_first_para, raw_xml)

    # ==========================================================================
    # PUBLIC API
    # ==========================================================================

    def generate(self, toc_data: dict, client_name: str = "Client") -> str:
        """
        Generate a filled TOC .docx from the Synergetics template.

        Args:
            toc_data    : dict with course details (see module docstring for schema).
            client_name : str used in the output filename.

        Returns:
            Absolute path (str) of the generated .docx file.
        """
        toc_data = self._sanitize(toc_data)  # fix AI formatting issues first
        self._validate(toc_data)
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_client = re.sub(r"[^\w\-]", "_", client_name)
        output_path = self.OUTPUT_DIR / f"TOC_{safe_client}_{timestamp}.docx"

        buf = io.BytesIO()
        with zipfile.ZipFile(self.TEMPLATE_PATH, "r") as zin:
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)

                    if item.filename == "word/document.xml":
                        text = data.decode("utf-8-sig")   # strips BOM if present
                        text = self._rebuild_document_xml(text, toc_data)
                        data = text.encode("utf-8")

                    elif item.filename == "word/header1.xml":
                        text = data.decode("utf-8-sig")
                        text = self._rebuild_header_xml(text, toc_data.get("title", ""))
                        data = text.encode("utf-8")

                    zout.writestr(item, data)

        output_path.write_bytes(buf.getvalue())
        return str(output_path.resolve())


# ==============================================================================
# Demo – run this file directly to generate a sample document
# ==============================================================================
if __name__ == "__main__":
    # ── React 18 course ──────────────────────────────────────────────────────
    # Every course has DIFFERENT hardware/software requirements.
    # A Power BI course would need:
    #   hardware_requirements: ["4 GB RAM", "Internet connection"]
    #   software_requirements: ["Power BI Desktop (latest)", "Excel 2016+", "Chrome"]
    # A Java course would need:
    #   hardware_requirements: ["8 GB RAM", "50 GB HDD", "i5 processor"]
    #   software_requirements: ["JDK 17+", "IntelliJ IDEA / Eclipse", "Maven"]
    # Never reuse requirements across courses — always match the actual training.
    SAMPLE_TOC = {
        "title": "Developing SPA with React 18",
        "duration": "4 Days",
        "description": (
            "React 18 is the latest version of the most popular UI library. "
            "This course gives participants hands-on experience building modern "
            "single-page applications using React 18 and its rich ecosystem."
        ),
        "objectives": [
            "Understand React 18 core concepts including hooks and concurrent features.",
            "Build scalable SPAs using React Router and state management libraries.",
            "Integrate REST APIs and handle async data flows with React Query.",
        ],
        "entry_profile": [
            "Good knowledge of JavaScript (ES6+)",
            "HTML 5 and CSS 3",
            "Basic understanding of REST APIs",
        ],
        "methodology": [
            "Concept Visualization",
            "Active Experimentation",
            "Application Development",
        ],
        "hardware_requirements": [
            "Intel Core i5 or equivalent [2+ GHz]",
            "8 GB RAM minimum (16 GB recommended)",
            "50 GB HDD space",
            "LAN / Internet connectivity",
        ],
        "software_requirements": [
            "Windows 10 / macOS 12 or later",
            "NodeJS 18.x or later",
            "Visual Studio Code",
            "Chrome latest",
        ],
        "course_contents": [
            {
                "day": 1,
                "sections": [
                    {
                        "title": "React Fundamentals",
                        "topics": [
                            "JSX and the Virtual DOM",
                            "Functional Components",
                            "Props and State",
                            "Event Handling",
                        ],
                    },
                    {
                        "title": "React Hooks",
                        "topics": [
                            "useState and useEffect",
                            "useRef and useContext",
                            "Custom Hooks",
                        ],
                    },
                ],
            },
            {
                "day": 2,
                "sections": [
                    {
                        "title": "Component Patterns",
                        "topics": [
                            "Controlled vs Uncontrolled Components",
                            "Higher-Order Components",
                            "Render Props",
                            "Context API",
                        ],
                    },
                ],
            },
            {
                "day": 3,
                "sections": [
                    {
                        "title": "Routing with React Router v6",
                        "topics": [
                            "Route Configuration",
                            "Nested Routes",
                            "Route Guards",
                            "Lazy Loading",
                        ],
                    },
                    {
                        "title": "State Management",
                        "topics": [
                            "Redux Toolkit",
                            "React Query",
                            "Zustand",
                        ],
                    },
                ],
            },
            {
                "day": 4,
                "sections": [
                    {
                        "title": "Advanced React 18 Features",
                        "topics": [
                            "Concurrent Mode",
                            "Suspense and Transitions",
                            "Server Components (RSC)",
                            "Performance Optimisation",
                        ],
                    },
                ],
            },
        ],
    }

    gen = WordTOCGenerator()
    path = gen.generate(SAMPLE_TOC, client_name="DemoClient")
    print(f"Generated: {path}")