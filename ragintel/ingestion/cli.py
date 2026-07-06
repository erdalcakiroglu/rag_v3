"""`ragintel ingest ...` CLI (İP-10): scan | run | retry | reprocess | status."""

from __future__ import annotations

import argparse
import json
import sys

from .orchestrator import Orchestrator


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ragintel ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Klasörü tara, dosyaları envantere al")
    s.add_argument("folder")
    s.add_argument("--scope", default="default", help="doc_scope etiketi")

    r = sub.add_parser("run", help="PENDING dosyaları uçtan uca işle")
    r.add_argument("--limit", type=int, default=None)

    sub.add_parser("retry", help="retry_count<3 FAILED'leri yeniden dene")

    rp = sub.add_parser("reprocess", help="Tek dosyayı elle yeniden işle")
    rp.add_argument("file_id", type=int)

    sub.add_parser("status", help="Durum sayımlarını yazdır")
    return p


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = _build_parser().parse_args(argv)

    from ..config.settings import DbSettings
    from ..database import Database, DatabaseConnectionError
    from ..database.pool import Database as _Db  # noqa

    try:
        db = Database(DbSettings()).open()
    except DatabaseConnectionError as exc:
        print(f"DB bağlantısı kurulamadı: {exc}", file=sys.stderr)
        return 2

    try:
        orch = Orchestrator(db)
        if args.cmd == "scan":
            report = orch.scan(args.folder, doc_scope=args.scope)
            print(json.dumps(report.summary(), ensure_ascii=False))
        elif args.cmd == "run":
            print(json.dumps(orch.run(limit=args.limit), ensure_ascii=False))
        elif args.cmd == "retry":
            print(json.dumps(orch.retry(), ensure_ascii=False))
        elif args.cmd == "reprocess":
            print(orch.reprocess(args.file_id))
        elif args.cmd == "status":
            print(json.dumps(orch.status(), ensure_ascii=False))
        return 0
    except Exception as exc:   # embedding backend / pipeline hatası -> anlaşılır çıkış
        print(f"HATA: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
