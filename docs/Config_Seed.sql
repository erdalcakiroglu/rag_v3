-- =====================================================================
-- Config_Seed.sql — davranışsal config'in DB seed'i (config-first, İP-0)
-- OTOMATİK ÜRETİM: python -m ragintel.config export-seed  (elle DÜZENLEME).
-- ELLE UYGULANIR (onay sonrası). ON CONFLICT DO NOTHING → canlı DB'yi EZMEZ.
-- Kaynak: pydantic GROUP_MODELS (SEEDABLE_GROUPS allowlist).
-- Bootstrap/secret (DB/Ollama/TEI/Langfuse/LiteLLM bağlantı+anahtarları) HARİÇ.
-- =====================================================================

INSERT INTO app_config (config_key, config_value, description) VALUES
    ('agent', $cfg${"compose_followup_count": 2, "confidence_high_coverage_threshold": 0.9, "max_iterations": 4, "max_tokens": 16000, "model": "qwen2.5:3b", "system_prompt": "", "timeout_sec": 60, "validation_coverage_threshold": 0.7}$cfg$, 'AgentConfig (pydantic — ragintel.config.settings)'),
    ('chunking', $cfg${"max_tokens": 512, "min_tokens": 30, "overlap_tokens": 64, "strategy": "section"}$cfg$, 'ChunkingConfig (pydantic — ragintel.config.settings)'),
    ('embedding', $cfg${"batch_size": 32, "dim": 1024, "model": "BAAI/bge-m3", "normalize": true}$cfg$, 'EmbeddingConfig (pydantic — ragintel.config.settings)'),
    ('ingestion', $cfg${"allowed_types": ["pdf", "docx", "xlsx", "txt"], "ingest_parallelism": 1, "max_file_mb": 100, "parse_timeout_sec": 300, "stuck_processing_minutes": 30}$cfg$, 'IngestionConfig (pydantic — ragintel.config.settings)'),
    ('injection', $cfg${"base64_min_run": 40, "enabled": true, "hidden_unicode_min_count": 1, "homoglyph_min_count": 2, "patterns": ["ignore\\s+(all\\s+|the\\s+)?(previous|prior|above|earlier|preceding)\\s+(instructions?|prompts?|messages?|context|rules?)", "disregard\\s+(all\\s+|the\\s+|any\\s+)?(previous|prior|above|earlier|preceding)", "forget\\s+(everything|all|(the\\s+)?(previous|prior)\\s+(instructions?|context))", "you\\s+are\\s+now\\s+(a|an|the)?\\s*\\w+", "act\\s+as\\s+(a|an|if)\\b", "pretend\\s+(to\\s+be|you\\s+are)\\b", "system\\s+prompt\\b", "new\\s+instructions?\\s*[:\\-]", "override\\s+(the\\s+)?(system|instructions?|previous)", "do\\s+not\\s+follow\\s+(the\\s+)?(previous|above|system)", "(önceki|yukarıdaki|tüm|bütün)\\s+(talimatlar[ıi]?|komutlar[ıi]?|yönergeleri?|kurallar[ıi]?)\\s*(['’]?[ıi]?)?\\s*(yoksay|görmezden\\s+gel|unut|dikkate\\s+alma|umursama)", "(bundan\\s+sonra|artık)\\s+(sen|şu\\s+şekilde|şöyle)\\b", "rol(ün[üu])?\\s+değiştir", "sistem\\s+(istemi|komutu|talimat[ıi]|mesaj[ıi])", "yeni\\s+talimat(lar)?\\s*[:\\-]", "(şu\\s+şekilde|şöyle|.+?\\s+gibi)\\s+davran", "talimatlar[ıi]\\s+unut"]}$cfg$, 'InjectionConfig (pydantic — ragintel.config.settings)'),
    ('quality', $cfg${"chunk": {"soft_flag_truncated_ratio": 0.3, "target_token_p95": 512}, "clean": {"hard_fail_retention": 0.6, "soft_flag_retention_high": 0.999, "soft_flag_retention_low": 0.8}, "embed": {"anomaly_cosine_high": 0.98, "hard_fail_nan": 0}, "ocr_fallback": {"enabled": true, "max_retry": 1, "trigger_coverage_below": 0.5}, "parse": {"hard_fail_coverage": 0.5, "hard_fail_garbage": 0.2, "soft_flag_coverage": 0.85}, "weights": {"chunk": 0.25, "clean": 0.2, "embed": 0.2, "parse": 0.35}}$cfg$, 'QualityConfig (pydantic — ragintel.config.settings)'),
    ('retrieval', $cfg${"context_low_quality_threshold": 70.0, "context_token_budget": 4000, "context_token_safety_margin": 1.1, "default_top_k": 10, "hybrid_dense_weight": 1.0, "hybrid_fusion": "rrf", "hybrid_rrf_k": 60, "hybrid_sparse_variant": "simple", "hybrid_sparse_weight": 1.0, "lookup_window": 2, "max_top_k": 20, "rerank_backend": "passthrough", "rerank_retries": 1, "rerank_timeout_sec": 5.0, "vector_ef_search": 80}$cfg$, 'RetrievalConfig (pydantic — ragintel.config.settings)')
ON CONFLICT (config_key) DO UPDATE
    SET config_value = excluded.config_value || app_config.config_value;

-- İç-içe grup alt-anahtar backfill (guard'lı; canlı EZİLMEZ, yalnızca eksikler dolar)
UPDATE app_config SET config_value = jsonb_set(config_value, '{chunk,soft_flag_truncated_ratio}', $cfg$0.3$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{chunk}' ? 'soft_flag_truncated_ratio');
UPDATE app_config SET config_value = jsonb_set(config_value, '{chunk,target_token_p95}', $cfg$512$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{chunk}' ? 'target_token_p95');
UPDATE app_config SET config_value = jsonb_set(config_value, '{clean,hard_fail_retention}', $cfg$0.6$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{clean}' ? 'hard_fail_retention');
UPDATE app_config SET config_value = jsonb_set(config_value, '{clean,soft_flag_retention_high}', $cfg$0.999$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{clean}' ? 'soft_flag_retention_high');
UPDATE app_config SET config_value = jsonb_set(config_value, '{clean,soft_flag_retention_low}', $cfg$0.8$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{clean}' ? 'soft_flag_retention_low');
UPDATE app_config SET config_value = jsonb_set(config_value, '{embed,anomaly_cosine_high}', $cfg$0.98$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{embed}' ? 'anomaly_cosine_high');
UPDATE app_config SET config_value = jsonb_set(config_value, '{embed,hard_fail_nan}', $cfg$0$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{embed}' ? 'hard_fail_nan');
UPDATE app_config SET config_value = jsonb_set(config_value, '{ocr_fallback,enabled}', $cfg$true$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{ocr_fallback}' ? 'enabled');
UPDATE app_config SET config_value = jsonb_set(config_value, '{ocr_fallback,max_retry}', $cfg$1$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{ocr_fallback}' ? 'max_retry');
UPDATE app_config SET config_value = jsonb_set(config_value, '{ocr_fallback,trigger_coverage_below}', $cfg$0.5$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{ocr_fallback}' ? 'trigger_coverage_below');
UPDATE app_config SET config_value = jsonb_set(config_value, '{parse,hard_fail_coverage}', $cfg$0.5$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{parse}' ? 'hard_fail_coverage');
UPDATE app_config SET config_value = jsonb_set(config_value, '{parse,hard_fail_garbage}', $cfg$0.2$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{parse}' ? 'hard_fail_garbage');
UPDATE app_config SET config_value = jsonb_set(config_value, '{parse,soft_flag_coverage}', $cfg$0.85$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{parse}' ? 'soft_flag_coverage');
UPDATE app_config SET config_value = jsonb_set(config_value, '{weights,chunk}', $cfg$0.25$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{weights}' ? 'chunk');
UPDATE app_config SET config_value = jsonb_set(config_value, '{weights,clean}', $cfg$0.2$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{weights}' ? 'clean');
UPDATE app_config SET config_value = jsonb_set(config_value, '{weights,embed}', $cfg$0.2$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{weights}' ? 'embed');
UPDATE app_config SET config_value = jsonb_set(config_value, '{weights,parse}', $cfg$0.35$cfg$::jsonb, true)
    WHERE config_key = 'quality' AND NOT (config_value #> '{weights}' ? 'parse');
