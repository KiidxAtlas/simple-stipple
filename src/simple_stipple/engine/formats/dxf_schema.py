"""DXF document validation at file-I/O boundaries."""

from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger(__name__)


def validate_dxf_document(document: Any) -> None:
    """Raise when ezdxf reports structural errors in ``document``.

    Auditing is best-effort because supported ezdxf versions do not all expose
    the same audit surface. Reported errors are never ignored.
    """
    try:
        auditor = document.audit()
    except (AttributeError, RuntimeError) as exc:
        LOGGER.debug("ezdxf audit unavailable: %s", exc)
        return
    if not auditor.has_errors:
        return
    details = "; ".join(str(error.message) for error in auditor.errors[:5])
    raise ValueError(
        f"DXF export failed validation ({len(auditor.errors)} error(s)): "
        f"{details}. The file was not written."
    )
