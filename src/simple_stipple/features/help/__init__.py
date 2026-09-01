"""Public facade for the searchable Simple Stipple user manual."""

from .dialog import TOC_ENTRIES, TOC_SEARCH_TERMS, HelpDialog, build_help_html

HelpDialog.__module__ = __name__

__all__ = ["HelpDialog", "TOC_ENTRIES", "TOC_SEARCH_TERMS", "build_help_html"]
