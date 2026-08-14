"""İP-2.4 Retrieval Benchmark — judge'sız, deterministik retrieval değerlendirmesi.

Akış:
1. Golden kayıtlar (DB'den `set_version` veya JSONL dosyasından) → `EvalRecord`.
2. Ground-truth eşleme: her `gold_evidence.quote` normalize edilip `chunk_text_norm`
   içinde aranır → kaydın "bulunması gereken chunk_id KÜMESİ" (loader ile birebir
   aynı mantık). Eşleme oranı raporlanır (<%95 → İP-2.1'e geri bildirim).
3. `answerable=false` kayıtlar retrieval metriklerinden HARİÇ (gold chunk yok).
4. Her answerable kayıt için retriever (vector|hybrid) sıralı chunk_id döndürür;
   recall@k / MRR / nDCG@k hesaplanır (k=5,10,20). Multi-hop recall gold KÜME
   üzerinden (iki dosyanın kanıtı da bulunmalı).
5. Genel + KATEGORİ-bazlı kırılım. Sonuç OTel span'iyle Langfuse'a gider (7e).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..observability.tracing import set_span_attributes, start_span
from ..text import normalize_for_quote, normalize_for_search
from . import repository as repo
from .metrics import mean, mrr, ndcg_at_k, recall_at_k

DEFAULT_KS = (5, 10, 20)


@dataclass
class EvalRecord:
    id: str
    question: str
    category: str
    doc_scope: str
    answerable: bool
    evidence: list[dict]  # [{file_name, page, sheet, quote}]


def from_db_rows(rows: list[dict]) -> list[EvalRecord]:
    out = []
    for d in rows:
        ev = d.get("gold_evidence") or []
        out.append(EvalRecord(
            id=d["id"], question=d["question"], category=d["category"],
            doc_scope=d["doc_scope"], answerable=bool(d["answerable"]),
            evidence=[{"file_name": e["file_name"], "page": e.get("page"),
                       "sheet": e.get("sheet"), "quote": e["quote"]} for e in ev]))
    return out


def from_golden_records(records) -> list[EvalRecord]:
    """`models.GoldenRecord` listesinden (JSONL yolu — onay öncesi koşular)."""
    out = []
    for r in records:
        out.append(EvalRecord(
            id=r.id, question=r.question, category=r.category.value,
            doc_scope=r.doc_scope, answerable=r.answerable,
            evidence=[{"file_name": e.file_name, "page": e.page, "sheet": e.sheet,
                       "quote": e.quote} for e in r.gold_evidence]))
    return out


# --- Ground-truth eşleme -----------------------------------------------------
@dataclass
class GoldMapping:
    gold_by_id: dict[str, set[int]]
    total_evidence: int
    mapped_evidence: int
    unmapped: list[dict] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.mapped_evidence / self.total_evidence if self.total_evidence else 1.0


def map_gold_chunks(conn, records: list[EvalRecord]) -> GoldMapping:
    """Her answerable kaydın gold chunk_id kümesini quote eşlemesiyle bulur."""
    gold: dict[str, set[int]] = {}
    total = mapped = 0
    unmapped: list[dict] = []
    for rec in records:
        if not rec.answerable:
            continue
        ids: set[int] = set()
        for ev in rec.evidence:
            total += 1
            cands = repo.list_candidate_chunks(
                conn, file_name=ev["file_name"], doc_scope=rec.doc_scope,
                page=ev.get("page"), sheet=ev.get("sheet"))
            qn = normalize_for_quote(ev["quote"])
            hit = [c["chunk_id"] for c in cands if qn and qn in (c["chunk_text_norm"] or "")]
            if hit:
                mapped += 1
                ids.update(hit)
            else:
                unmapped.append({"record_id": rec.id, "file_name": ev["file_name"],
                                 "page": ev.get("page"), "sheet": ev.get("sheet"),
                                 "quote": ev["quote"][:60]})
        gold[rec.id] = ids
    return GoldMapping(gold_by_id=gold, total_evidence=total, mapped_evidence=mapped, unmapped=unmapped)


# --- Korpus parmak izi --------------------------------------------------------
def corpus_fingerprint(conn, doc_scopes: Sequence[str] | None = None) -> dict:
    """Karnenin ölçüldüğü KORPUSU ilan eder: dosya/chunk sayısı + son değişiklik.

    NEDEN VAR: 2026-08-14'te aynı golden, aynı config ve aynı uçla alınmış iki karne
    (GENEL r@10 0.682 ve 0.618) yan yana kıyaslandı ve fark rerank'e yazıldı. Kök
    başkaydı: iki koşum FARKLI korpusta olmuş — arada toplu bir tarama koşup 08-13'te
    silinen altı mükerrer Bankacılık Kanunu baskısını geri getirmişti (1720 chunk,
    korpusun %3.8'i). Karne yalnız quote eşleme oranını basıyordu; hangi korpusta
    ölçtüğünü hiç söylemiyordu, bu yüzden kıyaslanamaz iki sayı kıyaslanabilir SANILDI.
    Aynı kusur ailesi: karne kendi paydasını ilan etmeli.
    """
    dosya = conn.execute("SELECT count(*) FROM core_files;").fetchone()[0]
    chunk = conn.execute("SELECT count(*) FROM core_chunks;").fetchone()[0]
    son = conn.execute(
        "SELECT max(greatest(created_at, updated_at)) FROM core_files;").fetchone()[0]
    kapsam: dict[str, dict[str, int]] = {}
    for sc in sorted({s for s in (doc_scopes or []) if s}):
        d = conn.execute(
            "SELECT count(*) FROM core_files WHERE doc_scope = %s;", (sc,)).fetchone()[0]
        c = conn.execute(
            "SELECT count(*) FROM core_chunks c JOIN core_files f USING (file_id) "
            "WHERE f.doc_scope = %s;", (sc,)).fetchone()[0]
        kapsam[sc] = {"files": int(d), "chunks": int(c)}
    return {"files": int(dosya), "chunks": int(chunk),
            "last_change": son.isoformat() if son is not None else None,
            "by_scope": kapsam}


# --- Retriever ----------------------------------------------------------------
class Retriever(Protocol):
    name: str
    def rank(self, query: str, doc_scope: str, top_k: int) -> list[int]: ...


class ServiceRetriever:
    """RetrievalService'i benchmark retriever'ına sarar (variant: vector|hybrid)."""

    def __init__(self, service, variant: str):
        if variant not in ("vector", "hybrid"):
            raise ValueError(f"Geçersiz variant: {variant!r} (vector|hybrid)")
        self.service = service
        self.variant = variant
        self.name = variant

    def rank(self, query: str, doc_scope: str, top_k: int) -> list[int]:
        uc = {"user_id": "eval", "tenant_id": "eval", "roles": ["eval"],
              "allowed_doc_scopes": [doc_scope]}
        if self.variant == "vector":
            res = self.service.search_vector(query, top_k=top_k, user_ctx=uc)
        else:
            res = self.service.search_hybrid(query, top_k=top_k, user_ctx=uc)
        return [int(r["chunk_id"]) for r in res]


class StoreRetriever:
    """İP-3.6 tuning retriever'ı: `PgRetrievalStore`'u önbelleklenmiş sorgu
    embedding'i + AÇIK parametrelerle (config'ten bağımsız) sürer; opsiyonel
    TEI rerank. Embedding cache aynı sorguyu tekrar embed etmez (sweep hızlanır).
    """

    def __init__(self, store, embed_cache: dict[str, list[float]], *, name: str,
                 method: str = "hybrid", ef_search: int = 100, rrf_k: int = 60,
                 iterative_scan: str = "relaxed_order",
                 dense_weight: float = 1.0, sparse_weight: float = 1.0,
                 sparse_variant: str = "simple", fusion: str = "rrf",
                 rerank: bool = False, rerank_fn=None, pool: int = 20):
        self.store = store
        self.embed = embed_cache
        self.name = name
        self.method = method
        self.ef_search = ef_search
        # M-7: sweep de ÜRETİMLE aynı HNSW semantiğiyle koşmalı (filtreli aday tükenmesi).
        self.iterative_scan = iterative_scan
        self.rrf_k = rrf_k
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.sparse_variant = sparse_variant
        self.fusion = fusion
        self.rerank = rerank
        self.rerank_fn = rerank_fn
        self.pool = pool

    def rank(self, question: str, doc_scope: str, top_k: int) -> list[int]:
        qv = self.embed[question]
        pool_k = max(top_k, self.pool) if self.rerank else top_k
        if self.method == "vector":
            rows = self.store.search_vector(
                query_vector=qv, allowed_doc_scopes=[doc_scope], top_k=pool_k, ef_search=self.ef_search,
                iterative_scan=self.iterative_scan)
        else:
            rows = self.store.search_hybrid(
                query=question, query_vector=qv, normalized_query=normalize_for_search(question),
                allowed_doc_scopes=[doc_scope], top_k=pool_k, ef_search=self.ef_search,
                iterative_scan=self.iterative_scan,
                fusion_strategy=self.fusion, rrf_k=self.rrf_k, dense_weight=self.dense_weight,
                sparse_weight=self.sparse_weight, sparse_variant=self.sparse_variant)
        ids = [int(r["chunk_id"]) for r in rows]
        if self.rerank and ids and self.rerank_fn is not None:
            ids = self.rerank_fn(question, ids, doc_scope)
        return ids[:top_k]


# --- Benchmark ---------------------------------------------------------------
def _aggregate(per_record: list[dict], ks: Sequence[int]) -> dict:
    metric_keys = [f"recall@{k}" for k in ks] + [f"ndcg@{k}" for k in ks] + ["mrr"]
    overall = {m: round(mean(r[m] for r in per_record), 4) for m in metric_keys}
    overall["n"] = len(per_record)
    by_cat: dict[str, dict] = {}
    cats = sorted({r["category"] for r in per_record})
    for cat in cats:
        rows = [r for r in per_record if r["category"] == cat]
        by_cat[cat] = {m: round(mean(r[m] for r in rows), 4) for m in metric_keys}
        by_cat[cat]["n"] = len(rows)
    return {"overall": overall, "by_category": by_cat}


def run_benchmark(records: list[EvalRecord], mapping: GoldMapping, retriever: Retriever,
                  *, ks: Sequence[int] = DEFAULT_KS, top_k: int | None = None) -> dict:
    top_k = top_k or max(ks)
    per_record: list[dict] = []
    with start_span("eval.retrieval_benchmark", variant=retriever.name, top_k=top_k,
                    gold_mapping_rate=round(mapping.rate, 4)):
        for rec in records:
            if not rec.answerable:
                continue
            gold = mapping.gold_by_id.get(rec.id, set())
            ranked = retriever.rank(rec.question, rec.doc_scope, top_k)
            row: dict[str, Any] = {"id": rec.id, "category": rec.category, "gold_n": len(gold)}
            for k in ks:
                row[f"recall@{k}"] = recall_at_k(gold, ranked, k)
                row[f"ndcg@{k}"] = ndcg_at_k(gold, ranked, k)
            row["mrr"] = mrr(gold, ranked)
            per_record.append(row)
        agg = _aggregate(per_record, ks)
        ov = agg["overall"]
        set_span_attributes(
            n_records=ov["n"],
            **{f"overall_{m}": ov[m] for m in ov if m != "n"},
            multi_hop_recall_at_5=agg["by_category"].get("multi_hop", {}).get("recall@5", 0.0),
        )
    return {
        "variant": retriever.name, "top_k": top_k, "ks": list(ks),
        "gold_mapping": {"total_evidence": mapping.total_evidence,
                         "mapped": mapping.mapped_evidence, "rate": round(mapping.rate, 4),
                         "unmapped": mapping.unmapped},
        "aggregate": agg, "per_record": per_record,
    }


def format_summary(result: dict) -> str:
    ks = result["ks"]
    L = [f"=== Retrieval Benchmark — variant={result['variant']} top_k={result['top_k']} ==="]
    gm = result["gold_mapping"]
    L.append(f"Quote→chunk eşleme: {gm['mapped']}/{gm['total_evidence']} = %{gm['rate']*100:.1f}"
             + ("  ⚠ <%95 → İP-2.1 geri bildirim" if gm["rate"] < 0.95 else ""))
    cf = result.get("corpus")
    if cf:
        kapsam = " · ".join(f"{s}: {v['files']}/{v['chunks']}"
                            for s, v in (cf.get("by_scope") or {}).items())
        L.append(f"KORPUS: {cf['files']} dosya · {cf['chunks']} chunk · son değişiklik "
                 f"{cf['last_change']}" + (f" · kapsam(dosya/chunk) {kapsam}" if kapsam else ""))
        L.append("  (bu satır farklı koşumlarda AYNI değilse karneler kıyaslanamaz)")
    cols = [f"recall@{k}" for k in ks] + ["mrr"] + [f"ndcg@{k}" for k in ks]
    header = "kategori".ljust(20) + "n   " + "  ".join(c.ljust(9) for c in cols)
    L.append(header)
    L.append("-" * len(header))
    ov = result["aggregate"]["overall"]
    L.append("GENEL".ljust(20) + f"{ov['n']:<4}" + "  ".join(f"{ov[c]:<9.3f}" for c in cols))
    for cat, m in sorted(result["aggregate"]["by_category"].items()):
        flag = "  ← FAZ G tetik" if cat == "multi_hop" else ""
        L.append(cat.ljust(20) + f"{m['n']:<4}" + "  ".join(f"{m[c]:<9.3f}" for c in cols) + flag)
    return "\n".join(L)
