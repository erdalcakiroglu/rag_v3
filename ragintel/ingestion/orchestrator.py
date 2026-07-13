"""Ingestion Orchestrator (İP-10) — durum makinesi + uçtan uca akış.

Durum makinesi: PENDING → PROCESSING → COMPLETED/FAILED.
Adım sırası: intake (İP-1, scan) → parse → clean → [injection: İP-4] → chunk →
embed → store. Her adım kendi metriğini/QC'sini yazar; orchestrator geçişleri ve
bellek-içi veri akışını yönetir.

- EmbeddingBackendError (Ollama erişilemez): dosya FAILED DEĞİL — PROCESSING→
  PENDING geri alınır, retry_count ARTMAZ, run anlaşılır hatayla durur.
- RETRY: retry_count<3 FAILED'ler PENDING'e çekilir (retry_count++).
- REPROCESS (elle): intake'te FAILED (raw kopyasız) → intake'ten; diğerleri parse'dan.
- Crash-recovery: PROCESSING'de takılı (updated_at > X dk) dosyalar açılışta RETRY'a.
- Sıralı işleme (MVP; paralellik config'te hazır, varsayılan 1).
"""

from __future__ import annotations

from ..config.loader import EffectiveConfig, load_config
from ..database.config_store import make_db_reader
from ..database.ingestion_repo import (
    get_file,
    increment_retry_and_pending,
    intake_made_copy,
    list_failed_retryable,
    list_pending_files,
    list_stuck_processing,
    mark_file_failed,
    revert_to_pending,
    set_status,
    status_counts,
)
from ..observability.logging import bind_context, clear_context, get_logger
from ..observability.tracing import add_event, ensure_tracing, start_span
from .chunking import ChunkAdapter
from .cleaning import CleanAdapter
from .embedding import EmbeddingBackendError, EmbeddingService
from .injection import InjectionAdapter
from .parsing import ParseAdapter
from .persistence import StorageWriteError, StorageWriter
from .qc import QCConsolidator
from .scanner import FolderScanner

# M-4: eskiden `MAX_RETRY = 3` sabitiydi → artık `ingestion.max_retry` (DB > ENV > default).


def _scan_text(doc) -> str:
    """Injection taraması girdisi: gövde metni + tablo düzleştirilmiş metinleri."""
    return doc.body_text + "\n" + "\n".join(t.flattened_text for t in doc.tables)


class Orchestrator:
    def __init__(self, db, *, config: EffectiveConfig | None = None,
                 embedder=None, parse_backend=None, token_counter=None,
                 storage_root=None, logger=None):
        self.db = db
        self.cfg = config or load_config(db_reader=make_db_reader(db))
        self.scanner = FolderScanner(db, config=self.cfg, storage_root=storage_root)
        self.parse = ParseAdapter(db, config=self.cfg, backend=parse_backend)
        self.clean = CleanAdapter(db, config=self.cfg)
        self.injection = InjectionAdapter(db, config=self.cfg)
        self.chunk = ChunkAdapter(db, config=self.cfg, counter=token_counter)
        self.embed = EmbeddingService(db, config=self.cfg, embedder=embedder)
        self.store = StorageWriter(db, config=self.cfg)
        self.qc = QCConsolidator(db, config=self.cfg)
        self.stuck_minutes = int(self.cfg.group("ingestion").stuck_processing_minutes)
        self.log = logger or get_logger("ingestion.orchestrator")
        ensure_tracing()

    # -- CLI eylemleri --------------------------------------------------------
    def scan(self, folder: str, *, doc_scope: str = "default"):
        return self.scanner.scan(folder, doc_scope=doc_scope)

    def run(self, *, limit: int | None = None, recover: bool = True,
            scope: str | None = None) -> dict:
        """PENDING dosyaları uçtan uca işler. EmbeddingBackendError fırlatabilir
        (altyapı hatası — run durur).

        M-7 ön-koşul: `scope=None` → TÜM scope'lar (production davranışı DEĞİŞMEZ).
        Scope verilirse yalnızca o scope'un dosyaları işlenir ve yalnızca o scope'ta
        stuck-recovery yapılır. Testler kendi scope'unu geçer → paylaşılan canlı DB'de
        REPROCESS penceresindeki gerçek dosyalara DOKUNAMAZLAR (mekanik güvence;
        "pencerede süit koşmayın" kuralı artık tek savunma hattı değil).
        """
        if recover:
            self.recover_stuck(scope=scope)
        with self.db.connection() as conn:
            pending = [f["file_id"] for f in list_pending_files(conn, limit, scope=scope)]
        results: dict[str, int] = {"COMPLETED": 0, "FAILED": 0}
        for file_id in pending:
            outcome = self._process_file(file_id)
            results[outcome] = results.get(outcome, 0) + 1
        self.log.info("run_complete", processed=len(pending), **results)
        return results

    def retry(self) -> dict:
        """retry_count < `ingestion.max_retry` FAILED'leri PENDING'e çeker (retry_count++)."""
        max_retry = int(self.cfg.group("ingestion").max_retry)
        with self.db.connection() as conn:
            retryable = list_failed_retryable(conn, max_retry)
            for f in retryable:
                increment_retry_and_pending(conn, f["file_id"])
        self.log.info("retry_scheduled", count=len(retryable))
        return self.run(recover=False)

    def reprocess(self, file_id: int) -> str:
        """Tek dosyayı elle yeniden işler. Başlangıç: intake-FAILED (raw kopyasız)
        → intake'ten; diğerleri parse'dan. retry_count ARTMAZ."""
        with self.db.connection() as conn:
            made_copy = intake_made_copy(conn, file_id)

        if not made_copy:
            self.log.info("reprocess_from_intake", file_id=file_id)
            self.scanner.reintake_file(file_id)
            with self.db.connection() as conn:
                st = get_file(conn, file_id)
            if st is None or st["status"] != "PENDING":
                return "FAILED"          # reintake kurtaramadı
        else:
            with self.db.connection() as conn:
                conn.execute(
                    "UPDATE core_files SET status='PENDING', fail_reason=NULL "
                    "WHERE file_id=%s;", (file_id,))
        return self._process_file(file_id)

    def reprocess_all(self, *, scope: str | None = None, dry_run: bool = False) -> dict:
        """M-7 Aşama 2: korpusun TAMAMINI yeniden işler (chunking/embedding/görsel
        ayarları değiştiğinde mevcut türevler geçersizdir — reprocess şart).

        KESİNTİ = DEVAM: hedef dosyalar ÖNCE toplu olarak PENDING'e çekilir, sonra
        tek tek işlenir. Koşum yarıda kalırsa işlenmemiş dosyalar PENDING kalır;
        `ragintel ingest run` kaldığı yerden devam eder (baştan başlamaz).

        `retry_count` ARTMAZ — bu bir hata kurtarma değil, bilinçli yeniden işlemedir.
        Eski chunk/vektörler dosya BAŞINA, yeni türevler yazılırken silinir
        (delete_file_derived) → korpus tur boyunca sorgulanabilir kalır, tek seferde
        boşalmaz.

        `scope=None` → tüm korpus. Dosya listesi ALINDIĞI ANDA sabitlenir.
        """
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT file_id, file_name FROM core_files "
                "WHERE (%s::text IS NULL OR doc_scope = %s::text) ORDER BY file_id;",
                (scope, scope),
            ).fetchall()
        targets = [(r[0], r[1]) for r in rows]
        if dry_run:
            return {"dry_run": True, "hedef_dosya": len(targets),
                    "scope": scope or "(tümü)",
                    "ornek": [n for _, n in targets[:5]]}

        with self.db.connection() as conn:
            for fid, _ in targets:
                conn.execute(
                    "UPDATE core_files SET status='PENDING', fail_reason=NULL "
                    "WHERE file_id=%s;", (fid,))
        self.log.warning("reprocess_all_scheduled", count=len(targets),
                         scope=scope or "(tümü)")
        # recover=False: az önce KENDİMİZ PENDING yaptık; stuck-recovery'ye gerek yok.
        result = self.run(recover=False, scope=scope)
        result["hedef_dosya"] = len(targets)
        return result

    def status(self) -> dict[str, int]:
        with self.db.connection() as conn:
            return status_counts(conn)

    def recover_stuck(self, scope: str | None = None) -> list[int]:
        """`scope=None` → global kurtarma (production: açılışta her scope taranır)."""
        with self.db.connection() as conn:
            stuck = list_stuck_processing(conn, self.stuck_minutes, scope=scope)
            for fid in stuck:
                increment_retry_and_pending(conn, fid)
        if stuck:
            self.log.warning("recovered_stuck", count=len(stuck), file_ids=stuck[:20],
                             scope=scope or "(tümü)")
        return stuck

    # -- pipeline -------------------------------------------------------------
    def _process_file(self, file_id: int) -> str:
        with self.db.connection() as conn:
            set_status(conn, file_id, "PROCESSING")
        bind_context(file_id=file_id)
        try:
            with start_span("ingest.run", file_id=file_id, component="orchestrator"):
                pr = self.parse.parse_file(file_id)
                if pr.status == "FAILED":
                    add_event("parse_failed", status=pr.status, reason=pr.fail_reason)
                    return "FAILED"
                parsed = pr.parsed

                co = self.clean.clean_file(file_id, parsed)
                if co.status == "FAILED":
                    add_event("clean_failed", status=co.status, reason=co.fail_reason)
                    return "FAILED"
                cleaned = co.result.document

                self.injection.scan_file(
                    file_id, _scan_text(cleaned), raw_text=_scan_text(parsed))
                cho = self.chunk.chunk_file(file_id, cleaned)
                emb = self.embed.embed_file(file_id, cho.chunks)
                wr = self.store.write_file(
                    file_id, emb, tables=cleaned.tables, figures=cleaned.figures
                )
                if wr.status == "COMPLETED":
                    self.qc.finalize_file(file_id)
                add_event("file_completed", status=wr.status, chunks=wr.chunks, vectors=wr.vectors)
                return wr.status
        except EmbeddingBackendError:
            # Altyapı hatası dosya hatası değildir: geri al, run'ı durdur.
            with self.db.connection() as conn:
                revert_to_pending(conn, file_id)
            self.log.error("run_halted_embedding_backend", file_id=file_id)
            raise
        except StorageWriteError:
            return "FAILED"      # store zaten FAILED işaretledi
        except Exception as exc:
            with self.db.connection() as conn:
                mark_file_failed(conn, file_id, f"orchestrator error: {exc}")
            self.log.error("file_pipeline_error", file_id=file_id, error=str(exc))
            return "FAILED"
        finally:
            clear_context()
