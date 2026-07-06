"""Ingestion pipeline paketi.

FAZ 1 İP-1..İP-10. İP-1: Folder Scanner (dosya kabul ve envanter).
"""

from .filetypes import SUPPORTED_TYPES, detect_type
from .orchestrator import Orchestrator
from .scanner import FileResult, FolderScanner, Outcome, ScanReport

__all__ = [
    "FolderScanner",
    "ScanReport",
    "FileResult",
    "Outcome",
    "detect_type",
    "SUPPORTED_TYPES",
    "Orchestrator",
]
