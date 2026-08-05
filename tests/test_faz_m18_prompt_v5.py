"""M-18 — prompt v5'in TEK DEĞİŞKEN olduğunu kilitler (yapı/madde nudge).

v4 dersi: iddia edilen "tek değişken" ölçülebilir olmalı. v4 bir satır EKLİYORDU
(base + 1). v5 ise CANLI sürümün (v2) YALNIZCA uzunluk/biçim satırını (`_V5_TARGET`)
DEĞİŞTİRİR (`_V5_RULE` ile). Başka hiçbir kural (grounding, quote, reddetme, tool
teslimatı) değişmez → round-trip byte-eşitlik ile kilitlenir.

Taban neden v1 değil: üretimde `agent_system_active` v2 ailesinde. v1'den türetmek
v2'nin GROUNDING (KATI) + REDDETME bloklarını düşürürdü (M-16 honesty kazanımı).

DAVRANIŞ değişikliği: bu test SADECE "tek değişken + grounding-güvenlik metni korundu"
zeminini kilitler. GERÇEK kabul kararı k=3 karne + honesty HARD gate'tedir
(bkz. docs/Brief_M18_Prompt_Yapi_Nudge.md §3-§4) — kod testi onu İKAME ETMEZ.
"""

from __future__ import annotations

from ragintel.agents.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_V2,
    SYSTEM_PROMPT_V4,
    SYSTEM_PROMPT_V5,
    V5_BASE,
    _V5_RULE,
    _V5_TARGET,
)


def test_v5_is_base_with_exactly_one_line_replaced():
    """v5 = v2, YALNIZ tek satır değişmiş: satır SAYISI korunur, tam 1 satır farklı."""
    base = PROMPT_VERSIONS[V5_BASE]
    base_lines = base.splitlines()
    v5_lines = SYSTEM_PROMPT_V5.splitlines()

    # REPLACE mekaniği (INSERT değil) → satır sayısı DEĞİŞMEZ.
    assert len(v5_lines) == len(base_lines), "v5 satır sayısı v2 ile aynı olmalı (replace, insert değil)"

    diff = [(b, v) for b, v in zip(base_lines, v5_lines) if b != v]
    assert len(diff) == 1, f"tam 1 satır değişmeli; değişenler: {diff}"
    only_base, only_v5 = diff[0]
    assert only_base == _V5_TARGET, f"değişen v2 satırı beklenmeyen: {only_base!r}"
    assert only_v5 == _V5_RULE, f"değişen v5 satırı beklenmeyen: {only_v5!r}"


def test_v5_round_trip_is_byte_identical_to_v2():
    """_V5_RULE geri _V5_TARGET yapılınca gövde v2 ile BİREBİR aynı (tek-değişken kanıtı)."""
    assert SYSTEM_PROMPT_V5.replace(_V5_RULE, _V5_TARGET, 1) == SYSTEM_PROMPT_V2


def test_v5_target_actually_present_and_replaced():
    """Sessiz no-op koruması: hedef v2'de VARDI, v5'te YOK; kural v5'te VAR."""
    assert _V5_TARGET in SYSTEM_PROMPT_V2, "hedef satır v2'de bulunamadı → replace no-op olurdu"
    assert _V5_TARGET not in SYSTEM_PROMPT_V5, "eski 'düz paragraf/madde imi KULLANMA' satırı hâlâ v5'te"
    assert _V5_RULE in SYSTEM_PROMPT_V5
    assert SYSTEM_PROMPT_V5 != SYSTEM_PROMPT_V2, "v5 v2'den farklı olmalı (davranış değişikliği)"


def test_v5_base_is_v2_and_grounding_blocks_preserved():
    """Taban v2; v2'nin honesty/reddetme blokları v5'te AYNEN durur (v1'e kayma yok)."""
    assert V5_BASE == "v2"
    assert PROMPT_VERSIONS[V5_BASE] is SYSTEM_PROMPT_V2
    for block in ("GROUNDING (KATI):", "REDDETME:", "HİPOTETİK"):
        assert block in SYSTEM_PROMPT_V5, f"v2 bloğu v5'te kaybolmuş: {block}"
        assert block not in DEFAULT_SYSTEM_PROMPT, f"{block} v1'de de varmış — taban testi anlamsız"


def test_v5_rule_preserves_grounding_safety():
    """Nudge yapıyı AÇIYOR ama grounding-güvenliğini metinde KORUYOR:
    her madde citation-bağlı; başlık/liste-girişi/dolgu YASAK; teslimat/quote/decline değişmedi."""
    rule = _V5_RULE
    # yapı açılıyor
    assert "madde im" in rule and "paragraf" in rule
    # ama alıntısız yapısal metin hâlâ yasak (coverage/honesty koruması)
    assert "ALINTISIZ" in rule
    assert "citation'a bağlı" in rule
    for banned in ("başlık", "liste-girişi", "dolgu"):
        assert banned in rule, f"yasak-listesi eksik: {banned}"
    # teslimat/quote/decline kanalları v5 gövdesinde korunur
    for keep in ("`submit_answer` TOOL'u ile teslim et", "KOPYALA-YAPIŞTIR",
                 "Bu bilgi dokümanlarda bulunamadı"):
        assert keep in SYSTEM_PROMPT_V5, keep


def test_switch_script_constants_match_prompts_module():
    """Anahtar betiği v5'i CANLI DB gövdesinden türetir (imaj eski olabilir diye kodu
    import etmez). Kural metni iki yerde yaşıyor — burada kilitlenir, yoksa DB'ye yazılan
    v5 ile git'teki v5 sessizce ayrışır (v4 deseni)."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "m18_prompt_v5_switch.py"
    spec = importlib.util.spec_from_file_location("m18_switch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.V5_TARGET == _V5_TARGET
    assert mod.V5_RULE == _V5_RULE
    assert mod.EXPECTED_BASE == V5_BASE
    # Betiğin türetmesi, kodun türetmesiyle birebir aynı sonucu vermeli.
    assert mod.derive_v5(PROMPT_VERSIONS[V5_BASE]) == SYSTEM_PROMPT_V5


def test_v5_registered_and_others_untouched():
    assert PROMPT_VERSIONS["v5"] is SYSTEM_PROMPT_V5
    assert PROMPT_VERSIONS["v2"] is SYSTEM_PROMPT_V2          # canlı gövde değişmedi
    assert PROMPT_VERSIONS["v4"] is SYSTEM_PROMPT_V4          # v4 gövdesi değişmedi
    assert set(PROMPT_VERSIONS) == {"v1", "v2", "v3", "v4", "v5"}
