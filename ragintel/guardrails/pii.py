"""FAZ 6 P2 — PII maskeleme (KVKK temel seti). Deterministik regex + doğrulama.

Karar (görev): TCKN (11 hane + checksum) ve TARİH (doğum tarihi deseni). AD-SOYAD bu
katmanda YOK (NER gerektirir — bkz. docs/FAZ6_PII_AdSoyad_Oneri.md). Desenler
`app_config('pii')`'den genişletilebilir (custom_patterns).

Maskeleme deterministik: TCKN yalnızca checksum GEÇERLİYSE maskelenir (false-positive
önlenir — 11 haneli ama TCKN olmayan sayı maskelenmez). Tarih tam-tarih desenidir
(gün+ay+yıl); tek yıl ("1990") maskelenmez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 11 hane, ilk hane 0 değil (aday TCKN; checksum ayrıca doğrulanır).
_TCKN_RE = re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)")

# Tam tarih desenleri (gün+ay+yıl). Tek yıl kasıtlı olarak HARİÇ.
#
# M-3(c) SINIR KORUMASI: nokta ayraçlı sürüm dizeleri (ör. SQL Server "14.0.3456.9")
# eski desende tarih sanılıp "[TARİH].9" olarak maskeleniyordu. İki katman:
#   1) Lookaround: match'in solunda/sağında nokta+rakam varsa (yani daha uzun bir
#      nokta ayraçlı dizinin PARÇASIYSA) eşleşme kurulmaz — "14.0.3456.9", "1.14.0.3456".
#   2) Aralık doğrulaması (_is_plausible_date): gün 1-31, ay 1-12, yıl 1900-2099.
#      "14.0.3456" → ay=0, yıl=3456 → tarih DEĞİL (TCKN'deki checksum mantığının eşi).
_DMY_RE = re.compile(r"(?<![\d.])(\d{1,2})([./])(\d{1,2})\2(\d{4})(?!\d)(?!\.\d)")   # 12.05.1980, 1/1/1990
_ISO_RE = re.compile(r"(?<![\d.])(\d{4})-(\d{2})-(\d{2})(?!\d)(?!\.\d)")             # 1980-05-12
_TCKN_MASK = "[TCKN]"
_DATE_MASK = "[TARİH]"
_YEAR_MIN, _YEAR_MAX = 1900, 2099


def _is_plausible_date(day: int, month: int, year: int) -> bool:
    """Gün/ay/yıl aralık kontrolü. dd.mm.yyyy VEYA mm/dd/yyyy sırasını kabul eder;
    ikisi de olamıyorsa tarih değildir (sürüm/derleme numarası vb.)."""
    if not (_YEAR_MIN <= year <= _YEAR_MAX):
        return False
    dmy = 1 <= day <= 31 and 1 <= month <= 12
    mdy = 1 <= month <= 31 and 1 <= day <= 12
    return dmy or mdy


def is_valid_tckn(value: str) -> bool:
    """TC Kimlik No checksum doğrulaması (11 hane; d10/d11 kuralları)."""
    if len(value) != 11 or not value.isdigit() or value[0] == "0":
        return False
    d = [int(c) for c in value]
    c10 = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    c11 = sum(d[:10]) % 10
    return d[9] == c10 and d[10] == c11


@dataclass
class PiiPolicy:
    enabled: bool = True
    mask_tckn: bool = True
    mask_dates: bool = True
    custom_patterns: tuple[str, ...] = ()   # app_config('pii')'den ek regex'ler


def mask_pii(text: str | None, policy: PiiPolicy | None = None) -> tuple[str, int]:
    """PII'yi maskeler; (maskeli_metin, maskelenen_adet) döndürür. Metin PII içermiyorsa
    değişmeden döner (adet 0)."""
    if not text:
        return text or "", 0
    policy = policy or PiiPolicy()
    if not policy.enabled:
        return text, 0
    count = 0
    out = text

    if policy.mask_tckn:
        def _tckn_sub(m: re.Match) -> str:
            nonlocal count
            if is_valid_tckn(m.group(0)):
                count += 1
                return _TCKN_MASK
            return m.group(0)  # 11 hane ama checksum tutmuyor → maskeleme YOK (FP önlenir)
        out = _TCKN_RE.sub(_tckn_sub, out)

    if policy.mask_dates:
        def _dmy_sub(m: re.Match) -> str:
            nonlocal count
            if not _is_plausible_date(int(m.group(1)), int(m.group(3)), int(m.group(4))):
                return m.group(0)   # sürüm/derleme numarası → maskeleme YOK (FP önlenir)
            count += 1
            return _DATE_MASK
        out = _DMY_RE.sub(_dmy_sub, out)

        def _iso_sub(m: re.Match) -> str:
            nonlocal count
            if not _is_plausible_date(int(m.group(3)), int(m.group(2)), int(m.group(1))):
                return m.group(0)
            count += 1
            return _DATE_MASK
        out = _ISO_RE.sub(_iso_sub, out)

    for pat in policy.custom_patterns:
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        def _custom_sub(m: re.Match) -> str:
            nonlocal count
            count += 1
            return "[PII]"
        out = rx.sub(_custom_sub, out)

    return out, count


def policy_from_config(cfg) -> PiiPolicy:
    """`app_config('pii')` grubundan PiiPolicy kurar (yoksa kod varsayılanı)."""
    try:
        p = cfg.group("pii")
    except Exception:
        return PiiPolicy()
    return PiiPolicy(
        enabled=bool(getattr(p, "enabled", True)),
        mask_tckn=bool(getattr(p, "mask_tckn", True)),
        mask_dates=bool(getattr(p, "mask_dates", True)),
        custom_patterns=tuple(getattr(p, "custom_patterns", ()) or ()),
    )
