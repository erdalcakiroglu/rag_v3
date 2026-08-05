"""M-15 kol-1 — prompt v4'ün TEK DEĞİŞKEN olduğunu kilitler.

M-16 dersi: iddia edilen "tek değişken" ölçülebilir olmalı. v4, CANLI sürüme
(`V4_BASE` = v2) göre YALNIZCA bir satır eklenmiş olmalı; başka hiçbir kural
(grounding, quote, reddetme, uzunluk) değişmemeli. Aksi hâlde karnede çıkan fark
hangi değişikliğe ait, söylenemez.

Taban neden v1 değil: üretimde `prompts.agent_system_active='v2'`. v1'den
türetmek v2'nin GROUNDING (KATI) + REDDETME bloklarını da kaldırır — M-16'nın
honesty/fallback kazanımını taşıyan blok. Aşağıdaki testler bunu kilitler.
"""

from __future__ import annotations

from ragintel.agents.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_V2,
    SYSTEM_PROMPT_V4,
    V4_BASE,
    _V4_RULE,
)


def test_v4_is_base_plus_exactly_one_line():
    base = PROMPT_VERSIONS[V4_BASE]
    base_lines = base.splitlines()
    v4_lines = SYSTEM_PROMPT_V4.splitlines()
    assert len(v4_lines) == len(base_lines) + 1, f"v4, {V4_BASE}'den tam 1 satır uzun olmalı"

    added = [ln for ln in v4_lines if ln not in base_lines]
    assert added == [_V4_RULE.rstrip("\n")], f"beklenmeyen ek satır(lar): {added}"

    # Eklenen satır ÇIKARILINCA gövde taban ile BİREBİR aynı olmalı (byte-eşitlik).
    assert SYSTEM_PROMPT_V4.replace(_V4_RULE, "", 1) == base


def test_v4_base_is_the_live_version_not_v1():
    """Taban v2 olmalı; v1'e kayarsa M-16'nın grounding/reddetme blokları düşer."""
    assert V4_BASE == "v2"
    assert PROMPT_VERSIONS[V4_BASE] is SYSTEM_PROMPT_V2
    assert SYSTEM_PROMPT_V4 != DEFAULT_SYSTEM_PROMPT.replace("", "", 1)
    for block in ("GROUNDING (KATI):", "REDDETME:", "HİPOTETİK"):
        assert block in SYSTEM_PROMPT_V4, f"v2 bloğu v4'te kaybolmuş: {block}"
        assert block not in DEFAULT_SYSTEM_PROMPT, f"{block} v1'de de varmış — taban testi anlamsız"


def test_v4_rule_targets_tool_turns_not_final_answer():
    """Kural yalnız TOOL turlarını hedefler; submit_answer/quote kuralları korunur."""
    rule = _V4_RULE.lower()
    assert "tool" in rule
    assert "submit_answer" not in rule          # nihai teslimat kanalına dokunmuyor
    for keep in ("`submit_answer` TOOL'u ile teslim et", "KOPYALA-YAPIŞTIR",
                 "Bu bilgi dokümanlarda bulunamadı"):
        assert keep in SYSTEM_PROMPT_V4, keep


def test_switch_script_constants_match_prompts_module():
    """Anahtar betiği v4'ü CANLI DB gövdesinden türetir (imaj eski olabilir diye kodu
    import etmez). O yüzden kural metni iki yerde yaşıyor — burada kilitlenir, yoksa
    DB'ye yazılan v4 ile git'teki v4 sessizce ayrışır."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "m15_prompt_v4_switch.py"
    spec = importlib.util.spec_from_file_location("m15_switch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from ragintel.agents.prompts import _V4_ANCHOR

    assert mod.V4_RULE == _V4_RULE
    assert mod.V4_ANCHOR == _V4_ANCHOR
    assert mod.EXPECTED_BASE == V4_BASE
    # Betiğin türetmesi, kodun türetmesiyle birebir aynı sonucu vermeli.
    assert mod.derive_v4(PROMPT_VERSIONS[V4_BASE]) == SYSTEM_PROMPT_V4


def test_v4_registered_and_others_untouched():
    assert PROMPT_VERSIONS["v4"] is SYSTEM_PROMPT_V4
    assert PROMPT_VERSIONS["v1"] is DEFAULT_SYSTEM_PROMPT      # v1 gövdesi değişmedi
    assert PROMPT_VERSIONS["v2"] is SYSTEM_PROMPT_V2           # canlı gövde değişmedi
    assert set(PROMPT_VERSIONS) == {"v1", "v2", "v3", "v4", "v5"}  # M-18: v5 eklendi
