"""Transaksiyonel Storage Yazımı (İP-8).

Dosya başına TEK transaction: chunks + vectors + tables + figures ya HEP ya HİÇ.
Başarıda core_files.status=COMPLETED; hata olursa transaction rollback edilir ve
AYRI bir transaction'da status=FAILED yazılır (yarım durum imkânsız — kill -9
eşdeğeri: commit edilmemiş transaction Postgres'te geri alınır).

REPROCESS: yazımdan önce dosyanın eski türev kayıtları silinir (CASCADE); öksüz
vektör kalamaz. quality_score YAZILMAZ (İP-9'un işi).

Vektörler COPY BINARY ile toplu yazılır; model_name İP-7 damgası ('bge-m3@ollama')
olarak geçer.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from ...config.loader import EffectiveConfig, load_config
from ...config.settings import StorageSettings
from ...database import storage_repo as repo
from ...database.config_store import make_db_reader
from ...observability.logging import bind_context, clear_context, get_logger
from ...observability.tracing import set_span_attributes, start_span
from ..storage import save_figure_png


class StorageWriteError(RuntimeError):
    """Yazım transaction'ı başarısız (dosya FAILED işaretlendi)."""


@dataclass
class WriteResult:
    file_id: int
    status: str            # 'COMPLETED' | 'FAILED'
    chunks: int
    vectors: int
    tables: int
    figures: int
    fail_reason: str | None = None


class StorageWriter:
    def __init__(self, db, *, config: EffectiveConfig | None = None, logger=None,
                 hnsw_bulk_reindex: bool | None = None):
        self.db = db
        self.cfg = config or (load_config(db_reader=make_db_reader(db)) if db
                              else load_config())
        if hnsw_bulk_reindex is None:
            self.hnsw_bulk_reindex = StorageSettings().hnsw_bulk_reindex
        else:
            self.hnsw_bulk_reindex = hnsw_bulk_reindex
        self.log = logger or get_logger("ingestion.storage")

    def _save_figure_images(self, file_id: int, figures) -> dict[int, str]:
        """M-7: görsel PNG'lerini dosya deposuna yazar → {figure_index: yol}.

        DB transaction'ının DIŞINDA yapılır (dosya G/Ç'sini transaction içinde
        tutmak kilidi uzatır). Yazım BAŞARISIZ olursa ingest'i düşürmeyiz:
        görsel ikincil bir varlıktır — uyarı loglanır, storage_path NULL kalır
        ve metin/citation zinciri etkilenmez.
        """
        paths: dict[int, str] = {}
        root = StorageSettings().root
        for f in figures:
            data = getattr(f, "image_png", None)
            if not data:
                continue
            try:
                paths[f.index] = save_figure_png(root, file_id, f.index, data)
            except OSError as exc:
                self.log.warning("figure_image_write_failed", file_id=file_id,
                                 figure_index=f.index, error=str(exc)[:200])
        return paths

    def write_file(self, file_id: int, embed_result, *, tables=None,
                   figures=None) -> WriteResult:
        """Bir dosyanın tüm türev kayıtlarını tek transaction'da yazar.

        embed_result: İP-7 EmbedResult (items: chunk + vector|None; model_name).
        tables/figures: İP-2 ParsedDocument.tables/figures.
        """
        tables = tables or []
        figures = figures or []
        items = embed_result.items
        model_name = embed_result.model_name or getattr(embed_result, "model_name", "")
        bind_context(file_id=file_id)
        try:
            with start_span("ingest.store", file_id=file_id, component="store"):
                try:
                    fig_paths = self._save_figure_images(file_id, figures)
                    with self.db.connection() as conn:
                        repo.register_vector(conn)
                        repo.delete_file_derived(conn, file_id)

                        # M-2b: SIRA ÖNEMLİ — tablolar chunk'lardan ÖNCE yazılır.
                        # core_tables.table_id IDENTITY'dir; chunk'ın kalıcı tablo
                        # bağını yazabilmek için id'lerin ÖNCEDEN var olması gerekir.
                        repo.insert_tables(conn, file_id, tables)
                        tbl_map = repo.table_id_map(conn, file_id)

                        chunks = [it.chunk for it in items]
                        repo.copy_chunks(conn, file_id, chunks, tbl_map)
                        id_map = repo.chunk_id_map(conn, file_id)

                        vec_rows = [
                            (id_map[it.chunk.chunk_index], it.vector, model_name)
                            for it in items if it.vector is not None
                        ]
                        repo.copy_vectors(conn, vec_rows)
                        repo.insert_figures(conn, file_id, figures, fig_paths)
                        repo.set_status(conn, file_id, "COMPLETED")
                    set_span_attributes(
                        store_chunks=len(chunks),
                        store_vectors=len(vec_rows),
                        store_tables=len(tables),
                        store_figures=len(figures),
                    )
                    self.log.info("storage_committed", chunks=len(chunks),
                                  vectors=len(vec_rows), tables=len(tables),
                                  figures=len(figures))
                    return WriteResult(file_id, "COMPLETED", len(chunks), len(vec_rows),
                                       len(tables), len(figures))
                except Exception as exc:
                    reason = f"storage write failed: {exc}"
                    with self.db.connection() as conn:
                        repo.set_status(conn, file_id, "FAILED", reason)
                    self.log.error("storage_rolled_back", error=str(exc))
                    raise StorageWriteError(reason) from exc
        finally:
            clear_context()

    # --- Toplu ilk yük: HNSW drop/recreate (config flag) ---------------------
    @contextmanager
    def bulk_load(self):
        """Toplu yük bağlamı: flag açıksa HNSW index'i drop eder, çıkışta yeniden
        oluşturur (>100k chunk ilk yükte yazım hızlanır). Varsayılan kapalı: no-op."""
        if not self.hnsw_bulk_reindex:
            yield
            return
        with self.db.connection() as conn:
            repo.drop_vector_index(conn)
        self.log.info("hnsw_dropped_for_bulk_load")
        try:
            yield
        finally:
            # M-4: HNSW build parametreleri config'ten (`storage` grubu).
            st = self.cfg.group("storage")
            with self.db.connection() as conn:
                repo.create_vector_index(conn, m=st.hnsw_m, ef_construction=st.hnsw_ef_construction)
            self.log.info("hnsw_recreated", hnsw_m=st.hnsw_m, ef_construction=st.hnsw_ef_construction)
