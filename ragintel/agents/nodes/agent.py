"""FAZ 4 agent node: tek LLM karar noktası (tool çağır VEYA yanıt teslim et).

- Her turda `retrieved`'den context builder ile bağlam blokları üretilir ve
  prompt'a konur (validate'in evreni = bu saklanan bağlam — İP-3.4 köprüsü).
- `user_ctx` LLM'e VERİLMEZ (tool şemalarında yok, runtime enjekte edilir).
- Bütçe bitince (iterasyon/token) yalnızca `submit_answer` sunulur → yanıt zorlanır.
- Zaman aşımı: LLM çağrılmaz; router fallback'e yönlendirir.
"""

from __future__ import annotations

import json
import time

from ...config.loader import EffectiveConfig, load_config
from ...observability.tracing import set_span_attributes, start_span
from ...retrieval.types import ContextBuildResult
from ...text import normalize_for_quote
from ..prompts import load_system_prompt
from ..tools import SUBMIT_ANSWER, ToolRegistry, accumulated_chunks  # noqa: F401 (accumulated_chunks tools_node'da)

_FEEDBACK_HEADER = "VALIDATION_FAILED:"


def _normalize_citations(raw) -> list[dict]:
    """LLM'in ürettiği citation listesini kanonik biçime indirger; bozukları atar.
    Gerçek (küçük) modeller str/eksik-alan üretebilir — pipeline crash etmemeli."""
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict) or "chunk_id" not in item:
            continue
        try:
            cid = int(item["chunk_id"])
        except (TypeError, ValueError):
            continue
        out.append({"claim": str(item.get("claim", "")), "chunk_id": cid, "quote": str(item.get("quote", ""))})
    return out


def _resolve_citation_ids(
    citations: list[dict], context: ContextBuildResult | None, retrieved: list[dict]
) -> list[dict]:
    """Citation'ın chunk_id'sini GERÇEK chunk_id'ye çöz.

    Model, bağlamda yalnızca bloğun görünür `[n]` etiketini görür (gerçek chunk_id
    prompt'ta yoktur) → çoğu model `chunk_id` alanına `n` yazar. Bunu bloğun gerçek
    chunk_id'sine eşleriz. Model zaten gerçek chunk_id verdiyse (ör. test mock'u)
    dokunmayız. Çok-chunk'lı blokta quote'u BİREBİR içeren chunk seçilir; yoksa ilk
    chunk (validate yine de fabricated_quote ile eler)."""
    if not context or not context.get("blocks"):
        return citations
    blocks = context["blocks"]
    real_ids = {int(cid) for b in blocks for cid in b["chunk_ids"]}
    index_to_block = {int(b["n"]): b for b in blocks}
    text_by_id = {int(c["chunk_id"]): str(c.get("text", "")) for c in retrieved}
    resolved: list[dict] = []
    for c in citations:
        cid = int(c["chunk_id"])
        if cid in real_ids:  # zaten gerçek chunk_id → dokunma
            resolved.append(c)
            continue
        block = index_to_block.get(cid)
        if block is None:  # ne gerçek id ne geçerli [n] → bırak, validate eler
            resolved.append(c)
            continue
        chosen = int(block["chunk_ids"][0])
        qn = normalize_for_quote(str(c.get("quote", "")))
        if qn:
            for bid in block["chunk_ids"]:
                if qn in normalize_for_quote(text_by_id.get(int(bid), "")):
                    chosen = int(bid)
                    break
        resolved.append({**c, "chunk_id": chosen})
    return resolved


def _render_context(context: ContextBuildResult | None) -> str:
    if not context or not context["blocks"]:
        return "(henüz bağlam yok — önce arama yapın)"
    lines = []
    for block in context["blocks"]:
        flag = " [DÜŞÜK KALİTE]" if block.get("low_quality") else ""
        lines.append(f"{block['label']}{flag}\n{block['text']}")
    return "\n\n".join(lines)


def _feedback_message(state: dict) -> list[dict]:
    validation = state.get("validation")
    if not validation or validation.get("passed"):
        return []
    issue_list = validation.get("issues", [])
    issues = "\n".join(f"- {issue}" for issue in issue_list)
    hint = (
        "Talimat: Yalnızca sağlanan bağlamdaki bilgiyle yanıtla; desteklenmeyen "
        "iddiaları çıkar veya ek arama yap."
    )
    # M-9: low_coverage EN SIK arıza ama hedefli talimatı YOKTU — model `low_coverage:0.667`
    # makine dizgesinden ne yapacağını çıkaramaz ve üstteki genel talimat onu yanlış yöne
    # ("iddia çıkar / arama yap") iter. Oysa gereken tek şey: HER CÜMLEYİ alıntıya bağla.
    if any(str(i).startswith("low_coverage") for i in issue_list):
        hint += (
            " Kapsama = geçerli alıntıya bağlanan CÜMLE sayısı / toplam cümle. Cevabındaki"
            " bazı cümleler alıntısız kaldığı için yanıt reddedildi. Cevabı yeniden yaz:"
            " HER cümle bir alıntıyla desteklensin; destekleyemediğin bağlayıcı/özet"
            " cümleleri TAMAMEN ÇIKAR (kısa ve tümüyle alıntılı bir cevap, uzun ve kısmen"
            " alıntılı olandan iyidir)."
        )
    # FAZ 5 validate v2: entailment issue'larına hedefli düzeltme talimatı.
    if any(str(i).startswith("unsupported_claim") for i in issue_list):
        hint += (" Bir iddiayı YALNIZCA alıntı o iddiayı DOĞRUDAN kanıtlıyorsa öne sür; "
                 "alıntı iddiayı kanıtlamıyorsa o cümleyi ve citation'ı ÇIKAR.")
    if any(str(i).startswith("overconfident_hypothetical") for i in issue_list):
        hint += (" Hipotetik/tahmini/koşullu ya da başka ülke/döneme ait bir bilgiyi KESİN olgu "
                 "gibi sunma; ya 'tahmin/projeksiyon' olduğunu açıkça belirt ya da bu soruyu "
                 "'bulunamadı' diye yanıtla (kaynak GÖSTERME).")
    # M-9: teslimat kanalı ŞART koşulur. Düzeltme turunda model düzyazıyla yanıtlarsa
    # (submit_answer'ı çağırmazsa) agent_node onu "citation'sız taslak" sayar → coverage 0
    # → fallback GARANTİ. Yani düzeltme turu, düzeltmeden ÖNCEKİNDEN kötü sonuç verebilir
    # (ölçüldü: 0.40/5 alıntı → 0.00/0 alıntı). Kural açıkça söylenmeliydi.
    hint += (" Düzeltilmiş cevabı MUTLAKA submit_answer aracıyla, citations alanını doldurarak"
             " gönder; düz metin olarak yazma.")
    content = f"{_FEEDBACK_HEADER}\n{issues}\n{hint}"
    return [{"role": "user", "content": content}]


def _counter_message(state: dict) -> list[dict]:
    """M-15/kol-2 (a): tur sayacını sistem prompt'undan çıkarıp EN SON mesaja taşır.

    NEDEN: `[Kalan iterasyon: N]` sistem mesajının İÇİNDEydi ve her tur değiştiği için ilk
    mesajı her tur farklılaştırıp prefix (KV-cache) yeniden-kullanımını tümüyle kırıyordu.
    Trailing minik bir not olarak taşınınca system + query/context + tur-geçmişi turlar arası
    BYTE-ÖZDEŞ kalır; yalnız en sondaki küçük mesaj değişir (cache onu zaten ucuz değerler).
    Sayaç modele HÂLÂ iletilir → tur-bütçesi işlevi korunur; yalnız konumu sonda.

    SINIR (dürüst): mesaj-2'deki bağlam hâlâ her tur yeniden kurulup numaralanır → asıl
    ~2500 token'lık yükü ancak (b) append-only dondurur. (a) tek başına yalnız sistem mesajını
    cache'lenebilir kılan ÖN KOŞUL'dur, latency ödülü değil; ödül (b)'den sonra ölçülür."""
    budget = state["budget"]
    remaining = int(budget["max_iterations"]) - int(budget["iteration"])
    return [{"role": "user", "content": f"[Kalan iterasyon: {remaining}]"}]


def _assemble_messages(state: dict, cfg: EffectiveConfig, context: ContextBuildResult) -> list[dict]:
    # M-15/kol-2 (a): system BYTE-ÖZDEŞ kalır — sayaç artık burada DEĞİL, en sonda (_counter_message).
    system = load_system_prompt(cfg)
    head = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Soru: {state['query']}\n\nBağlam blokları:\n{_render_context(context)}"},
    ]
    tail = list(state.get("messages") or [])
    # Sıra: head + tur-geçmişi + feedback + [sayaç]. Sayaç GERÇEKTEN en sonda: feedback retry
    # turunda trailing mesaj eklese bile sayaç onun da sonrasına gelir.
    return head + tail + _feedback_message(state) + _counter_message(state)


def agent_node(
    state: dict,
    *,
    gateway,
    registry: ToolRegistry,
    context_builder,
    config: EffectiveConfig | None = None,
) -> dict:
    cfg = config or load_config()
    budget = dict(state["budget"])

    with start_span("agent.step", iteration=int(budget["iteration"])) as span:
        # Zaman aşımı: yeni LLM çağrısı yapma; router deadline'ı görüp fallback'e gider.
        if budget["deadline_ts"] and time.time() > float(budget["deadline_ts"]):
            set_span_attributes(decision="timeout")
            return {"pending_tool_calls": None}

        # Bu tur bir retry mi? (başarısız validation state'te duruyorsa)
        validation = state.get("validation")
        is_retry = bool(validation) and not validation.get("passed", False)

        context = context_builder.build(state.get("retrieved") or [])
        messages = _assemble_messages(state, cfg, context)

        exhausted = (
            int(budget["iteration"]) >= int(budget["max_iterations"])
            or int(budget["tokens_used"]) >= int(budget["max_tokens"])
        )
        tools = registry.final_only_schemas() if exhausted else registry.llm_tool_schemas()

        resp = gateway.complete(messages=messages, tools=tools)
        budget["iteration"] = int(budget["iteration"]) + 1
        budget["tokens_used"] = int(budget["tokens_used"]) + int(resp.total_tokens)
        set_span_attributes(
            is_retry=is_retry,
            forced_final=exhausted,
            tokens_used=int(budget["tokens_used"]),
            tool_calls=",".join(tc.name for tc in resp.tool_calls) or "none",
        )

        update: dict = {
            "budget": budget,
            "context": context,
            "retry_count": int(state.get("retry_count", 0)) + (1 if is_retry else 0),
        }
        # Retry turuna girildi: eski başarısız validation temizlenir ki bu turdaki
        # ek tool çağrıları retry_count'u tekrar artırmasın (retry başına 1 artış).
        if is_retry:
            update["validation"] = None

        submit = next((tc for tc in resp.tool_calls if tc.name == SUBMIT_ANSWER), None)
        if submit is not None:
            set_span_attributes(decision="submit_answer")
            update["draft_answer"] = str(submit.arguments.get("answer") or "")
            update["citations"] = _resolve_citation_ids(
                _normalize_citations(submit.arguments.get("citations")),
                context,
                state.get("retrieved") or [],
            )
            update["pending_tool_calls"] = None
            return update

        if resp.tool_calls:
            set_span_attributes(decision="tool_call")
            update["messages"] = [resp.raw_message]
            update["draft_answer"] = None  # stale taslağı temizle → router tools'a yönlensin
            update["pending_tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in resp.tool_calls
            ]
            return update

        # Ne tool ne submit: içeriği citation'sız taslak say (validate düşük coverage'la eler).
        set_span_attributes(decision="content_no_tool")
        update["draft_answer"] = str(resp.content or "")
        update["citations"] = []
        update["pending_tool_calls"] = None
        return update
