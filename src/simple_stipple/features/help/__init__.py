"""Public facade for the searchable Simple Stipple user manual."""

from .content import TOC_ENTRIES, TOC_SEARCH_TERMS, build_help_html
from .dialog import HelpDialog

HelpDialog.__module__ = __name__

__all__ = ["HelpDialog", "TOC_ENTRIES", "TOC_SEARCH_TERMS", "build_help_html"]
