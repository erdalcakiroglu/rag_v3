"""İP-2.1a şema doğrulaması: kategori enum'u, answerable kuralları, duplicate soru."""

from __future__ import annotations

import pytest

from ragintel.eval import GoldenSetValidationError, load_golden_jsonl


def test_valid_sample_fixture_parses():
    records = load_golden_jsonl("eval/golden/v1.sample.jsonl")
    assert len(records) == 5
    assert records[0].category.value == "single_fact"
    assert records[4].answerable is False
    assert records[4].gold_evidence == []


def test_duplicate_question_rejected(tmp_path):
    p = tmp_path / "dup.jsonl"
    p.write_text(
        "\n".join(
            [
                '{"id":"a","question":"Ayni soru","ideal_answer":"x","category":"single_fact",'
                '"difficulty":1,"gold_evidence":[{"file_name":"a.pdf","page":1,"quote":"x"}],'
                '"doc_scope":"s","answerable":true,"created_by":"t","notes":""}',
                '{"id":"b","question":"  ayni   SORU  ","ideal_answer":"y","category":"single_fact",'
                '"difficulty":1,"gold_evidence":[{"file_name":"b.pdf","page":1,"quote":"y"}],'
                '"doc_scope":"s","answerable":true,"created_by":"t","notes":""}',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(GoldenSetValidationError, match="duplicate question"):
        load_golden_jsonl(p)


def test_invalid_answerable_contract_rejected(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text(
        '{"id":"x","question":"Bos kanitli cevap","ideal_answer":"x","category":"single_fact",'
        '"difficulty":1,"gold_evidence":[],"doc_scope":"s","answerable":true,'
        '"created_by":"t","notes":""}',
        encoding="utf-8",
    )

    with pytest.raises(GoldenSetValidationError, match="answerable kayıt en az bir gold_evidence"):
        load_golden_jsonl(p)
