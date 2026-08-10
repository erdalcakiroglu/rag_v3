"""Parse Adaptörü (İP-2 orkestrasyonu).

Sorumluluklar:
  - Tip'e göre dispatch: pdf/docx -> backend (docling/fallback), xlsx/txt -> office.
  - Dosya başına timeout (config: ingestion.parse_timeout_sec).
  - Parse kalite ölçümü + metrics_ingestion(step='parse') — deneme başına AYRI kayıt.
  - OCR fallback: coverage < trigger VE pdf ise 1 (config: max_retry) kez OCR ile
    yeniden dene; ikinci sonuç kabul edilir (Ek-A İP-2).
  - Glif onarımı: metin katmanı VAR ama bozuk kodlamalı (subset font, ToUnicode
    düşmüş) sayfalar tam-sayfa OCR ile yeniden okunur ve SAYFA BAZINDA
    birleştirilir (config: quality.glyph_repair). OCR fallback'ten ayrı koldur —
    orada coverage düşüktür, burada yüksektir ve hiçbir gösterge yanmaz.
  - Hard fail (coverage/garbage eşikleri, config'ten) -> core_files FAILED;
    pipeline diğer dosyalarla devam eder. Başarıda language yazılır.
  - Tablolar/şekiller ParsedDocument'te taşınır; DB'ye YAZILMAZ (İP-8 transaksiyonu).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from dataclasses import dataclass, field

from ...config.loader import EffectiveConfig, load_config
from ...database.config_store import make_db_reader
from ...database.ingestion_repo import (
    get_file,
    insert_metric,
    insert_qc_finding,
    list_pending_files,
    mark_file_failed,
    set_file_language,
)
from ...observability.logging import bind_context, clear_context, get_logger
from ...observability.tracing import add_event, set_span_attributes, start_span
from .backends import get_backend
from .office_backend import parse_txt, parse_xlsx
from .parsed_document import ParsedDocument
from .quality import compute_parse_metrics

PARSE_STEP = "parse"


def run_with_timeout(fn, timeout_sec: float):
    """fn'i timeout ile çalıştırır. Aşımda TimeoutError. (Worker thread MVP'de
    zorla öldürülemez; kalıcı kill İP-10/subprocess konusudur.)"""
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        return fut.result(timeout=timeout_sec)
    except FutTimeout:
        ex.shutdown(wait=False, cancel_futures=True)
        raise TimeoutError(f"parse timeout > {timeout_sec}s")
    finally:
        ex.shutdown(wait=False)


@dataclass
class ParseAttempt:
    attempt_no: int
    ocr: bool
    duration_ms: int
    metrics: dict = field(default_factory=dict)
    parsed: ParsedDocument | None = None
    timed_out: bool = False
    error: str | None = None


@dataclass
class ParseResult:
    file_id: int
    status: str                      # 'PARSED' | 'FAILED'
    parsed: ParsedDocument | None
    attempts: list[ParseAttempt]
    fail_reason: str | None = None


class ParseAdapter:
    def __init__(
        self,
        db=None,
        *,
        config: EffectiveConfig | None = None,
        backend=None,
        timeout_sec: int | None = None,
        logger=None,
    ):
        self.db = db
        if config is not None:
            self.cfg = config
        elif db is not None:
            self.cfg = load_config(db_reader=make_db_reader(db))
        else:
            self.cfg = load_config()
        self.quality = self.cfg.group("quality")
        self.timeout_sec = timeout_sec or int(self.cfg.group("ingestion").parse_timeout_sec)
        if backend is not None:
            self.backend = backend
        else:
            from ...config.settings import ParsingSettings
            ing = self.cfg.group("ingestion")   # M-7: görsel çıkarma config-first
            ps = ParsingSettings()
            self.backend = get_backend(
                ps.backend,
                figure_images=bool(ing.figure_images),
                figure_image_scale=float(ing.figure_image_scale),
                pdf_backend=ps.pdf_backend,
                tableformer_mode=str(getattr(ing, "tableformer_mode", "accurate")),
                parse_num_threads=int(getattr(ing, "parse_num_threads", 4)),
            )
        self.log = logger or get_logger("ingestion.parse")

    # -- saf dispatch (DB yok) ------------------------------------------------
    def parse_path(self, path: str, file_type: str, *, ocr: bool = False,
                   full_page_ocr: bool = False) -> ParsedDocument:
        if file_type in ("pdf", "docx"):
            if full_page_ocr:
                # Yetenek sorulur, varsayılmaz: fallback backend'de bu bayrak
                # yoktur ve TypeError ile dosyayı FAILED yapardı.
                if not getattr(self.backend, "supports_full_page_ocr", False):
                    raise ValueError(
                        f"backend {getattr(self.backend, 'name', '?')} tam-sayfa OCR desteklemiyor"
                    )
                return self.backend.parse(path, file_type, ocr=True, full_page_ocr=True)
            return self.backend.parse(path, file_type, ocr=ocr)
        if file_type == "xlsx":
            return parse_xlsx(path)
        if file_type == "txt":
            return parse_txt(path)
        raise ValueError(f"parse desteklemiyor: {file_type}")

    # -- tek dosya (DB) -------------------------------------------------------
    def parse_file(self, file_id: int) -> ParseResult:
        with self.db.connection() as conn:
            row = get_file(conn, file_id)
        if row is None:
            raise KeyError(f"file_id {file_id} yok")

        file_type, path, file_name = row["file_type"], row["source_path"], row["file_name"]
        bind_context(file=file_name, file_id=file_id)
        try:
            with start_span(
                "ingest.parse",
                file_id=file_id,
                file_name=file_name,
                file_type=file_type,
                component="parse",
            ):
                attempts: list[ParseAttempt] = []

                a1 = self._attempt_and_record(file_id, path, file_type, ocr=False, attempt_no=1)
                attempts.append(a1)
                final = a1

                ocr_cfg = self.quality.ocr_fallback
                if (not a1.timed_out and file_type == "pdf" and ocr_cfg.enabled
                        and a1.metrics.get("coverage", 1.0) < ocr_cfg.trigger_coverage_below):
                    self.log.info("ocr_fallback_triggered",
                                  coverage=a1.metrics.get("coverage"),
                                  trigger=ocr_cfg.trigger_coverage_below)
                    add_event(
                        "ocr_fallback_triggered",
                        coverage=a1.metrics.get("coverage"),
                        trigger=ocr_cfg.trigger_coverage_below,
                    )
                    for r in range(int(ocr_cfg.max_retry)):
                        a = self._attempt_and_record(
                            file_id, path, file_type, ocr=True, attempt_no=2 + r
                        )
                        attempts.append(a)
                        final = a

                final, glif = self._glif_onar(
                    file_id, path, file_type, final, attempts
                )

                status, reason = self._judge(final, file_type)
                set_span_attributes(
                    parse_attempts=len(attempts),
                    parse_status=status,
                    parse_coverage=final.metrics.get("coverage") if final.metrics else None,
                    parse_garbage_ratio=(
                        final.metrics.get("garbage_ratio") if final.metrics else None
                    ),
                )
                with self.db.connection() as conn:
                    if status == "FAILED":
                        mark_file_failed(conn, file_id, reason)
                        self.log.warning("parse_failed", reason=reason)
                    else:
                        lang = final.parsed.language if final.parsed else None
                        set_file_language(conn, file_id, lang)
                        _hard, soft_low = self._flags(final, file_type)
                        if soft_low:
                            insert_qc_finding(
                                conn, file_id=file_id, finding="low_coverage",
                                detail=f"coverage={final.metrics.get('coverage')}",
                            )
                        if glif is not None:
                            insert_qc_finding(
                                conn, file_id=file_id, finding=glif["finding"],
                                detail=glif["detail"],
                            )
                        self.log.info("parse_ok", language=lang,
                                      attempts=len(attempts), low_coverage=soft_low,
                                      glyph_repair=(glif or {}).get("finding"))

                return ParseResult(file_id, status, final.parsed, attempts, reason)
        finally:
            clear_context()

    def parse_pending(self, limit: int | None = None) -> list[ParseResult]:
        with self.db.connection() as conn:
            files = list_pending_files(conn, limit)
        results = []
        for f in files:
            try:
                results.append(self.parse_file(f["file_id"]))
            except Exception as exc:  # bir dosya hatası pipeline'ı durdurmaz
                self.log.error("parse_file_error", file_id=f["file_id"], error=str(exc))
        return results

    # -- internals ------------------------------------------------------------
    def _attempt(self, path: str, file_type: str, ocr: bool, attempt_no: int,
                 *, full_page_ocr: bool = False) -> ParseAttempt:
        t0 = time.perf_counter()
        try:
            parsed = run_with_timeout(
                lambda: self.parse_path(path, file_type, ocr=ocr,
                                        full_page_ocr=full_page_ocr),
                self.timeout_sec,
            )
        except TimeoutError as exc:
            dur = int((time.perf_counter() - t0) * 1000)
            return ParseAttempt(attempt_no, ocr, dur, timed_out=True, error=str(exc))
        except Exception as exc:  # parse hatası -> yutulmaz, kaydedilir
            dur = int((time.perf_counter() - t0) * 1000)
            return ParseAttempt(attempt_no, ocr, dur, error=f"parse error: {exc}")
        dur = int((time.perf_counter() - t0) * 1000)
        metrics = compute_parse_metrics(parsed, file_type)
        return ParseAttempt(attempt_no, ocr, dur, metrics=metrics, parsed=parsed)

    def _attempt_and_record(self, file_id, path, file_type, *, ocr, attempt_no,
                            full_page_ocr: bool = False) -> ParseAttempt:
        att = self._attempt(path, file_type, ocr, attempt_no, full_page_ocr=full_page_ocr)
        hard_fail, soft_low = self._flags(att, file_type)
        detail = {
            "attempt": attempt_no,
            "ocr": ocr,
            "full_page_ocr": full_page_ocr,
            "backend": getattr(self.backend, "name", "?") if file_type in ("pdf", "docx") else "office",
            "attempt_duration_ms": att.duration_ms,
            "timed_out": att.timed_out,
            "error": att.error,
            "hard_fail": hard_fail,
            "soft_flag_low_coverage": soft_low,
        }
        if att.metrics:
            detail.update(att.metrics)
        if att.parsed is not None:
            detail["language"] = att.parsed.language
            detail["warnings"] = att.parsed.parse_warnings[:20]
        with self.db.connection() as conn:
            insert_metric(
                conn, file_id=file_id, step=PARSE_STEP,
                duration_ms=att.duration_ms,
                ok=(not att.timed_out and att.error is None and not hard_fail),
                detail=detail,
            )
        return att

    # -- glif onarımı (bozuk font kodlaması) ----------------------------------
    def _glif_onar(self, file_id, path, file_type, final: ParseAttempt,
                   attempts: list[ParseAttempt]) -> tuple[ParseAttempt, dict | None]:
        """Bozuk kodlamalı sayfaları tam-sayfa OCR ile yeniden okur ve birleştirir.

        `ocr_fallback`tan AYRI çalışır ve onun ARDINDAN gelir: o kol metin
        katmanı YOK olduğunda (coverage düşük), bu kol metin katmanı VAR ama
        anlamsız olduğunda devreye girer. İkinci durumda coverage yüksek,
        garbage_ratio 0.000000 ve quality_score 99 çıkar — mevcut göstergelerin
        hiçbiri yanmaz, tetik bu yüzden ayrı bir ölçüte (imza yoğunluğu) dayanır.

        BULGU HER HÂLÜKÂRDA YAZILIR: onarım kapalıysa ya da backend
        desteklemiyorsa bile bozukluk 'encoding_broken' olarak kaydedilir.
        Ölçülemeyen kusur yönetilemez; sessiz geçmek bu kusurun korpusa ilk
        girişindeki hatanın aynısı olurdu.
        """
        if file_type != "pdf" or final.parsed is None or final.timed_out:
            return final, None
        try:
            cfg = self.quality.glyph_repair
        except AttributeError:            # eski config şeması -> kol yok
            return final, None
        if not cfg.enabled:
            return final, None

        from .glyph_repair import birlestir, bozuk_sayfalar

        bozuk = bozuk_sayfalar(
            final.parsed,
            imza_bin=float(cfg.signature_per_1k),
            c0_bin=float(getattr(cfg, "control_per_1k", 0.0)),
            min_karakter=int(cfg.min_page_chars),
        )
        if not bozuk:
            return final, None

        n_sayfa = len(final.parsed.pages) or 1
        ozet = f"bozuk_sayfa={len(bozuk)}/{n_sayfa} sayfa={sorted(bozuk)[:20]}"
        self.log.warning("glyph_repair_triggered", broken_pages=len(bozuk),
                         total_pages=n_sayfa)
        add_event("glyph_repair_triggered", broken_pages=len(bozuk), total_pages=n_sayfa)

        if not getattr(self.backend, "supports_full_page_ocr", False) or cfg.max_retry < 1:
            return final, {"finding": "encoding_broken",
                           "detail": f"{ozet} onarim=YOK (backend/config)"}

        # MALİYET KAPISI: tam-sayfa OCR dosya düzeyinde bir bayraktır, sayfa
        # düzeyinde seçilemez -> 2 bozuk sayfa için 562 sayfa yeniden okunur.
        # Kapı bulguyu susturmaz, yalnız onarımı atlar; eşik düşürülüp
        # yeniden koşulabilsin diye orana detayda yer verilir.
        oran = len(bozuk) / n_sayfa
        if oran < float(cfg.min_broken_page_ratio):
            return final, {
                "finding": "encoding_broken",
                "detail": (f"{ozet} oran={oran:.4f} < {cfg.min_broken_page_ratio} "
                           f"onarim=ATLANDI (maliyet kapisi)"),
            }

        att = self._attempt_and_record(
            file_id, path, file_type, ocr=True,
            attempt_no=len(attempts) + 1, full_page_ocr=True,
        )
        attempts.append(att)
        if att.parsed is None:
            return final, {"finding": "encoding_broken",
                           "detail": f"{ozet} onarim=BASARISIZ ({att.error or 'timeout'})"}

        birlesik = birlestir(final.parsed, att.parsed, bozuk)
        kalan = bozuk_sayfalar(
            birlesik,
            imza_bin=float(cfg.signature_per_1k),
            c0_bin=float(getattr(cfg, "control_per_1k", 0.0)),
            min_karakter=int(cfg.min_page_chars),
        )
        # İDDİA ETME, DOĞRULA: birleştirme sonrası bozuk sayfa gerçekten
        # düştü mü? Düşmediyse bu bir onarım değildir ve öyle kaydedilmez.
        onarilan = len(bozuk) - len(kalan)
        yeni = ParseAttempt(
            attempt_no=att.attempt_no, ocr=True,
            duration_ms=final.duration_ms + att.duration_ms,
            metrics=compute_parse_metrics(birlesik, file_type), parsed=birlesik,
        )
        detail = f"{ozet} onarilan={onarilan} kalan={len(kalan)}"
        if onarilan <= 0:
            # Kazanç yoksa BİRLEŞTİRME UYGULANMAZ: sağlam sayfaları OCR
            # gürültüsüne maruz bırakmanın karşılığı yok.
            return final, {"finding": "encoding_broken",
                           "detail": f"{detail} (birlestirme UYGULANMADI)"}
        return yeni, {"finding": "encoding_repaired", "detail": detail}

    def _flags(self, att: ParseAttempt, file_type: str) -> tuple[bool, bool]:
        if att.timed_out or att.error is not None or not att.metrics:
            return True, False
        pt = self.quality.parse
        m = att.metrics
        hard = (m["coverage"] < pt.hard_fail_coverage
                or m["garbage_ratio"] > pt.hard_fail_garbage)
        soft = m["coverage"] < pt.soft_flag_coverage
        return hard, soft

    def _judge(self, final: ParseAttempt, file_type: str) -> tuple[str, str | None]:
        if final.timed_out:
            return "FAILED", final.error
        if final.error is not None:
            return "FAILED", final.error
        hard, _soft = self._flags(final, file_type)
        if hard:
            m = final.metrics
            return "FAILED", (
                f"parse hard-fail: coverage={m['coverage']} "
                f"garbage_ratio={m['garbage_ratio']}"
            )
        return "PARSED", None
