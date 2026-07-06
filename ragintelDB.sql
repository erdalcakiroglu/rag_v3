-- Tum dosyalarin ve chunk'larin silinmesi icin kullanilir.
-- psql "host=192.168.36.15 dbname=ragintel user=ragintel_app"
BEGIN;
TRUNCATE ragintel.core_files CASCADE;
COMMIT;

-- Doğrulama: hepsi 0 dönmeli
SELECT (SELECT count(*) FROM ragintel.core_files)   AS files,
       (SELECT count(*) FROM ragintel.core_chunks)  AS chunks,
       (SELECT count(*) FROM ragintel.core_vectors) AS vectors,
       (SELECT count(*) FROM ragintel.qc_findings)  AS qc,
       (SELECT count(*) FROM ragintel.metrics_ingestion) AS metrics;

       -------------
-- Dosya durumlarını kontrol etmek için kullanılabilir.
SELECT file_id, file_name, status, source_path
FROM ragintel.core_files
WHERE status IN ('PROCESSING', 'PENDING')
ORDER BY file_id;



-- UPDATE ragintel.core_files
-- SET status = 'PENDING'
-- WHERE file_id IN (2961, 2962);

--- Dosya işleme pipeliemım başlatılması için kullanılabilir.
ragintel ingest scan .\raw_files
ragintel ingest run
ragintel ingest retry
ragintel ingest run --limit 10   --- 10 dosya işlenir. 10'dan fazla dosya varsa, kalanlar bir sonraki çalıştırmada işlenir.
python -m ragintel.report ingestion


------------------
select * from ragintel.v_config_flat  -- parametreleri gorelim.

