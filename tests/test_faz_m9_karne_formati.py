"""M-9 — karne formatı: metrik tuzağına kalıcı çözüm (mimar talimatı, v2.42).

TUZAK: Fallback ("Cevap bulunamadı") TANIMI GEREĞİ sadıktır — hiçbir iddia öne sürmez,
dolayısıyla faithfulness ~1.0 alır; bağlamı da 'isabetli' sayılır. Fallback oranı
yükseldikçe faithfulness/context_precision YÜKSELİR. Yani karne, sistem bozuldukça
daha iyi görünebilir.

ÖLÇÜLDÜ (M-9 temiz karne, agent=qwen3.5:35b):
  fallback veren satırlar : faithfulness 0.937 · answer_relevancy 0.000
  gerçek cevap verenler   : faithfulness 0.985 · answer_relevancy 0.692
  genel ortalama          : faithfulness 0.955 (ŞİŞKİN) — 20/31 fallback olduğu hâlde

KURAL: her karne/gate raporunda metrikler İKİ KIRILIMLA verilir — answered-only
(fallback'ler hariç) + fallback oranı AYRI SATIR. Tek dürüst özet budur.
"""

from __future__ import annotations

from ragintel.eval.harness import _aggregate, format_report, is_fallback


def _satir(qid, cat, fb, faith, rel):
    return {"id": qid, "category": cat, "fallback": fb, "iterations": 3, "confidence": "low",
            "faithfulness": faith, "answer_relevancy": rel,
            "context_precision": 0.9, "context_recall": 0.9}


# --- fallback tespiti ---------------------------------------------------------
def test_fallback_metni_tespit_edilir():
    assert is_fallback({"answer": "Cevap bulunamadı. İncelenen kaynaklar aşağıdadır."})


def test_gercek_cevap_fallback_SAYILMAZ():
    """POZİTİF ÖN-KOŞUL: tespit her şeyi fallback saysaydı answered-only boş kalırdı."""
    assert not is_fallback({"answer": "Karbon vergisini ilk uygulayan ülke Finlandiya'dır [1]."})
    assert not is_fallback({"answer": ""})


# --- iki kırılım --------------------------------------------------------------
def test_answered_only_fallbacklerin_SISIRMESINDEN_arindirilmis():
    """Asıl koruma: fallback'ler ortalamayı yukarı çeker; answered-only bunu dışlar."""
    scored = [
        _satir("q1", "single_fact", True, 0.95, 0.00),   # fallback: sadık ama alakasız
        _satir("q2", "single_fact", True, 0.95, 0.00),
        _satir("q3", "single_fact", False, 0.80, 0.70),  # gerçek cevap
    ]
    agg = _aggregate(scored)

    assert agg["overall"]["faithfulness"] == 0.9      # ŞİŞKİN (fallback'ler dâhil)
    assert agg["answered_only"]["overall"]["faithfulness"] == 0.8   # DÜRÜST
    assert agg["answered_only"]["overall"]["n"] == 1
    # şişme YÖNÜ kanıtlanır: fallback dâhil ortalama, gerçek kaliteden YÜKSEK görünür
    assert agg["overall"]["faithfulness"] > agg["answered_only"]["overall"]["faithfulness"]


def test_fallback_orani_AYRI_SATIR():
    agg = _aggregate([_satir("q1", "single_fact", True, 0.95, 0.0),
                      _satir("q2", "single_fact", False, 0.8, 0.7)])
    assert agg["fallback"] == {"count": 1, "total": 2, "rate": 0.5, "ids": ["q1"]}


def test_fallback_yokken_iki_kirilim_AYNI():
    """Karşı-örnek: fallback yoksa iki kırılım çakışır (format gereksiz gürültü üretmez)."""
    agg = _aggregate([_satir("q1", "single_fact", False, 0.8, 0.7)])
    assert agg["overall"] == agg["answered_only"]["overall"]
    assert agg["fallback"]["rate"] == 0.0


def test_rapor_METNI_iki_kirilimi_de_gosterir():
    """Mühür formatı: sayı JSON'da olsa da rapor METNİNDE görünmezse kimse okumaz."""
    agg = _aggregate([_satir("q1", "single_fact", True, 0.95, 0.0),
                      _satir("q2", "single_fact", False, 0.8, 0.7)])
    result = {
        "judge": "local/dev-mode", "judge_model": "llama3.3:latest", "agent_model": "qwen3.5:35b",
        "golden": "v0.1", "runs": 3, "limit": None, "status": "complete", "elapsed_sec": 1.0,
        "progress": {}, "dataset": {"answerable_run": 2, "unanswerable_run": 0, "errors": []},
        "ragas": {**agg, "per_question": []},
        "honesty": {"pass": 0, "total": 0, "score": "0/0", "per_question": []},
        "iterations": {"mean": 3.0, "max": 3, "distribution": {}, "by_category": {}},
        "targets": {},
    }
    metin = format_report(result)
    assert "FALLBACK ORANI: 1/2" in metin
    assert "ANSWERED-ONLY" in metin
    assert "q1" in metin                    # hangi sorular fallback verdi — teşhis edilebilir
