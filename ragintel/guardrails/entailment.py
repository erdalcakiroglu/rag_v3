"""FAZ 5 — validate v2: toplu entailment guardrail (Tasarım §4-v2).

v1 deterministik kontroller (grounding.py) quote'un bağlamda BİREBİR/örtüşmeyle geçtiğini
denetler ama quote'un iddiayı ANLAMSAL olarak DESTEKLEYİP desteklemediğini görmez. Bu modül,
v1 PASS'tan sonra TÜM citation'ları TEK judge çağrısında denetler:
  (a) supported: alıntı iddiayı anlamsal olarak destekliyor mu?
  (b) overconfident_hypothetical: hipotetik/koşullu/tahmini ya da başka bağlam içeriği,
      kesin/güncel bir olgu gibi mi sunulmuş? (gs-v0-034 sınıfı)

GÜVENLİK KATMANI FELSEFESİ: judge erişilemezse FAIL-CLOSED DEĞİL — v1 sonucuyla devam edilir
(`skipped=True`), ama guardrail eksilmesi GÖRÜNÜR olur (span attribute + WARNING log + metrik).
Bu, rerank fail-open'ından farklı bir bilinçli karardır (o kalite; bu güvenlik → görünürlük şart).

DEV-MODE: judge dev'de bulut (deepseek-v4-flash, `deepseek/dev-mode` etiketi). PROD'da lokal
judge ZORUNLU (veri egemenliği — bkz. IP23 runbook). Etiket span'e yazılır.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..config.settings import LiteLLMSettings
from ..llm.gateway import is_retryable, retry_wait_seconds
from ..observability.logging import get_logger

_LOG = get_logger("guardrails.entailment")
_SYS = "You are a strict grounding auditor. Output ONLY valid minified JSON, no prose."


def dev_label(api_base: str) -> str:
    b = (api_base or "").lower()
    provider = "deepseek" if "deepseek" in b else "groq" if "groq" in b else "local" if ("11434" in b or "localhost" in b) else "cloud"
    return f"{provider}/dev-mode"


def _coerce01(v) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return 1 if int(v) == 1 else 0
    s = str(v).strip().lower()
    if s in ("1", "yes", "true", "evet", "supported", "destekli", "desteklenir"):
        return 1
    return 0


def _extract_json(text: str):
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        for oc, cc in (("{", "}"), ("[", "]")):
            i, j = t.find(oc), t.rfind(cc)
            if 0 <= i < j:
                try:
                    return json.loads(t[i:j + 1])
                except json.JSONDecodeError:
                    continue
    raise ValueError(f"entailment judge JSON parse edilemedi: {(text or '')[:160]!r}")


@dataclass
class EntailmentResult:
    """`issues`: validate'e eklenecek issue string'leri. `skipped`: judge erişilemedi →
    v1 sonucu korundu (güvenlik katmanı görünür şekilde atlandı)."""

    issues: list[str] = field(default_factory=list)
    skipped: bool = False
    label: str = ""
    detail: list[dict] = field(default_factory=list)


class EntailmentJudge:
    """Config'ten model + LiteLLMSettings bağlantısı (temperature=0). Retry-After'a uyan backoff."""

    def __init__(self, *, model: str | None = None, settings: LiteLLMSettings | None = None, max_retries: int = 2):
        self.settings = settings or LiteLLMSettings()
        self.model = model or self.settings.model or "deepseek-v4-flash"
        self.label = dev_label(self.settings.api_base)
        self.max_retries = max_retries

    def ask_json(self, user: str):
        import time

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
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = litellm.completion(**kwargs)
                return _extract_json(resp.choices[0].message.content)
            except Exception as exc:
                last = exc
                if attempt < self.max_retries and is_retryable(exc):
                    time.sleep(retry_wait_seconds(exc, attempt))
                    continue
                raise
        raise RuntimeError(str(last))


def _build_prompt(question: str, answer: str, pairs: list[dict]) -> str:
    lines = []
    for k, p in enumerate(pairs):
        lines.append(f'[{k}] iddia: "{p["claim"]}" | alıntı: "{p["quote"]}"')
    joined = "\n".join(lines)
    # v2.2 — ASİMETRİK: `supported` GEVŞEK (false-positive kaynağı bu → kısmi/parafraz destek =
    # PASS, şüphede PASS); `hypothetical_as_fact` KATI ve hedge'e mazeret verme (gs-v0-034 sınıfı).
    return (
        f"Soru: {question}\nYanıt: {answer}\n\n"
        "Aşağıdaki her (iddia, alıntı) çifti için değerlendir:\n"
        "(a) supported = 1 (VARSAYILAN — GEVŞEK): iddia, alıntı VE genel bağlam tarafından TAM, "
        "KISMİ ya da PARAFRAZ olarak destekleniyorsa 1. supported = 0 SADECE: alıntı iddiayla AÇIKÇA "
        "ÇELİŞİYORSA ya da iddiadaki temel olgunun HİÇBİR dayanağı yoksa (net uydurma). ŞÜPHEDEYSEN 1.\n"
        "(b) hypothetical_as_fact = 1 (KATI): ALINTININ İÇERİĞİ hipotetik/koşullu/tahmini/projeksiyon "
        "ya da açıkça BAŞKA bir ülke/döneme ait bir DEĞER olmasına rağmen, iddia bu değeri SORUNUN "
        "kesin/güncel cevabı gibi veriyorsa 1. ÖNEMLİ: iddiada 'tahmin/2014 verisi/yaklaşık' gibi bir "
        "ibare bulunması bunu MAZERET yapmaz — alıntının kaynağı hipotetik/başka-bağlam ise ve soru "
        "GÜNCEL/GERÇEK bir olgu istiyorsa yine 1. Alıntının içeriği gerçekten güncel/olgusal ise 0.\n\n"
        f"Çiftler:\n{joined}\n\n"
        'JSON şeması: {"verdicts":[{"i":0,"supported":1,"hypothetical_as_fact":0}]} '
        "(çift sayısıyla aynı, i sırayla 0-indeks)."
    )


def check_entailment(
    *,
    question: str,
    answer: str,
    citations: list[dict],
    judge: EntailmentJudge,
) -> EntailmentResult:
    """v1 PASS sonrası toplu entailment. Judge erişilemezse skipped=True (fail-OPEN)."""
    pairs = [
        {"chunk_id": int(c["chunk_id"]), "claim": str(c.get("claim", "")), "quote": str(c.get("quote", ""))}
        for c in citations
        if isinstance(c, dict) and "chunk_id" in c
    ]
    if not pairs:
        return EntailmentResult(label=judge.label)
    try:
        data = judge.ask_json(_build_prompt(question, answer, pairs))
    except Exception as exc:  # judge erişilemez → FAIL-OPEN (v1 korunur), ama GÖRÜNÜR
        _LOG.warning("entailment_skipped", reason=str(exc)[:160], label=judge.label,
                     citation_count=len(pairs))
        return EntailmentResult(skipped=True, label=judge.label)

    verdicts = data.get("verdicts") if isinstance(data, dict) else data
    verdicts = verdicts or []
    issues: list[str] = []
    detail: list[dict] = []
    for k, p in enumerate(pairs):
        v = verdicts[k] if k < len(verdicts) else {}
        supported = _coerce01(v.get("supported", 1)) if isinstance(v, dict) else 1
        hypo = _coerce01(v.get("hypothetical_as_fact", 0)) if isinstance(v, dict) else 0
        detail.append({"chunk_id": p["chunk_id"], "supported": supported, "hypothetical_as_fact": hypo})
        if not supported:
            issues.append(f"unsupported_claim:{p['chunk_id']}")
        if hypo:
            issues.append(f"overconfident_hypothetical:{p['chunk_id']}")
    return EntailmentResult(issues=issues, label=judge.label, detail=detail)
