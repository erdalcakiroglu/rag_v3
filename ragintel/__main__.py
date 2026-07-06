"""`ragintel` konsol giriş noktası: `config` ve `ingest` (İP-10) alt komutları."""

from __future__ import annotations

import sys


def _force_utf8() -> None:
    """Windows konsolunda (cp1252) Türkçe karakter hatasını önle."""
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print("Kullanım:")
        print("  ragintel config show [--json]")
        print("  ragintel ingest scan <folder> [--scope S] | run [--limit N]")
        print("                 | retry | reprocess <file_id> | status")
        return 0

    if argv[0] == "config":
        from .config.__main__ import main as config_main

        return config_main(argv[1:])

    if argv[0] == "ingest":
        from .ingestion.cli import main as ingest_main

        return ingest_main(argv[1:])

    print(f"Bilinmeyen komut: {argv[0]!r}. `ragintel --help` deneyin.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
