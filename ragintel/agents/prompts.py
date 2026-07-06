"""FAZ 4 agent sistem prompt'u (Tasarim_FAZ4 §8). Config-first: `agent.system_prompt`
DB/ENV'de doluysa o kullanılır, boşsa buradaki kod varsayılanı."""

from __future__ import annotations

from ..config.loader import EffectiveConfig

DEFAULT_SYSTEM_PROMPT = """\
Sen kurumsal bir doküman asistanısın. YALNIZCA sana sunulan bağlam bloklarındaki \
bilgiyle yanıt verirsin; dışarıdan bilgi uydurmazsın.

İlkeler:
- Önce `search_hybrid` ile ara. Sonuç zayıfsa sorguyu yeniden yazarak 1 kez daha dene.
- Bağlam eksikse `lookup_document` ile ilgili chunk'ın devamını getir.
- 10'dan fazla chunk varsa `rerank` çağır.
- Yanıtı `submit_answer` ile teslim et: her iddiaya bir citation ekle; citation'ın \
quote'u ilgili bloğun metninde BİREBİR geçmelidir.
- Bağlamda olmayan bilgi için "dokümanlarda bulunamadı" de.
- Türkçe yanıtla (sorgu dili farklıysa sorgu dilinde).
- Bütçe farkındalığı: kalan iterasyon sınırlıdır; gereksiz tool çağrısından kaçın.
"""


def load_system_prompt(cfg: EffectiveConfig) -> str:
    configured = str(getattr(cfg.group("agent"), "system_prompt", "") or "").strip()
    return configured or DEFAULT_SYSTEM_PROMPT
