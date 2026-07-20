from __future__ import annotations

import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .crawler import InstanceReport
from .resources import GROUPS

TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_html(report: InstanceReport) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.j2")
    return template.render(
        report=report,
        groups=GROUPS,
        generated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def render_pdf(report: InstanceReport, output_path: str) -> None:
    """Render to PDF via HTML -> WeasyPrint. Imported lazily since WeasyPrint
    pulls in system libraries (Pango/Cairo) that not everyone will have
    installed if they only want HTML/DOCX output.
    """
    try:
        from weasyprint import HTML
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "PDF output requires WeasyPrint and its system dependencies. "
            "Install with `pip install weasyprint` and see "
            "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation "
            "for the required system libraries (Pango, Cairo, etc.)."
        ) from exc

    html_content = render_html(report)
    HTML(string=html_content).write_pdf(output_path)
