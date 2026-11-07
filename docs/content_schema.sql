-- Design schema v1. Research staging data is not automatically release-ready content.
PRAGMA foreign_keys = ON;

CREATE TABLE source (
  source_id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
  version_or_revision TEXT, retrieved_at TEXT NOT NULL,
  license_id TEXT, attribution TEXT, sha256 TEXT,
  redistribution_status TEXT NOT NULL CHECK (redistribution_status IN ('allowed','conditional','research_only','unknown'))
);
CREATE TABLE kana (
  kana_id TEXT PRIMARY KEY, category TEXT NOT NULL, curriculum_tier TEXT NOT NULL,
  hira TEXT, kata TEXT, romanization_json TEXT NOT NULL CHECK(json_valid(romanization_json)),
  context_rules_json TEXT NOT NULL CHECK(json_valid(context_rules_json)),
  standalone_audio_eligible INTEGER NOT NULL CHECK(standalone_audio_eligible IN(0,1)),
  review_status TEXT NOT NULL
);
CREATE TABLE kana_component (
  kana_id TEXT NOT NULL REFERENCES kana, position INTEGER NOT NULL,
  component_kana_id TEXT NOT NULL REFERENCES kana,
  PRIMARY KEY(kana_id,position)
);
CREATE TABLE graphic_origin (
  origin_id TEXT PRIMARY KEY, kana_id TEXT NOT NULL REFERENCES kana,
  script TEXT NOT NULL CHECK(script IN('hiragana','katakana')),
  source_character TEXT NOT NULL, variant_scope TEXT NOT NULL,
  graphic_process TEXT, period_start INTEGER, period_end INTEGER,
  evidence_status TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES source,
  locator TEXT NOT NULL, uncertainty_note TEXT
);
CREATE TABLE historical_reading (
  historical_reading_id TEXT PRIMARY KEY, origin_id TEXT REFERENCES graphic_origin,
  language TEXT NOT NULL, stage TEXT NOT NULL, place_or_tradition TEXT,
  period_start INTEGER, period_end INTEGER, transcription TEXT NOT NULL,
  notation_system TEXT NOT NULL, claim_type TEXT NOT NULL
    CHECK(claim_type IN('reconstruction','text_attestation','scholarly_interpretation')),
  source_id TEXT NOT NULL REFERENCES source, locator TEXT NOT NULL,
  uncertainty_note TEXT
);
CREATE TABLE locality (
  locality_id TEXT PRIMARY KEY, dialect_group TEXT NOT NULL, place TEXT,
  locality_scope TEXT NOT NULL CHECK(locality_scope IN('survey_point','standard_language','unspecified_dictionary_reference')),
  survey_date TEXT, generation TEXT, source_id TEXT REFERENCES source
);
CREATE TABLE dialect_reading (
  reading_id TEXT PRIMARY KEY, headword TEXT NOT NULL, locality_id TEXT NOT NULL REFERENCES locality,
  reading_context TEXT NOT NULL, transcription TEXT NOT NULL, notation_system TEXT NOT NULL,
  tone_value TEXT, tone_category TEXT, literary_colloquial TEXT,
  source_id TEXT NOT NULL REFERENCES source, locator TEXT NOT NULL, review_status TEXT NOT NULL
);
CREATE TABLE lexeme (
  lexeme_id TEXT PRIMARY KEY, display_form TEXT NOT NULL, display_reading TEXT NOT NULL,
  word_origin_class TEXT NOT NULL CHECK(word_origin_class IN('native','sino_japanese','english_loan','other_loan','wasei_eigo','mixed','unknown')),
  frequency_basis_json TEXT NOT NULL CHECK(json_valid(frequency_basis_json)),
  review_status TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES source
);
CREATE TABLE word_form (
  form_id TEXT PRIMARY KEY, lexeme_id TEXT NOT NULL REFERENCES lexeme,
  spelling TEXT NOT NULL, reading TEXT NOT NULL,
  restrictions_json TEXT NOT NULL CHECK(json_valid(restrictions_json)),
  romanization TEXT, romanization_system TEXT
);
CREATE TABLE sense (
  sense_id TEXT PRIMARY KEY, lexeme_id TEXT NOT NULL REFERENCES lexeme,
  pos_json TEXT NOT NULL CHECK(json_valid(pos_json)),
  glosses_json TEXT NOT NULL CHECK(json_valid(glosses_json)),
  restrictions_json TEXT NOT NULL CHECK(json_valid(restrictions_json)), usage_notes TEXT,
  source_id TEXT NOT NULL REFERENCES source, source_locator TEXT NOT NULL
);
CREATE TABLE source_expression (
  expression_id TEXT PRIMARY KEY, sense_id TEXT NOT NULL REFERENCES sense,
  language TEXT NOT NULL, original_form TEXT NOT NULL, original_pos TEXT, original_definition TEXT,
  borrowing_relation TEXT NOT NULL CHECK(borrowing_relation IN('direct','indirect','abbreviation','wasei','mixed','unknown')),
  source_id TEXT NOT NULL REFERENCES source, locator TEXT NOT NULL, evidence_status TEXT NOT NULL
);
CREATE TABLE mandarin_equivalent (
  equivalent_id TEXT PRIMARY KEY, sense_id TEXT NOT NULL REFERENCES sense,
  form TEXT, pinyin TEXT,
  relation TEXT NOT NULL CHECK(relation IN('attested_transliteration','semantic_translation','mixed_translation','no_common_transliteration','unknown')),
  usage_region TEXT, source_id TEXT REFERENCES source, locator TEXT, review_status TEXT NOT NULL,
  CHECK((relation IN('no_common_transliteration','unknown')) OR form IS NOT NULL)
);
CREATE TABLE example (
  example_id TEXT PRIMARY KEY, text_ja TEXT NOT NULL, reading_ja TEXT,
  chinese_translation TEXT, english_translation TEXT, source_id TEXT NOT NULL REFERENCES source,
  author_attribution TEXT NOT NULL, license_id TEXT NOT NULL, modified INTEGER NOT NULL DEFAULT 0,
  review_status TEXT NOT NULL
);
CREATE TABLE sense_example (
  sense_id TEXT NOT NULL REFERENCES sense, example_id TEXT NOT NULL REFERENCES example,
  indexed_expression TEXT, alignment_status TEXT NOT NULL,
  PRIMARY KEY(sense_id,example_id)
);
CREATE TABLE word_pronunciation (
  pronunciation_id TEXT PRIMARY KEY, form_id TEXT NOT NULL REFERENCES word_form,
  locale TEXT NOT NULL, notation_system TEXT NOT NULL, transcription TEXT,
  accent_system TEXT, accent_pattern TEXT, context TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES source, locator TEXT NOT NULL,
  evidence_status TEXT NOT NULL
);
CREATE TABLE sense_dialect_comparison (
  comparison_id TEXT PRIMARY KEY, sense_id TEXT NOT NULL REFERENCES sense,
  reading_id TEXT NOT NULL REFERENCES dialect_reading,
  relation TEXT NOT NULL CHECK(relation IN('same_historical_lexeme','cognate_character_only','modern_translation','disputed')),
  source_id TEXT NOT NULL REFERENCES source, locator TEXT NOT NULL,
  note TEXT NOT NULL, review_status TEXT NOT NULL
);
CREATE TABLE kana_word_index (
  kana_id TEXT NOT NULL REFERENCES kana, form_id TEXT NOT NULL REFERENCES word_form,
  token_positions_json TEXT NOT NULL CHECK(json_valid(token_positions_json)),
  teaching_rank INTEGER, review_status TEXT NOT NULL, PRIMARY KEY(kana_id,form_id)
);
CREATE TABLE audio_asset (
  audio_id TEXT PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
  locale TEXT NOT NULL, speaker_or_voice TEXT, variant_or_accent TEXT,
  asset_kind TEXT NOT NULL CHECK(asset_kind IN('licensed_recording','device_tts','labeled_reconstruction')),
  path_or_voice_id TEXT, source_id TEXT REFERENCES source, license_id TEXT,
  sha256 TEXT, verified_text TEXT NOT NULL, review_status TEXT NOT NULL
);
CREATE INDEX idx_word_reading ON lexeme(display_reading);
CREATE INDEX idx_dialect_headword ON dialect_reading(headword,locality_id);

