"""
Backstop Executive Recovery Report Package.
Compiles audit-ready, one-page printable HTML recovery reports for C-level executives and judges.
"""

from .generator import ExecutiveReportGenerator, generate_report

__all__ = ["ExecutiveReportGenerator", "generate_report"]
