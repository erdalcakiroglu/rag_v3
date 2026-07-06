"""`python -m ragintel.config show` — efektif konfigürasyonu kaynağıyla yazdırır.

Her pipeline ayarı için değeri ve geldiği katmanı (db/env/default) gösterir;
ayrıca bootstrap DB/log ayarlarını (şifre maskeli) listeler. DB erişilemezse
zincir ENV>varsayılan ile derlenir ve DB katmanının atlandığı bildirilir.
"""

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

from .export_seed import build_seed_sql
from .loader import EffectiveConfig, load_config
from .settings import DbSettings, LogSettings

_DEFAULT_SEED_PATH = "docs/Config_Seed.sql"


def _load_with_db() -> tuple[EffectiveConfig, str | None]:
    """DB katmanıyla yüklemeyi dener; başarısızsa ENV>varsayılana düşer.

    Returns:
        (config, uyarı) — uyarı DB atlandıysa açıklama, aksi halde None.
    """
    db_settings = DbSettings()
    try:
        # Lazy import: config paketi database'e üst düzeyde bağımlı olmasın.
        from ..database import Database, make_db_reader

        db = Database(db_settings).open()
        try:
            cfg = load_config(db_reader=make_db_reader(db))
        finally:
            db.close()
        return cfg, None
    except Exception as exc:  # DB yok/erişilemez — zinciri DB'siz derle
        return load_config(db_reader=None), f"DB katmanı atlandı ({exc})"


def _render_text(cfg: EffectiveConfig, db_warning: str | None) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("ragintel — Efektif Konfigürasyon (öncelik: db > env > default)")
    lines.append("=" * 60)
    if db_warning:
        lines.append(f"[UYARI] {db_warning}")
        lines.append("")

    for group in sorted(cfg.pipeline):
        lines.append(f"[{group}]")
        values = cfg.pipeline[group]
        srcs = cfg.sources.get(group, {})
        width = max((len(k) for k in values), default=0)
        for key in sorted(values):
            src = srcs.get(key, "default")
            val = json.dumps(values[key], ensure_ascii=False)
            lines.append(f"  {key:<{width}}  = {val:<28}  (kaynak: {src})")
        lines.append("")

    lines.append("[bootstrap.db]  (ENV > varsayılan)")
    for k, v in cfg.db.safe_summary().items():
        lines.append(f"  {k:<20} = {v}")
    lines.append("")
    lines.append("[bootstrap.log]  (ENV > varsayılan)")
    lines.append(f"  level = {cfg.log.level}")
    lines.append(f"  json  = {cfg.log.json_logs}")
    return "\n".join(lines)


def _flatten_sources(prefix: str, value, src, out: list[str]) -> None:
    """İç-içe grupları (ör. quality.embed.anomaly_cosine_high) düz anahtara indirger."""
    if isinstance(value, dict):
        for key in sorted(value):
            sub_src = src.get(key, {}) if isinstance(src, dict) else src
            _flatten_sources(f"{prefix}.{key}", value[key], sub_src, out)
    else:
        out.append(f"{prefix}: {src if isinstance(src, str) else 'default'}")


def _render_sources(cfg: EffectiveConfig, db_warning: str | None) -> str:
    """Grup+anahtar bazında yalnızca kaynağı (db/env/default) listeler (düz)."""
    lines: list[str] = []
    if db_warning:
        lines.append(f"[UYARI] {db_warning}")
    for group in sorted(cfg.pipeline):
        _flatten_sources(group, cfg.pipeline[group], cfg.sources.get(group, {}), lines)
    return "\n".join(lines)


def _render_json(cfg: EffectiveConfig, db_warning: str | None) -> str:
    payload = {
        "priority": "db > env > default",
        "db_warning": db_warning,
        "pipeline": cfg.pipeline,
        "sources": cfg.sources,
        "bootstrap": {
            "db": cfg.db.safe_summary(),
            "log": {"level": cfg.log.level, "json": cfg.log.json_logs},
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _force_utf8_stdout() -> None:
    """Windows konsolunda (cp1252) Türkçe karakter hatasını önle."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(prog="python -m ragintel.config")
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="Efektif konfigürasyonu yazdır")
    show.add_argument("--json", action="store_true", help="JSON çıktısı")
    show.add_argument("--source", action="store_true", help="Yalnızca grup+anahtar → kaynak (db/env/default)")

    seed = sub.add_parser("export-seed", help="Davranışsal config'i Config_Seed.sql olarak üret")
    seed.add_argument("--output", default=_DEFAULT_SEED_PATH, help=f"Çıktı yolu (varsayılan: {_DEFAULT_SEED_PATH})")
    seed.add_argument("--stdout", action="store_true", help="Dosyaya yazmak yerine stdout'a bas")
    seed.add_argument(
        "--on-conflict",
        choices=("do-nothing", "merge"),
        default="do-nothing",
        help="do-nothing: mevcut grubu değiştirme; merge: canlıyı ezmeden eksik anahtarları doldur",
    )

    args = parser.parse_args(argv)

    if args.command == "show":
        cfg, warn = _load_with_db()
        if args.source:
            out = _render_sources(cfg, warn)
        else:
            out = _render_json(cfg, warn) if args.json else _render_text(cfg, warn)
        print(out)
        return 0

    if args.command == "export-seed":
        cfg, warn = _load_with_db()
        sql = build_seed_sql(cfg, on_conflict=args.on_conflict)
        if args.stdout:
            print(sql)
        else:
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(sql, encoding="utf-8")
            note = f" ({warn})" if warn else ""
            print(f"Config seed yazıldı: {path}{note}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
