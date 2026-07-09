"""İP-2.3 — RAGAS-tarzı eval judge + 4 metrik (DEV-MODE).

Judge = Groq `llama-3.3-70b` (LiteLLM openai-compat, temperature=0). TÜM sonuçlar
`judge=groq/dev-mode` etiketli: **RESMİ KARNE DEĞİL**, geliştirme göstergesidir.
Veri egemenliği (spec İP-2.3: lokal Ollama judge) prod'da sağlanır; H200 erişilemezken
dev-mode Groq kullanılır (bilinçli, etiketli sapma).

RAGAS paketine bağımlı DEĞİL: 4 metrik (faithfulness, answer_relevancy,
context_precision, context_recall) RAGAS metodolojisiyle judge LLM'e yapılandırılmış
JSON sorularak hesaplanır (kontrol + düşük paralellik/maliyet + dev-mode etiket için
self-contained). Metodoloji RAGAS ile hizalı; sayılar dev-göstergedir.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass

from ..config.settings import LiteLLMSettings, OllamaSettings
from ..ingestion.embedding.embedder import OllamaEmbedder
from ..observability.logging import get_logger

JUDGE_LABEL = "groq/dev-mode"  # geriye-uyum sabiti; gerçek etiket dev_label() ile üretilir
_LOG = get_logger("eval.judge")


def dev_label(api_base: str) -> str:
    """Judge etiketi sağlayıcıya göre: hepsi DEV-MODE (resmi karne değil)."""
    b = (api_base or "").lower()
    provider = "deepseek" if "deepseek" in b else "groq" if "groq" in b else "cloud"
    return f"{provider}/dev-mode"


def _verdict(v) -> int:
    """Judge verdict'ini 0/1'e indirger — model int/bool/string dönebilir
    (ör. 'supported'/'unsupported'/'desteklenir'). Negatifler ÖNCE kontrol edilir
    ('not supported' → 0)."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return 1 if int(v) == 1 else 0
    s = str(v).strip().lower()
    if s in ("1", "yes", "true", "evet"):
        return 1
    if s in ("0", "no", "false", "hayır", "hayir", ""):
        return 0
    if any(neg in s for neg in ("unsupport", "not support", "desteklenm", "irrelevant", "ilgisiz", "atfedilem")):
        return 0
    if any(pos in s for pos in ("support", "destekl", "relevant", "attribut", "yararlı", "useful", "ilgili")):
        return 1
    return 0

# RAGAS-tarzı metrik metodolojisi judge prompt'ları (İngilizce talimat + Türkçe içerik).
_SYS = "You are a meticulous RAG evaluation judge. Output ONLY valid minified JSON, no prose."


def _extract_json(text: str):
    """LLM çıktısından ilk JSON nesnesini/dizisini çıkarır (```json sarımına dayanıklı)."""
    if text is None:
        raise ValueError("judge boş yanıt döndürdü")
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # ilk {...} veya [...] bloğunu yakala
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i = t.find(open_c)
        j = t.rfind(close_c)
        if 0 <= i < j:
            try:
                return json.loads(t[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"judge JSON parse edilemedi: {text[:200]!r}")


@dataclass
class Judge:
    """Groq judge istemcisi (dev-mode). temperature=0; ratelimit'e karşı seri +
    backoff'lu retry (düşük paralellik — maliyet/ratelimit gözetimi)."""

    model: str | None = None
    settings: LiteLLMSettings | None = None
    inter_call_delay: float = 0.4  # ardışık judge çağrıları arası kısa bekleme
    max_retries: int = 4

    def __post_init__(self):
        self.settings = self.settings or LiteLLMSettings()
        self.model = self.model or self.settings.model or "llama-3.3-70b-versatile"
        self.label = dev_label(self.settings.api_base)

    def ask_json(self, user: str):
        import litellm

        kwargs = {
            "model": f"{self.settings.provider}/{self.model}",
            "messages": [{"role": "system", "content": _SYS}, {"role": "user", "content": user}],
            "temperature": 0,
            "api_base": self.settings.api_base,
            "timeout": self.settings.request_timeout,
        }
        if self.settings.api_key:
            kwargs["api_key"] = self.settings.api_key
        from ..llm.gateway import is_retryable, retry_wait_seconds

        last_exc = None
        for attempt in range(self.max_retries):
            try:
                if self.inter_call_delay:
                    time.sleep(self.inter_call_delay)
                resp = litellm.completion(**kwargs)
                return _extract_json(resp.choices[0].message.content)
            except Exception as exc:  # ratelimit/5xx → Retry-After'a uyan backoff; parse hatası → kısa retry
                last_exc = exc
                wait = retry_wait_seconds(exc, attempt) if is_retryable(exc) else 2.0 * (2**attempt)
                _LOG.warning("judge_retry", attempt=attempt + 1, error=str(exc)[:120], wait=round(wait, 1))
                time.sleep(wait)
        raise RuntimeError(f"judge çağrısı {self.max_retries} denemede başarısız: {last_exc}")


# --- Embedding (answer_relevancy için bge-m3, ADR-012 tutarlılığı) ------------
class JudgeEmbedder:
    def __init__(self):
        s = OllamaSettings()
        self._emb = OllamaEmbedder(s.base_url, model=s.model, timeout=s.timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._emb.embed_batch(texts)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _join_contexts(contexts: list[str], *, max_chars: int = 8000) -> str:
    blocks = []
    total = 0
    for i, c in enumerate(contexts):
        piece = f"[{i}] {c}".strip()
        if total + len(piece) > max_chars:
            break
        blocks.append(piece)
        total += len(piece)
    return "\n\n".join(blocks)


# --- 4 RAGAS-tarzı metrik ----------------------------------------------------
def faithfulness(judge: Judge, answer: str, contexts: list[str]) -> float:
    """Yanıt olgusal ifadelere bölünür; her ifade bağlamdan çıkarılabiliyor mu?
    skor = desteklenen / toplam."""
    if not answer.strip():
        return 0.0
    prompt = (
        "Görev: YANIT'ı bağımsız olgusal ifadelere böl. Her ifade için SADECE verilen "
        "BAĞLAM'dan doğrudan çıkarılabiliyorsa verdict=1, çıkarılamıyor/çelişiyorsa verdict=0.\n\n"
        f"BAĞLAM:\n\"\"\"\n{_join_contexts(contexts)}\n\"\"\"\n\n"
        f"YANIT:\n\"\"\"\n{answer}\n\"\"\"\n\n"
        'JSON şeması: {"statements":[{"statement":"...","verdict":0}]}'
    )
    data = judge.ask_json(prompt)
    sts = data.get("statements") if isinstance(data, dict) else data
    if not sts:
        return 1.0  # olgusal iddia yok → çelişki de yok
    verdicts = [_verdict(s.get("verdict", 0)) for s in sts]
    return sum(verdicts) / len(verdicts)


def context_recall(judge: Judge, ground_truth: str, contexts: list[str]) -> float:
    """Referans cevap ifadelere bölünür; her ifade bağlamlarca destekleniyor mu?
    skor = atfedilen / toplam."""
    prompt = (
        "Görev: REFERANS CEVAP'ı bağımsız ifadelere böl. Her ifade için verilen "
        "BAĞLAM'larca destekleniyorsa (atfedilebiliyorsa) verdict=1, yoksa verdict=0.\n\n"
        f"BAĞLAM:\n\"\"\"\n{_join_contexts(contexts)}\n\"\"\"\n\n"
        f"REFERANS CEVAP:\n\"\"\"\n{ground_truth}\n\"\"\"\n\n"
        'JSON şeması: {"statements":[{"statement":"...","verdict":0}]}'
    )
    data = judge.ask_json(prompt)
    sts = data.get("statements") if isinstance(data, dict) else data
    if not sts:
        return 0.0
    verdicts = [_verdict(s.get("verdict", 0)) for s in sts]
    return sum(verdicts) / len(verdicts)


def context_precision(judge: Judge, question: str, contexts: list[str], ground_truth: str) -> float:
    """Her bağlam parçası, soruyu referans cevaba ulaştırmak için yararlı mı?
    Sıra-ağırlıklı precision (RAGAS formülü)."""
    if not contexts:
        return 0.0
    prompt = (
        "Görev: Sıralı BAĞLAM parçalarının her biri, SORU'yu REFERANS CEVAP'a ulaşacak şekilde "
        "yanıtlamak için yararlı/ilgili mi? Sırayı KORU; her parça için 1 (yararlı) veya 0.\n\n"
        f"SORU: {question}\n"
        f"REFERANS CEVAP: {ground_truth}\n\n"
        f"BAĞLAM PARÇALARI (sıralı):\n\"\"\"\n{_join_contexts(contexts)}\n\"\"\"\n\n"
        'JSON şeması: {"verdicts":[1,0]}  (parça sayısıyla aynı uzunlukta, aynı sırada, 0-indeks)'
    )
    data = judge.ask_json(prompt)
    raw = data.get("verdicts") if isinstance(data, dict) else data
    n = min(len(contexts), len(raw or []))
    verdicts = [_verdict(raw[i]) for i in range(n)]
    total_rel = sum(verdicts)
    if total_rel == 0:
        return 0.0
    cum = 0
    s = 0.0
    for k, v in enumerate(verdicts, start=1):
        if v:
            cum += 1
            s += cum / k
    return s / total_rel


def answer_relevancy(judge: Judge, embedder: JudgeEmbedder, question: str, answer: str) -> float:
    """Yanıttan üretilen soruların orijinal soruyla kosinüs benzerliği (bge-m3);
    kaçamak yanıt → 0."""
    if not answer.strip():
        return 0.0
    prompt = (
        "Görev: Aşağıdaki YANIT'ın tam olarak cevap verdiği 3 olası SORU üret. Ayrıca yanıt "
        "kaçamak/belirsiz ise (ör. 'bilmiyorum', 'bulunamadı', 'yanıt üretilemedi') noncommittal=1.\n\n"
        f"YANIT:\n\"\"\"\n{answer}\n\"\"\"\n\n"
        'JSON şeması: {"questions":["...","...","..."],"noncommittal":0}'
    )
    data = judge.ask_json(prompt)
    if isinstance(data, dict) and _verdict(data.get("noncommittal", 0)) == 1:
        return 0.0
    gen = (data.get("questions") if isinstance(data, dict) else data) or []
    gen = [q for q in gen if isinstance(q, str) and q.strip()]
    if not gen:
        return 0.0
    vecs = embedder.embed([question] + gen)
    qv, gvs = vecs[0], vecs[1:]
    sims = [_cosine(qv, g) for g in gvs]
    return max(0.0, sum(sims) / len(sims))


def median(values: list[float]) -> float:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return 0.0
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2
