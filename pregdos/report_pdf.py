from __future__ import annotations

import logging
import importlib.resources
from pathlib import Path
from typing import Any, Sequence

from fpdf import FPDF

from . import results


_FONT_DIRS = (
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/dejavu"),
    Path("/usr/local/share/fonts/dejavu"),
)
_FUNDING_TEXT = "Part of the SONORA project, funded by the European Union under PIANOFORTE (grant No 101061037)."
_SONORA_URL = "https://pianoforte-partnership.eu/sonora/"
_PIANOFORTE_URL = "https://pianoforte-partnership.eu/"

logging.getLogger("fontTools").setLevel(logging.WARNING)


def _format_count(value: int | None) -> str:
    if value is None:
        return ""
    sign = "-" if value < 0 else ""
    n = abs(value)
    for suffix, factor in (("G", 1_000_000_000), ("M", 1_000_000), ("k", 1_000)):
        if n >= factor:
            scaled = n / factor
            if scaled >= 9.95 or scaled.is_integer():
                return f"{sign}{scaled:.0f} {suffix}"
            return f"{sign}{scaled:.2f} {suffix}"
    return f"{value}"


def _format_one_sig(value: str | None) -> str:
    if not value:
        return ""
    return results.one_significant_digit(value) or ""


def _format_uncertainty(sd: float | None, unit: str, shared_unit: str, shared_sd: str | None) -> str:
    if sd is None:
        return ""
    display_unit = shared_unit
    display_sd = shared_sd
    try:
        shared_value = abs(float(shared_sd)) if shared_sd else 0.0
    except ValueError:
        shared_value = 0.0
    if 0 < shared_value < 0.01:
        independent = results.humanize_dose(sd, None, unit)
        display_sd = independent["value"]
        display_unit = independent["unit"] or unit
    sd_text = _format_one_sig(display_sd)
    return f"± {sd_text} {display_unit}" if sd_text else ""


class ReportPDF(FPDF):
    def __init__(self, title: str, run_id: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.title = title
        self.run_id = run_id
        self.set_auto_page_break(auto=True, margin=13)
        self.set_margins(18, 12, 10)
        self._font_family = self._load_fonts()
        self.set_font(self._font_family, size=9)
        self.alias_nb_pages()
        self.set_title(title)
        self.set_author("PregDos")
        self.set_creator("PregDos")

    def _load_fonts(self) -> str:
        for font_dir in _FONT_DIRS:
            regular = font_dir / "DejaVuSans.ttf"
            bold = font_dir / "DejaVuSans-Bold.ttf"
            if regular.is_file() and bold.is_file():
                self.add_font("DejaVu", "", regular)
                self.add_font("DejaVu", "B", bold)
                return "DejaVu"
        return "Helvetica"

    def _safe(self, text: Any) -> str:
        value = "" if text is None else str(text)
        if self._font_family == "Helvetica":
            value = value.replace("σ", "sigma")
            return value.encode("latin-1", errors="replace").decode("latin-1")
        return value

    def header(self):
        # The run id is redundant here -- it is in the Run table and repeated in the footer.
        self.set_font(self._font_family, "B", 13)
        self.cell(0, 7, self._safe(self.title), border=0, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-8)
        self.set_font(self._font_family, "", 7)
        self.set_text_color(90, 90, 90)
        self.cell(0, 5, self._safe(f"PregDos report | {self.run_id} | page {self.page_no()} of {{nb}}"), align="C")
        self.set_text_color(0, 0, 0)

    def funding_footer(self):
        if self.get_y() > self.h - 34:
            self.add_page()
        self.set_y(-27)
        logo_y = self.get_y()
        eu_w = 14
        piano_w = 28
        gap = 3
        logos_w = eu_w + gap + piano_w
        logos_x = self.w - self.r_margin - logos_w
        text_w = logos_x - self.l_margin - 8
        self.set_xy(self.l_margin, logo_y + 7)
        self.set_font(self._font_family, "", 6.5)
        self.set_text_color(90, 90, 90)
        text_y = self.get_y()
        auto_page_break = self.auto_page_break
        bottom_margin = self.b_margin
        self.set_auto_page_break(False)
        try:
            self.multi_cell(text_w, 3.4, self._safe(_FUNDING_TEXT), border=0, new_x="LMARGIN", new_y="NEXT")
        finally:
            self.set_auto_page_break(auto_page_break, bottom_margin)
        self._link_text_word(self.l_margin, text_y, _FUNDING_TEXT, "SONORA", _SONORA_URL)
        self._link_text_word(self.l_margin, text_y, _FUNDING_TEXT, "PIANOFORTE", _PIANOFORTE_URL)
        self._footer_logo("eu-flag.png", logos_x, logo_y, eu_w)
        self._footer_logo("pianoforte-logo.png", logos_x + eu_w + gap, logo_y + 1, piano_w)
        self.set_text_color(0, 0, 0)

    def _footer_logo(self, filename: str, x: float, y: float, w: float):
        try:
            logo = importlib.resources.files("pregdos") / "static" / "img" / filename
            if logo.is_file():
                self.image(str(logo), x=x, y=y, w=w)
        except (OSError, ValueError):
            pass

    def _link_text_word(self, x: float, y: float, text: str, word: str, url: str):
        before, _, _ = text.partition(word)
        if not before:
            return
        self.link(
            x + self.get_string_width(self._safe(before)),
            y,
            self.get_string_width(self._safe(word)),
            3.4,
            url,
        )

    def heading(self, text: str):
        self.ln(4)
        self.set_font(self._font_family, "B", 10)
        self.cell(0, 6, self._safe(text), new_x="LMARGIN", new_y="NEXT")

    def paragraph(self, text: str):
        self.set_font(self._font_family, "", 8)
        self.multi_cell(0, 4.5, self._safe(text), new_x="LMARGIN", new_y="NEXT")

    def bullet(self, text: str, *, markdown: bool = False):
        """A dash-bulleted line, hanging-indented.  ``markdown`` enables inline ``**bold**``."""
        self.set_font(self._font_family, "", 8)
        self.set_x(self.l_margin + 4)
        self.multi_cell(
            0, 4.5, self._safe("- " + text),
            new_x="LMARGIN", new_y="NEXT", markdown=markdown,
        )

    def kv_table(self, items: list[tuple[str, Any]], cols: int = 2):
        label_w = 32
        value_w = (self.epw / cols) - label_w
        row_h = 5.2
        self.set_font(self._font_family, "", 8)
        for i in range(0, len(items), cols):
            row = items[i:i + cols]
            for label, value in row:
                self.set_fill_color(238, 241, 245)
                self.set_font(self._font_family, "B", 7.5)
                self.cell(label_w, row_h, self._safe(label), border=1, fill=True)
                self.set_font(self._font_family, "", 7.5)
                self.cell(value_w, row_h, self._clip(value, value_w), border=1)
            if len(row) < cols:
                self.cell(label_w + value_w, row_h, "", border=1)
            self.ln(row_h)

    def _clip(self, text: Any, width: float) -> str:
        value = self._safe(text)
        if self.get_string_width(value) <= width - 2:
            return value
        ellipsis = "..."
        while value and self.get_string_width(value + ellipsis) > width - 2:
            value = value[:-1]
        return value + ellipsis

    def result_table(self, groups: list[dict[str, Any]]):
        for group in groups:
            self._group_heading(group)
            include_status = any(r.get("problem") or r.get("sum") is None for r in group["rows"])
            if include_status:
                widths = [36, 43, 36, 31, 36]
                headers = ["Field", "Result", "Uncertainty (1σ)", "Histories", "Status"]
            else:
                widths = [48, 54, 44, 36]
                headers = ["Field", "Result", "Uncertainty (1σ)", "Histories"]
            self.set_font(self._font_family, "B", 7)
            self.set_fill_color(34, 49, 63)
            self.set_text_color(255, 255, 255)
            for header, width in zip(headers, widths):
                self.cell(width, 5, self._safe(header), border=1, fill=True)
            self.ln(5)
            self.set_text_color(0, 0, 0)

            fill = False
            for row in group["rows"]:
                self._result_row(widths, row, fill, include_status=include_status)
                fill = not fill
            if group["total_sum"] is not None:
                total = {
                    "scorer": group["scorer"],
                    "structure": group["structure"],
                    "quantity": group["quantity"],
                    "field": f"All {group['n_fields']} fields",
                    "field_name": "",
                    "sum": group["total_sum"],
                    "sd": group["total_sd"],
                    "unit": group["unit"],
                    "problem": None,
                    "structure_mass_normalized": False,
                    "structure_volume_normalized": False,
                    "simulated_histories": sum(
                        r.get("simulated_histories") or 0 for r in group["rows"]
                        if r.get("simulated_histories") is not None
                    ) or None,
                    "csv_name": "",
                }
                self._result_row(widths, total, fill, bold=True, include_status=include_status)
            self.ln(2)

    def _group_heading(self, group: dict[str, Any]):
        if self.get_y() > self.page_break_trigger - 18:
            self.add_page()
        self.set_font(self._font_family, "B", 8)
        self.set_fill_color(238, 241, 245)
        text = f"{group['scorer']} | {group['structure']} | {group['quantity']} ({group['unit']})"
        self.cell(0, 6, self._safe(text), border=1, fill=True, new_x="LMARGIN", new_y="NEXT")

    def _result_row(
        self,
        widths: Sequence[float],
        row: dict[str, Any],
        fill: bool,
        bold: bool = False,
        include_status: bool = False,
    ):
        if self.get_y() > self.page_break_trigger - 8:
            self.add_page()
        self.set_fill_color(248, 249, 251) if fill else self.set_fill_color(255, 255, 255)
        self.set_font(self._font_family, "B" if bold else "", 6.6)
        height = 5
        dose = uncertainty = status = ""
        if row.get("problem"):
            dose = "unusable"
            status = row.get("problem") or ""
        elif row.get("sum") is None:
            dose = "multi-bin grid"
            status = "download CSV"
        else:
            formatted = results.humanize_dose(row["sum"], row.get("sd"), row.get("unit", ""))
            dose = " ".join(part for part in (formatted["value"], formatted["unit"]) if part)
            uncertainty = _format_uncertainty(
                row.get("sd"), row.get("unit", ""), formatted["unit"] or "", formatted["sd"]
            )
        field = row.get("field")
        field_text = "-" if field is None else str(field)
        if row.get("field_name"):
            field_text = f"{field_text} - {row['field_name']}"
        values = [
            field_text,
            dose,
            uncertainty,
            _format_count(row.get("simulated_histories")),
        ]
        if include_status:
            values.append(status)
        for value, width in zip(values, widths):
            self.cell(width, height, self._clip(value, width), border=1, fill=True)
        self.ln(height)


def build_report_pdf(
    *,
    study: str,
    run_id: str,
    groups: list[dict[str, Any]],
    warnings: list[str],
    plan_fractions: int | None,
    generated_at: str,
    provenance: dict[str, str],
) -> bytes:
    pdf = ReportPDF("PregDos Report", run_id)
    pdf.add_page()

    pdf.heading("Run")
    pdf.kv_table([
        ("Study", study),
        ("Run ID", run_id),
        ("Generated", generated_at),
        ("Fractions", plan_fractions if plan_fractions else "unavailable"),
    ])

    pdf.heading("Provenance")
    pdf.kv_table([
        ("PregDos", provenance.get("pregdos", "")),
        ("TOPAS", provenance.get("topas", "")),
        ("dicomexport", provenance.get("dicomexport", "")),
        ("Geant4", provenance.get("geant4", "")),
    ])

    if warnings:
        pdf.heading("Warnings")
        for warning in warnings:
            pdf.paragraph(f"- {warning}")

    pdf.heading("Scorer Results")
    if groups:
        pdf.result_table(groups)
    else:
        pdf.paragraph("No scorer output found in this run.")

    pdf.heading("Notes")
    pdf.bullet("PregDos is under active development and validation is ongoing; results should "
               "be checked independently.")
    if plan_fractions:
        pdf.bullet("Reported values are scaled to total course dose using the planned fraction count.")
    else:
        pdf.bullet("Planned fractions were unavailable; reported values use the generated TOPAS plan scale.")
    pdf.bullet("The uncertainty is the 1-sigma Monte-Carlo statistical error.")
    has_ambient = any(group.get("quantity") == "AmbientDoseEquivalent" for group in groups)
    if has_ambient:
        pdf.bullet("AmbientDoseEquivalent H*(10) is scored from **neutrons only**. It excludes "
                   "protons, photons and every other particle, so it is **not** a total dose: a "
                   "structure in or near the beam can receive more absorbed dose from protons "
                   "than this row shows. It is a protection quantity in Sv and must not be added "
                   "to the absorbed-dose rows in Gy.", markdown=True)
    has_dose_to_water = any(group.get("quantity") == "DoseToWater" for group in groups)
    if has_dose_to_water:
        pdf.bullet("DoseToWater is **physical** absorbed dose in Gy; the proton RBE of 1.1 is "
                   "**not** applied to these values.", markdown=True)
    pdf.funding_footer()

    return bytes(pdf.output())
