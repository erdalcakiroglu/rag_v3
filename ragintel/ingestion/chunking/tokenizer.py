"""Token sayımı — BGE-M3'ün KENDİ tokenizer'ı (İP-5 kuralı: tiktoken YASAK).

`TokenCounter` protokolü char-span'li token sayımı sağlar (chunk sınırlarını
karaktere geri eşlemek için). Gerçek sayaç `BGEM3TokenCounter` (AutoTokenizer,
"BAAI/bge-m3", fast/offset_mapping). Testler enjekte edilebilir bir sahte sayaç
kullanarak hızlı/offline kalır; gerçek tokenizer `slow` test ile doğrulanır.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

# M-10/0: tokenizer'ın YÜKLENECEĞİ yerel dizin (konteynerde build'de gömülür).
#
# NEDEN ENV — config/GROUP_MODELS DEĞİL: bu bir DAĞITIM YOLU, davranış ayarı değil.
# DB-otoriter olsaydı tek bir değer hem konteynere (/opt/models/...) hem geliştirici
# makinesine (öyle bir dizin yok) dayatılırdı; biri kesin kırılırdı. Config-first
# kuralı davranışsal ayarlar içindir — yol, makinenin bilgisidir.
TOKENIZER_DIR_ENV = "RAGINTEL_TOKENIZER_DIR"


def tokenizer_kaynagi(model_name: str) -> tuple[str, bool]:
    """(yükleme_kaynağı, local_files_only) döndürür. KİMLİK (`model_name`) DEĞİŞMEZ.

    ÖLÇÜLDÜ (transformers 4.57.3, lokal + H200 build):
      * `from_pretrained("BAAI/bge-m3")` — HF cache DOLU ve HF_HUB_OFFLINE=1 olsa
        BİLE `model_info()` çağırır → `OfflineModeIsEnabled` → kapalı ağda ölür.
      * `from_pretrained("BAAI/bge-m3", local_files_only=True)` — AYNI hata; bu
        bayrak repo-id yolunu KURTARMIYOR.
      * `from_pretrained("<yerel dizin>", local_files_only=True)` — ÇALIŞIYOR.

    KÖK NEDEN (H200 traceback'inden, tahmin değil):
        tokenization_utils_base._patch_mistral_regex:
            if _is_local or is_base_mistral(pretrained_model_name_or_path):
                                   └─> model_info(model_id)  → ağa çıkar
    `or` KISA DEVRE yapar: kaynak yerel bir DİZİNSE `_is_local=True` olur ve
    `is_base_mistral` HİÇ çağrılmaz. Repo-id verildiğinde `_is_local=False` →
    model_info → offline'da patlar. Yani arıza sürüm değil ÇAĞRI BİÇİMİ
    kaynaklıdır; yerel dizin onu sürümden bağımsız keser. (4.57.1'e düşürmek de
    semptomu gizler — ama 4.57.3+ geri geldiğinde arıza geri gelir.)

    Yani tek geçerli offline yol, repo-id değil YEREL DİZİN vermektir.

    `model_name` yalnızca KİMLİKTİR ve bu fonksiyon onu değiştirmez: embedding
    damgası (`bge-m3@ollama`, bkz. embedder.model_stamp) aynı `embedding.model`
    otoritesinden türer. Yükleme yolunu oraya yazmak damgayı kaydırır ve
    korpustaki vektörleri geçersizleştirirdi — bu yüzden kanal AYRI.
    """
    dizin = os.environ.get(TOKENIZER_DIR_ENV, "").strip()
    if not dizin:
        return model_name, False
    if not os.path.isdir(dizin):
        # Sessizce repo-id'ye DÜŞMÜYORUZ: kapalı ağda o yol anlaşılmaz bir
        # OfflineModeIsEnabled verir ve arıza tokenizer'da sanılır. Asıl arıza
        # imajın bozuk olmasıdır — onu söyle.
        raise RuntimeError(
            f"{TOKENIZER_DIR_ENV}={dizin!r} ayarlı ama dizin YOK. İmaj bozuk: "
            f"builder'daki `save_pretrained` veya runtime'daki `COPY --from=builder` "
            f"adımını kontrol edin."
        )
    return dizin, True


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...
    def token_spans(self, text: str) -> list[tuple[int, int]]: ...


class BGEM3TokenCounter:
    """BGE-M3 HF tokenizer ile token sayımı (embedding modeliyle uyumlu)."""

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self.model_name = model_name    # KİMLİK — yükleme kaynağı ayrı (bkz. tokenizer_kaynagi)
        self._tok = None

    @property
    def tok(self):
        if self._tok is None:
            from transformers import AutoTokenizer
            kaynak, yerel = tokenizer_kaynagi(self.model_name)
            # use_fast: offset_mapping (token_spans) YALNIZCA fast tokenizer'da var.
            self._tok = AutoTokenizer.from_pretrained(
                kaynak, use_fast=True, local_files_only=yerel)
        return self._tok

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        if not text:
            return []
        enc = self.tok(text, add_special_tokens=False, return_offsets_mapping=True)
        return [(int(s), int(e)) for s, e in enc["offset_mapping"]]

    def count(self, text: str) -> int:
        return len(self.token_spans(text))


_WORD = re.compile(r"\S+")


class WordTokenCounter:
    """Deterministik, offline sahte sayaç (boşlukla ayrılmış her sözcük = 1 token).

    Yalnızca test/geliştirme; üretimde BGEM3TokenCounter kullanılır.
    """

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        return [(m.start(), m.end()) for m in _WORD.finditer(text)]

    def count(self, text: str) -> int:
        return len(self.token_spans(text))
