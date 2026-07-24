"""M-15 kol-1 — prompt v4'ün TEK DEĞİŞKEN olduğunu kilitler.

M-16 dersi: iddia edilen "tek değişken" ölçülebilir olmalı. v4, v1'e göre
YALNIZCA bir satır eklenmiş olmalı; başka hiçbir kural (grounding, quote,
reddetme, uzunluk) değişmemeli. Aksi hâlde karnede çıkan fark hangi
değişikliğe ait, söylenemez.
"""

from __future__ import annotations

from ragintel.agents.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_V4,
    _V4_RULE,
)


def test_v4_is_v1_plus_exactly_one_line():
    v1_lines = DEFAULT_SYSTEM_PROMPT.splitlines()
    v4_lines = SYSTEM_PROMPT_V4.splitlines()
    assert len(v4_lines) == len(v1_lines) + 1, "v4, v1'den tam 1 satır uzun olmalı"

    added = [ln for ln in v4_lines if ln not in v1_lines]
    assert added == [_V4_RULE.rstrip("\n")], f"beklenmeyen ek satır(lar): {added}"

    # Eklenen satır ÇIKARILINCA gövde v1 ile BİREBİR aynı olmalı (byte-eşitlik).
    assert SYSTEM_PROMPT_V4.replace(_V4_RULE, "", 1) == DEFAULT_SYSTEM_PROMPT


def test_v4_rule_targets_tool_turns_not_final_answer():
    """Kural yalnız TOOL turlarını hedefler; submit_answer/quote kuralları korunur."""
    rule = _V4_RULE.lower()
    assert "tool" in rule
    assert "submit_answer" not in rule          # nihai teslimat kanalına dokunmuyor
    for keep in ("`submit_answer` TOOL'u ile teslim et", "KOPYALA-YAPIŞTIR",
                 "dokumanlarda bulunamadi".replace("dokumanlarda", "dokümanlarda")
                 .replace("bulunamadi", "bulunamadı")):
        assert keep in SYSTEM_PROMPT_V4, keep


def test_v4_registered_and_others_untouched():
    assert PROMPT_VERSIONS["v4"] is SYSTEM_PROMPT_V4
    assert PROMPT_VERSIONS["v1"] is DEFAULT_SYSTEM_PROMPT      # v1 gövdesi değişmedi
    assert set(PROMPT_VERSIONS) == {"v1", "v2", "v3", "v4"}
