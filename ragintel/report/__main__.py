"""`python -m ragintel.report ingestion|backfill` (İP-9)."""

from __future__ import annotations

import argparse
import json
import sys


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(prog="python -m ragintel.report")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("ingestion", help="Korpus ingestion raporu")
    rep.add_argument("--json", action="store_true")
    rep.add_argument("--scope", default=None, help="doc_scope ile sınırla")
    bf = sub.add_parser("backfill", help="COMPLETED ama skorsuz dosyalara quality_score yaz")
    args = parser.parse_args(argv)

    from ..config.settings import DbSettings
    from ..database import Database, DatabaseConnectionError

    try:
        db = Database(DbSettings()).open()
    except DatabaseConnectionError as exc:
        print(f"DB bağlantısı kurulamadı: {exc}", file=sys.stderr)
        return 2

    try:
        if args.cmd == "ingestion":
            from .ingestion_report import build_report, render_text
            report = build_report(db, doc_scope=args.scope)
            print(json.dumps(report, ensure_ascii=False, indent=2) if args.json
                  else render_text(report))
        elif args.cmd == "backfill":
            from ..ingestion.qc import QCConsolidator
            result = QCConsolidator(db).backfill_scores()
            print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
