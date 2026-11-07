"""Resumable, conservative official Wiktionary cross-check of common candidates.

Run after collect_lexicon.py. Default is offline cache processing. --download
requests at most 50 titles per batch, serially, with maxlag=5 and pauses. It never
claims to be a general wikitext parser or an independently verified etymology.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data/raw/wiktionary_batches"
POOL = ROOT / "data/candidates/untagged_katakana_priority_review_pool.jsonl"
OUT = ROOT / "data/candidates"
RISK = re.compile(r"wasei|pseudo[- ]loan|pseudo[- ]anglic|coinage|coined|clipp(?:ing|ed)|abbreviat|shorten(?:ing|ed)|\bblend\b|\bperhaps\b|\bpossibly\b|\bprobably\b|\buncertain\b|\bdisputed\b|\beither\b|\bcalque\b", re.I)


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def api_url(titles):
    return "https://en.wiktionary.org/w/api.php?" + urlencode({"action": "query", "titles": "|".join(titles), "prop": "revisions", "rvprop": "ids|timestamp|content", "rvslots": "main", "format": "json", "formatversion": 2, "maxlag": 5})


def fetch(titles):
    url = api_url(titles)
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "KanaMemory-research/0.1 (noncommercial local etymology audit; manual interactive run)", "Accept": "application/json"})
            with urlopen(request, timeout=45) as response:
                data = response.read()
            parsed = json.loads(data)
            if "error" in parsed:
                if parsed["error"].get("code") in {"maxlag", "ratelimited"} and attempt < 2:
                    time.sleep(5 * 2 ** attempt)
                    continue
                raise RuntimeError(str(parsed["error"]))
            return data
        except HTTPError as exc:
            if exc.code in {429, 503} and attempt < 2:
                time.sleep(min(30, max(5, int(exc.headers.get("Retry-After", "5"))) * 2 ** attempt))
                continue
            raise
    raise RuntimeError("Retry limit reached")


def classify(page, matched_rows, requested_title):
    title = page.get("title", requested_title)
    base = {"headword": title, "requested_headword": requested_title,
        "jmdict_entry_ids": sorted({r["entry_id"] for r in matched_rows}),
        "priority_tags": sorted({p for r in matched_rows for p in r["priority"]}),
        "jmdict_has_common_priority_tag": True,
        "frequency_claim": "dictionary_priority_proxy_not_modern_frequency_top_n",
        "source_ids": [], "license": "CC-BY-SA-4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": "English Wiktionary contributors; see the linked article history",
        "source_url": "https://en.wiktionary.org/wiki/" + quote(title),
        "review_status": "not_human_reviewed", "source_expression": None}
    if not page.get("revisions"):
        return dict(base, classification="missing_page_or_revision")
    revision = page["revisions"][0]
    base.update({"wiktionary_page_id": page["pageid"], "wiktionary_revision_id": revision["revid"],
        "revision_timestamp": revision["timestamp"], "source_ids": [f"enwiktionary:page:{page['pageid']}:revision:{revision['revid']}"],
        "permalink": f"https://en.wiktionary.org/w/index.php?oldid={revision['revid']}",
        "attribution_history_url": "https://en.wiktionary.org/w/index.php?title=" + quote(title) + "&action=history"})
    text = revision["slots"]["main"]["content"]
    languages = re.split(r"^==\s*Japanese\s*==\s*$", text, flags=re.M)
    if len(languages) < 2:
        return dict(base, classification="no_japanese_section_or_redirect")
    japanese = re.split(r"^==[^=]", languages[1], flags=re.M)[0]
    etymologies = re.findall(r"^===\s*Etymology[^=]*===\s*\n(.*?)(?=^===|\Z)", japanese, flags=re.M | re.S)
    evidence = []
    for number, section in enumerate(etymologies, 1):
        for match in re.finditer(r"\{\{(?:bor\+?|borrowed)\|([^{}]+)\}\}", section):
            positional = [part.strip() for part in match.group(1).split("|") if "=" not in part]
            if len(positional) >= 3 and positional[0] == "ja":
                evidence.append({"etymology_number": number, "donor_language": positional[1], "source_expression": positional[2], "evidence_template": match.group(0)})
    base["direct_borrowing_evidence"] = evidence
    base["etymology_section_count"] = len(etymologies)
    if len(etymologies) != 1:
        return dict(base, classification="multiple_or_missing_etymologies_needs_sense_mapping")
    risks = sorted({m.group(0).lower() for m in RISK.finditer(etymologies[0])})
    if risks:
        return dict(base, classification="wasei_abbreviation_uncertain_or_derived_review", review_triggers=risks)
    if not evidence:
        return dict(base, classification="no_supported_explicit_borrowing_template")
    if any(x["donor_language"] not in {"en", "en-US", "en-GB"} for x in evidence):
        return dict(base, classification="nonenglish_or_multiple_donor_languages")
    expressions = sorted({x["source_expression"] for x in evidence})
    if len(expressions) != 1 or not expressions[0] or expressions[0] == "-":
        return dict(base, classification="multiple_or_missing_english_expressions")
    if len(base["jmdict_entry_ids"]) != 1:
        return dict(base, classification="multiple_jmdict_entries_needs_sense_mapping")
    return dict(base, classification="explicit_english_borrowing_candidate", source_expression=expressions[0],
        source_language="eng", etymology_pathway="English -> Japanese (Wiktionary direct borrowing template)",
        english_lemma_candidate=expressions[0], english_original_definition=None,
        notes=["Candidate only: independently review Wiktionary's cited references and align Japanese senses before release.", "English source expression is separate from JMdict's English gloss of the Japanese meaning.", "The original English word can itself have older non-English origins; the recorded donor here is the immediate English source."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--pause", type=float, default=1.2)
    parser.add_argument("--limit", type=int, default=3720)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 50:
        parser.error("batch-size must be between 1 and 50")
    if args.pause < 1:
        parser.error("pause must be at least one second")
    rows = [json.loads(x) for x in POOL.read_text(encoding="utf-8").splitlines()]
    by_title = {}
    skipped = 0
    for row in rows:
        title = row["display_reading"]
        if not re.fullmatch(r"[ァ-ヺー・]+", title):
            skipped += 1
            continue
        by_title.setdefault(title, []).append(row)
    titles = list(by_title)[:args.limit]
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest_path = CACHE / "request_manifest.json"
    # Per-batch content-addressed filenames allow changed limits to reuse cache.
    batches = [titles[n:n + args.batch_size] for n in range(0, len(titles), args.batch_size)]
    manifest = {"input_pool_sha256": hashlib.sha256(POOL.read_bytes()).hexdigest(), "input_rows": len(rows), "selected_titles": len(titles), "mixed_script_rows_not_queried": skipped, "batch_size": args.batch_size, "api_policy": "serial requests, cached raw revisions, maxlag=5, minimum pause=1s, bounded retry/backoff", "batches": []}
    collected, seen, failures = [], set(), []
    for number, batch in enumerate(batches, 1):
        key = hashlib.sha256("\n".join(batch).encode()).hexdigest()[:16]
        path = CACHE / f"batch_{key}.json"
        from_cache = path.exists()
        if not from_cache and args.download:
            try:
                raw = fetch(batch)
                path.write_bytes(raw)
            except Exception as exc:
                failures.append({"batch": number, "error": str(exc), "titles": batch})
                print(f"Batch {number}/{len(batches)} stopped: {exc}", flush=True)
                break
            time.sleep(args.pause)
        if not path.exists():
            continue
        raw = path.read_bytes()
        response = json.loads(raw)
        mapping = {x["from"]: x["to"] for x in response.get("query", {}).get("normalized", [])}
        pages = {p["title"]: p for p in response.get("query", {}).get("pages", [])}
        for original in batch:
            title = mapping.get(original, original)
            if original in seen:
                continue
            seen.add(original)
            page = pages.get(title, {"title": title})
            collected.append(classify(page, by_title[original], original))
        manifest["batches"].append({"number": number, "titles": batch, "raw_path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "api_url": api_url(batch)})
        # Checkpoints after every batch: interrupted runs retain useful work.
        positives = [r for r in collected if r["classification"] == "explicit_english_borrowing_candidate"]
        counts = Counter(r["classification"] for r in collected)
        for filename, records in (("english_borrowing_crosscheck_candidates.jsonl", positives), ("english_borrowing_crosscheck_review_queue.jsonl", [r for r in collected if r["classification"] != "explicit_english_borrowing_candidate"])):
            (OUT / filename).write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records), encoding="utf-8")
        write_json(manifest_path, manifest)
        write_json(ROOT / "research/wiktionary_crosscheck_audit.json", {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "input_pool_rows": len(rows), "titles_selected": len(titles), "titles_processed": len(collected), "batch_count_processed": len(manifest["batches"]), "batch_count_planned": len(batches), "classification_counts": dict(counts), "candidate_count": len(positives), "numeric_1000_target_met": len(positives) >= 1000, "completion_claim": "English-source candidates only; no claim of 1000 reviewed common learning entries or true frequency top1000", "status": "completed_requested_scope" if number == len(batches) else "in_progress_or_resumable", "failures": failures, "input_manifest": "data/raw/wiktionary_batches/request_manifest.json", "source_license": "CC-BY-SA-4.0", "code_does_not_follow_page_instructions": True})
        if number % 5 == 0 or number == len(batches):
            print(f"Batch {number}/{len(batches)}; titles={len(collected)}; explicit English candidates={len(positives)}; cache={from_cache}", flush=True)
    if failures:
        write_json(ROOT / "research/wiktionary_crosscheck_fetch_errors.json", failures)
    print(f"Done: {len(collected)} titles processed; {sum(r['classification'] == 'explicit_english_borrowing_candidate' for r in collected)} strict candidates", flush=True)


if __name__ == "__main__":
    main()
