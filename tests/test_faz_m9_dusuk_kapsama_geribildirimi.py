"""M-9 — düzeltme turu, düzeltilecek şeyi MODELE SÖYLEMELİ.

BULUNAN ARIZA (lokal agent qwen3.5:35b, temiz izole karne):
31 cevaplanabilir sorunun 20'sinde "Cevap bulunamadı" (fallback) üretildi — retrieval
KUSURSUZ olduğu hâlde (context_recall 0.95; cevap bağlamda birebir mevcut). Tek arıza
türü `low_coverage` idi: cevap doğru ve alıntılıydı, ama alıntısız kalan bir bağlayıcı/
özet cümle kapsamayı eşiğin (0.70) altına düşürüyordu (ör. 0.667 → RED).

İki eksik, iki düzeltme (EŞİĞE DOKUNULMADI — kalite satışı değil, talimat eksiği):
  1. `low_coverage`, issue'lar içinde EN SIK olanıydı ama hedefli talimatı olmayan
     TEK tür'dü. Model `low_coverage:0.667` makine dizgesinden ne yapacağını çıkaramaz;
     üstelik genel talimat onu YANLIŞ yöne ("iddia çıkar / ek arama yap") iter.
  2. Düzeltme turunda model düz metinle yanıtlarsa agent_node bunu "citation'sız taslak"
     sayar → kapsama 0.0 → fallback GARANTİ. Ölçüldü: bir soruda düzeltme turu
     0.40/5-alıntı'yı 0.00/0-alıntı'ya çevirdi — yani düzeltme, düzeltmeden ÖNCEKİNDEN
     kötü sonuç verdi. Teslimat kanalı (submit_answer) ŞART koşulmalıydı.
"""

from __future__ import annotations

from ragintel.agents.nodes.agent import _feedback_message


def _state(issues: list[str]) -> dict:
    return {"validation": {"passed": False, "coverage": 0.667, "issues": issues}}


def test_low_coverage_HEDEFLI_talimat_alir():
    """Modele kapsamanın TANIMI ve yapılacak iş söylenir: her cümleyi alıntıya bağla."""
    msg = _feedback_message(_state(["low_coverage:0.667"]))[0]["content"]
    assert "low_coverage:0.667" in msg          # ham issue korunur (teşhis)
    assert "CÜMLE" in msg or "cümle" in msg     # kapsamanın tanımı
    assert "HER cümle" in msg                   # yapılacak iş


def test_low_coverage_talimati_YALNIZCA_o_arizada_cikar():
    """POZİTİF ÖN-KOŞUL (boş-geçer test değil): talimat, arıza yokken görünmemeli —
    yoksa 'metin var' iddiası her durumda doğrulanır ve hiçbir şey kanıtlamaz."""
    msg = _feedback_message(_state(["unsupported_claim:3"]))[0]["content"]
    assert "HER cümle" not in msg
    assert "DOĞRUDAN kanıtlıyorsa" in msg       # kendi hedefli talimatı yerinde


def test_teslimat_kanali_HER_duzeltme_turunda_sart_kosulur():
    """Düzeltme turunda düz metin = citation'sız taslak = kapsama 0 = fallback garanti.
    Bu yüzden submit_answer şartı arıza türünden BAĞIMSIZ olarak her geri bildirimde yer alır."""
    for issues in (["low_coverage:0.0"], ["unsupported_claim:3"], ["citation_not_in_context:9"]):
        msg = _feedback_message(_state(issues))[0]["content"]
        assert "submit_answer" in msg, f"{issues} için teslimat kanalı şart koşulmadı"
        assert "düz metin olarak yazma" in msg


def test_dogrulama_gectiyse_geri_bildirim_YOK():
    """Karşı-örnek: başarılı doğrulamada düzeltme mesajı üretilmez (aksi hâlde model,
    doğru cevabı 'yanlış' sanıp bozardı)."""
    assert _feedback_message({"validation": {"passed": True, "coverage": 1.0, "issues": []}}) == []
    assert _feedback_message({}) == []
