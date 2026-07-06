"""`python -m ragintel.eval load|retrieval` (İP-2.1a loader + İP-2.4 benchmark)."""

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


def _open_db():
    from ..config.settings import DbSettings
    from ..database import Database
    return Database(DbSettings()).open()


def _cmd_load(args) -> int:
    from .loader import load_golden_set
    db = _open_db()
    try:
        res = load_golden_set(db, args.path, set_version=args.version)
        print(json.dumps(res.__dict__, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_retrieval(args) -> int:
    from ..config.loader import load_config
    from ..database.config_store import make_db_reader
    from ..retrieval import RetrievalService
    from . import repository as repo
    from .retrieval_benchmark import (
        ServiceRetriever, from_db_rows, from_golden_records, map_gold_chunks, run_benchmark, format_summary,
    )

    db = _open_db()
    try:
        # Kaynak: --from-file (onay öncesi JSONL) veya DB set_version.
        if args.from_file:
            from .models import load_golden_jsonl
            records = from_golden_records(load_golden_jsonl(args.from_file))
            src = f"file:{args.from_file}"
        else:
            with db.connection() as conn:
                rows = repo.list_golden_records(conn, args.golden)
            if not rows:
                print(f"HATA: '{args.golden}' set'inde kayıt yok (yüklendi mi?). "
                      f"--from-file ile JSONL'den de koşabilirsiniz.", file=sys.stderr)
                return 2
            records = from_db_rows(rows)
            src = f"db:{args.golden}"

        with db.connection() as conn:
            mapping = map_gold_chunks(conn, records)

        cfg = load_config(db_reader=make_db_reader(db))
        service = RetrievalService(db=db, config=cfg)
        retriever = ServiceRetriever(service, args.variant)
        result = run_benchmark(records, mapping, retriever, top_k=args.top_k)
        result["source"] = src

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_summary(result))
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(prog="python -m ragintel.eval")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ld = sub.add_parser("load", help="Golden set JSONL'i DB'ye yükle")
    ld.add_argument("path")
    ld.add_argument("--version", required=True)

    rt = sub.add_parser("retrieval", help="Retrieval benchmark (İP-2.4)")
    rt.add_argument("--golden", default="v0", help="DB set_version (varsayılan v0)")
    rt.add_argument("--from-file", default=None, help="DB yerine JSONL dosyasından koş (onay öncesi)")
    rt.add_argument("--variant", choices=("vector", "hybrid"), required=True)
    rt.add_argument("--top-k", type=int, default=None, help="Retrieval derinliği (varsayılan max(k)=20)")
    rt.add_argument("--json", action="store_true", help="Tam JSON sonuç")

    args = parser.parse_args(argv)
    if args.cmd == "load":
        return _cmd_load(args)
    if args.cmd == "retrieval":
        return _cmd_retrieval(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
