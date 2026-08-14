"""Folder Scanner — dosya kabul ve envanter (İP-1).

İzlenen klasördeki dosyaları güvenli ve idempotent şekilde `core_files`'a
kaydeder. Kurallar (FAZ1_Is_Plani İP-1):

- Tip tespiti içerikle (python-magic), uzantıyla değil.
- sha256 dedup: aynı checksum -> SKIP (no-op); aynı isim farklı içerik ->
  doc_version + 1.
- Boyut limiti app_config.ingestion.max_file_mb; aşan dosya FAILED + fail_reason.
- Raw dosya depoya kopyalanır (source_path); orijinal klasör asla değişmez.
- Yeni kayıt status=PENDING. Her yeni kayıt için metrics_ingestion(step='intake').
- İdempotent: ikinci tarama no-op (mevcut checksum SKIP; kopya ve metrik tekrar
  yazılmaz).
"""

from __future__ import annotations

import getpass
import os
import time
from dataclasses import dataclass, field
from enum import Enum

from ..config.loader import EffectiveConfig, load_config
from ..database.ingestion_repo import (
    find_file_by_checksum,
    get_file,
    insert_core_file,
    insert_metric,
    mark_file_failed,
    max_doc_version_for_name,
)
from ..database.config_store import make_db_reader
from ..database.pool import Database
from ..observability.logging import bind_context, clear_context, get_logger
from ..observability.tracing import set_span_attributes, start_span
from .filetypes import detect_with_mime
from .storage import copy_to_storage, file_size, sha256_file

INTAKE_STEP = "intake"


class Outcome(str, Enum):
    CREATED = "created"      # yeni geçerli dosya, PENDING
    VERSIONED = "versioned"  # aynı isim, farklı içerik -> doc_version+1
    SKIPPED = "skipped"      # aynı checksum zaten var -> no-op
    FAILED = "failed"        # boyut limiti aşıldı -> FAILED
    REJECTED = "rejected"    # desteklenmeyen içerik tipi -> kayıt açılmaz


@dataclass
class FileResult:
    path: str
    outcome: Outcome
    file_id: int | None = None
    file_type: str | None = None
    doc_version: int | None = None
    reason: str | None = None


@dataclass
class ScanReport:
    results: list[FileResult] = field(default_factory=list)

    def add(self, r: FileResult) -> None:
        self.results.append(r)

    def count(self, outcome: Outcome) -> int:
        return sum(1 for r in self.results if r.outcome is outcome)

    @property
    def total(self) -> int:
        return len(self.results)

    def summary(self) -> dict[str, int]:
        return {o.value: self.count(o) for o in Outcome}


class FolderScanner:
    def __init__(
        self,
        db: Database,
        *,
        config: EffectiveConfig | None = None,
        storage_root: str | None = None,
        upload_user: str | None = None,
        logger=None,
    ):
        self.db = db
        self.cfg = config or load_config(db_reader=make_db_reader(db))
        ingestion = self.cfg.group("ingestion")
        self.max_bytes = int(ingestion.max_file_mb) * 1024 * 1024
        self.allowed = set(ingestion.allowed_types)
        if storage_root is not None:
            self.storage_root = storage_root
        else:
            from ..config.settings import StorageSettings
            self.storage_root = StorageSettings().root
        self.upload_user = upload_user or getpass.getuser()
        self.log = logger or get_logger("ingestion.scanner")

    # -- public ---------------------------------------------------------------
    def scan(
        self, folder: str, *, doc_scope: str = "default", recursive: bool = True
    ) -> ScanReport:
        report = ScanReport()
        for path in self._iter_files(folder, recursive):
            report.add(self._process(path, doc_scope))
        self.log.info("scan_complete", folder=folder, **report.summary())
        return report

    def reintake_file(self, file_id: int) -> FileResult:
        """İP-10 REPROCESS-from-intake: intake'te FAILED olmuş (raw kopyasız)
        tek dosyayı orijinal yolundan yeniden değerlendirir. Geçerliyse depoya
        kopyalar ve PENDING yapar; değilse FAILED kalır."""
        with self.db.connection() as conn:
            row = get_file(conn, file_id)
        if row is None:
            raise KeyError(f"file_id {file_id} yok")
        path, name = row["source_path"], row["file_name"]
        bind_context(file=name, file_id=file_id)
        try:
            with start_span("ingest.intake", file_id=file_id, file_name=name, component="intake"):
                if not os.path.exists(path):
                    self._fail(file_id, "reintake: kaynak dosya yok")
                    return FileResult(path, Outcome.FAILED, file_id=file_id,
                                      reason="kaynak dosya yok")
                ftype, _mime = detect_with_mime(path)
                if ftype is None or ftype not in self.allowed:
                    self._fail(file_id, "reintake: desteklenmeyen içerik tipi")
                    return FileResult(path, Outcome.REJECTED, file_id=file_id)
                size = file_size(path)
                if size > self.max_bytes:
                    self._fail(file_id, f"reintake: hâlâ boyut limiti aşıyor ({size})")
                    return FileResult(path, Outcome.FAILED, file_id=file_id,
                                      reason="hâlâ oversize")
                checksum = sha256_file(path)
                source_path = copy_to_storage(path, self.storage_root, checksum, name)
                set_span_attributes(file_size=size, file_type=ftype, intake_action="reintake")
                with self.db.connection() as conn:
                    conn.execute(
                        "UPDATE core_files SET source_path=%s, file_size=%s, "
                        "status='PENDING', fail_reason=NULL WHERE file_id=%s;",
                        (source_path, size, file_id))
                    insert_metric(conn, file_id=file_id, step=INTAKE_STEP,
                                  duration_ms=0, ok=True,
                                  detail={"action": "reintake", "file_type": ftype,
                                          "file_size": size, "copied": True})
                self.log.info("reintake_ok", file_id=file_id)
                return FileResult(path, Outcome.CREATED, file_id=file_id, file_type=ftype)
        finally:
            clear_context()

    def _fail(self, file_id: int, reason: str) -> None:
        with self.db.connection() as conn:
            mark_file_failed(conn, file_id, reason)

    # -- internals ------------------------------------------------------------
    def _iter_files(self, folder: str, recursive: bool):
        """Taranacak dosyalar — DEPONUN KENDİ ARŞİVİ (`storage_root/raw`) HARİÇ.

        Neden dışlanıyor: izlenen klasör depo köküyle aynı (ya da onu kapsıyor)
        olabilir; `/datafile/ragintel/storage` canlıda tam olarak böyle. O durumda
        `os.walk` `storage/raw/<sha>/…` altındaki KENDİ kopyalarımızı da dosya sanar.
        Checksum dedup bunu normalde SKIP'ler, ama bir dosyanın `core_files` satırı
        silinince checksum kaydı da gider: sonraki tarama dosyayı kendi arşivinden
        DİRİLTİR. 2026-08-14'te tam bu oldu — 08-13'te elenen altı mükerrer Bankacılık
        Kanunu baskısı geri geldi ve retrieval r@10'u 0.682'den 0.618'e düşürdü.
        Arşiv bir GİRDİ kaynağı değil; silme kararı kalıcı olmalı.
        """
        kok = os.path.abspath(self.storage_root)
        arsiv = os.path.join(kok, "raw")
        if recursive:
            for root, dirs, files in os.walk(folder):
                if os.path.abspath(root) == kok:
                    atlanan = [d for d in dirs if os.path.join(kok, d) == arsiv]
                    if atlanan:
                        dirs[:] = [d for d in dirs if os.path.join(kok, d) != arsiv]
                        self.log.info("skip_storage_archive", folder=arsiv)
                if os.path.abspath(root) == arsiv or os.path.abspath(root).startswith(
                        arsiv + os.sep):
                    continue
                for name in sorted(files):
                    yield os.path.join(root, name)
        else:
            for name in sorted(os.listdir(folder)):
                p = os.path.join(folder, name)
                if os.path.isfile(p):
                    yield p

    def _process(self, path: str, doc_scope: str) -> FileResult:
        file_name = os.path.basename(path)
        bind_context(file=file_name)
        t0 = time.perf_counter()
        try:
            with start_span("ingest.intake", file_name=file_name, component="intake"):
                ftype, detected_mime = detect_with_mime(path)
                if ftype is None or ftype not in self.allowed:
                    self.log.warning("reject_unsupported_type",
                                     detected=ftype, mime=detected_mime)
                    return FileResult(path, Outcome.REJECTED, file_type=ftype,
                                      reason=f"desteklenmeyen içerik tipi ({detected_mime})")

                checksum = sha256_file(path)
                size = file_size(path)

                with self.db.connection() as conn:
                    existing = find_file_by_checksum(conn, checksum)
                    if existing is not None:
                        set_span_attributes(file_id=existing["file_id"], intake_action="skip_duplicate")
                        self.log.info("skip_duplicate",
                                      existing_file_id=existing["file_id"])
                        return FileResult(path, Outcome.SKIPPED,
                                          file_id=existing["file_id"],
                                          file_type=ftype,
                                          reason="aynı checksum zaten kayıtlı")

                    maxv = max_doc_version_for_name(conn, file_name)
                    prior_versions = maxv or 0
                    doc_version = (maxv + 1) if maxv else 1

                    oversize = size > self.max_bytes
                    if oversize:
                        status = "FAILED"
                        fail_reason = (
                            f"file_size {size} bayt limiti aşıyor "
                            f"({self.max_bytes} bayt)"
                        )
                        source_path = os.path.abspath(path)
                    else:
                        status = "PENDING"
                        fail_reason = None
                        source_path = copy_to_storage(
                            path, self.storage_root, checksum, file_name
                        )

                    file_id = insert_core_file(
                        conn,
                        file_name=file_name,
                        file_type=ftype,
                        file_size=size,
                        checksum=checksum,
                        source_path=source_path,
                        doc_scope=doc_scope,
                        doc_version=doc_version,
                        status=status,
                        fail_reason=fail_reason,
                        upload_user=self.upload_user,
                    )
                    if file_id is None:
                        self.log.info("skip_race_conflict")
                        return FileResult(path, Outcome.SKIPPED, file_type=ftype,
                                          reason="eşzamanlı kayıt (conflict)")

                    duration_ms = int((time.perf_counter() - t0) * 1000)
                    set_span_attributes(
                        file_id=file_id,
                        file_type=ftype,
                        file_size=size,
                        doc_version=doc_version,
                        intake_action=status.lower(),
                        prior_versions=prior_versions,
                    )
                    insert_metric(
                        conn,
                        file_id=file_id,
                        step=INTAKE_STEP,
                        duration_ms=duration_ms,
                        ok=not oversize,
                        detail={
                            "action": status,
                            "file_type": ftype,
                            "detected_mime": detected_mime,
                            "file_size": size,
                            "doc_version": doc_version,
                            "prior_versions": prior_versions,
                            "is_version_bump": doc_version > 1,
                            "checksum_prefix": checksum[:12],
                            "copied": not oversize,
                        },
                    )

                if oversize:
                    self.log.warning("file_failed_oversize", file_id=file_id,
                                     size=size, limit=self.max_bytes)
                    return FileResult(path, Outcome.FAILED, file_id=file_id,
                                      file_type=ftype, doc_version=doc_version,
                                      reason=fail_reason)

                outcome = Outcome.VERSIONED if doc_version > 1 else Outcome.CREATED
                self.log.info("file_ingested", file_id=file_id,
                              doc_version=doc_version, outcome=outcome.value)
                return FileResult(path, outcome, file_id=file_id, file_type=ftype,
                                  doc_version=doc_version)
        except Exception as exc:  # tek dosya hatası pipeline'ı durdurmaz
            self.log.error("intake_error", error=str(exc))
            return FileResult(path, Outcome.REJECTED, reason=f"hata: {exc}")
        finally:
            clear_context()
