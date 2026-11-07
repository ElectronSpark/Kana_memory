"""Reconcile linguistic review evidence without certifying semantic completeness.

Run with the project Python. This reads the pinned baseline, reviewer decisions,
integration records and the actual runtime catalogue; it never edits app data.
The JSON report separates verified ID/anchor coverage from linguistic judgments.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "research/linguistic_audit"
REVIEWERS = ("grammar_a", "grammar_b", "words_pronunciation")
EXPECTED = {"item": 524, "example": 4388, "reference": 5707}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=AUDIT / "final_audit_report.json")
    args = parser.parse_args()
    inputs, checks, warnings = {}, [], []

    def label(path):
        return path.resolve().relative_to(ROOT).as_posix()

    def track(path):
        path = path.resolve()
        data = path.read_bytes()
        entry = {"sha256": sha256(data), "bytes": len(data)}
        previous = inputs.setdefault(label(path), entry)
        if previous != entry:
            raise ValueError("Input changed during this run: " + label(path))
        return data

    def read(relative):
        return json.loads(track(AUDIT / relative).decode("utf-8-sig"))

    def check(name, passed, **details):
        checks.append({"name": name, "passed": bool(passed), **details})

    def validate(name, pack, targets):
        try:
            validate_pack(pack, known_targets=targets)
        except (TypeError, ValueError, KeyError) as exc:
            check(name, False, error=str(exc))
        else:
            check(name, True)

    track(Path(__file__))
    track(ROOT / "tools/build_linguistic_review.py")
    baseline_items = read("baseline_items.json")
    baseline = read("baseline_examples.json")
    ledgers = {name: read(name + "_review.json") for name in REVIEWERS}
    integration = read("integration_report.json")
    helper = read("grammar_b_words_assist_work/review_ledger.json")
    corrections = read("integration_span_corrections.json") if (AUDIT / "integration_span_corrections.json").exists() else []
    review_bundle = json.loads(track(ROOT / "app/src/kanamemory/resources/learning_material_review.json"))
    compiled_copy = track(ROOT / "data/enrichment/learning_material_review.json")
    check("compiled_resource_matches_data_copy",
          sha256(compiled_copy) == inputs["app/src/kanamemory/resources/learning_material_review.json"]["sha256"])

    sys.path.insert(0, str(ROOT / "app/src"))
    from kanamemory.example_catalogue import bundled_example_pack
    from kanamemory.example_store import reference_id_for, validate_pack
    from kanamemory.learning_content import learning_items

    final_items = learning_items()
    final = bundled_example_pack(final_items)
    baseline_sets = {}
    coverage = {}
    for kind, rows in (("item", baseline_items), ("example", baseline["examples"]),
                       ("reference", baseline["references"])):
        identities = [row["id"] for row in rows]
        baseline_sets[kind] = set(identities)
        owners = defaultdict(list)
        duplicates = {}
        for reviewer, ledger in ledgers.items():
            reviewed = ledger["reviewed_" + kind + "_ids"]
            duplicates[reviewer] = sorted(cid for cid, n in Counter(reviewed).items() if n > 1)
            for cid in set(reviewed):
                owners[cid].append(reviewer + "_review.json")
        missing = sorted(set(identities) - owners.keys())
        unexpected = sorted(owners.keys() - set(identities))
        check("baseline_" + kind + "_count_and_unique_ids",
              len(identities) == EXPECTED[kind] == len(set(identities)), actual=len(identities), expected=EXPECTED[kind])
        check("baseline_" + kind + "_review_coverage", not missing and not unexpected,
              uncovered_ids=missing, unexpected_ids=unexpected)
        coverage[kind + "s"] = {
            "baseline_count": len(identities), "reviewed_unique_count": len(owners),
            "uncovered_ids": missing, "unexpected_ids": unexpected,
            "duplicate_ids_inside_ledgers": duplicates,
            "id_to_review_ledgers": {cid: owners[cid] for cid in sorted(set(identities))},
        }
    for name, ledger in ledgers.items():
        item_statuses = ledger["per_item_status"]
        status_ids = set(item_statuses) if isinstance(item_statuses, dict) else {r["item_id"] for r in item_statuses}
        check(name + "_per_item_status_coverage", status_ids == set(ledger["reviewed_item_ids"]))
    validate("baseline_pack_schema_and_anchors", baseline, baseline_items)
    validate("runtime_pack_schema_and_anchors", final, final_items)

    final_ids = [row["id"] for row in final_items]
    check("runtime_learning_identities_preserved",
          len(final_ids) == len(set(final_ids)) and set(final_ids) == baseline_sets["item"])
    final_examples = {row["id"]: row for row in final["examples"]}
    baseline_integration = {}
    for key in ("examples", "references"):
        runtime_index = {row["id"]: row for row in final[key]}
        statuses, unreconciled = Counter(), []
        for original in baseline[key]:
            entry = review_bundle[key].get(original["id"])
            replacement = entry["replacement"] if entry else original
            status = "retired" if replacement is None else "changed" if entry else "unchanged"
            statuses[status] += 1
            if (replacement is None and original["id"] in runtime_index
                    or replacement is not None and runtime_index.get(replacement["id"]) != replacement
                    or entry and fingerprint(original) not in entry.get("input_sha256s", [entry["before_sha256"]])):
                unreconciled.append(original["id"])
        check("baseline_" + key + "_payload_integration", not unreconciled, unreconciled_ids=unreconciled)
        baseline_integration[key] = dict(statuses)
    pairs = Counter((r["example_id"], r["target_id"]) for r in final["references"])
    duplicate_pairs = [{"example_id": e, "target_id": t, "count": n}
                       for (e, t), n in sorted(pairs.items()) if n > 1]
    check("runtime_no_duplicate_example_target_pairs", not duplicate_pairs, duplicates=duplicate_pairs)
    per_item_examples = {cid: set() for cid in final_ids}
    anchor_errors = []
    span_count = 0
    for ref in final["references"]:
        if ref["target_id"] in per_item_examples:
            per_item_examples[ref["target_id"]].add(ref["example_id"])
        example = final_examples.get(ref["example_id"])
        for span in ref["spans"]:
            span_count += 1
            if (example is None or ref["example_revision"] != example["revision"]
                    or type(span["start"]) is not int or type(span["end"]) is not int
                    or not 0 <= span["start"] < span["end"] <= len(example["text"])
                    or example["text"][span["start"]:span["end"]] != span["quote"]):
                anchor_errors.append({"reference_id": ref["id"], "span": span})
    check("runtime_all_unicode_anchors_exact", not anchor_errors, checked_spans=span_count, errors=anchor_errors)
    counts = {cid: len(rows) for cid, rows in sorted(per_item_examples.items())}
    below_minimum = {cid: n for cid, n in counts.items() if n < 10}
    check("runtime_ten_unique_examples_per_item", not below_minimum, below_minimum=below_minimum)
    runtime_counts = {"examples": len(final["examples"]), "references": len(final["references"])}
    check("integration_report_matches_runtime_counts", integration["pack_counts"] == runtime_counts)

    # Canonical finding files are counted once; copies embedded in ledgers are
    # provenance mirrors, not additional discoveries or additional fixes.
    dispositions = integration.get("finding_dispositions", [])
    disposition_map = {row["finding_sha256"]: row for row in dispositions}
    check("integration_disposition_hashes_unique", len(disposition_map) == len(dispositions))
    findings_by_hash = {}
    raw_findings_count = 0
    deferred_hashes = {fingerprint(row) for row in integration.get("deferred_findings", [])}
    clarity_hashes = {fingerprint(row) for row in integration.get("non_error_clarity_findings", [])}
    for reviewer in REVIEWERS:
        path = reviewer + "_findings.json"
        value = read(path)
        rows = value["findings"] if isinstance(value, dict) else value
        raw_findings_count += len(rows)
        for index, finding in enumerate(rows):
            digest = fingerprint(finding)
            record = findings_by_hash.setdefault(digest, {
                "finding_id": finding.get("finding_id") or "finding:" + digest,
                "finding_sha256": digest, "sources": [],
                "kind": finding.get("kind"), "item_id": finding.get("item_id"),
                "example_id": finding.get("example_id"), "reference_id": finding.get("reference_id"),
                "original_confidence": finding.get("confidence"),
                "disposition": disposition_map.get(digest, {}).get("disposition", "unreconciled"),
                "integration_record": disposition_map.get(digest),
                "in_deferred_findings": digest in deferred_hashes,
                "in_non_error_clarity_findings": digest in clarity_hashes,
            })
            record["sources"].append({"file": path, "index": index})
    finding_rows = list(findings_by_hash.values())
    check("each_canonical_finding_has_integration_disposition",
          set(findings_by_hash) == set(disposition_map),
          missing_hashes=sorted(set(findings_by_hash) - set(disposition_map)),
          unknown_hashes=sorted(set(disposition_map) - set(findings_by_hash)))
    disposition_counts = Counter(row["disposition"] for row in finding_rows)
    check("handled_findings_count_reconciled",
          disposition_counts["corrected"] == integration["handled_findings"])
    check("deferred_and_clarity_records_reconciled",
          deferred_hashes == {r["finding_sha256"] for r in finding_rows if r["disposition"] == "deferred"}
          and clarity_hashes == {r["finding_sha256"] for r in finding_rows if r["disposition"] == "adopted_as_clarity_improvement"})

    # Later focused reviews are additional evidence, not another copy of the
    # original 190 findings. Their saved source hashes describe the review-time
    # input; later independent edits to the same source file are legitimate.
    postcheck_paths = {
        "morphology": "grammar_b_words_assist_work/morphology_postcheck.json",
        "masu": "grammar_masu_span_review.json",
        "new_or_rewritten_examples": "integration_examples_work/review_ledger.json",
    }
    postcheck_documents, pending_postchecks = {}, []
    for name, relative in postcheck_paths.items():
        if (AUDIT / relative).exists():
            postcheck_documents[name] = read(relative)
        else:
            pending_postchecks.append(relative)
    integration_snapshot = None
    if "new_or_rewritten_examples" in postcheck_documents:
        relative = "integration_examples_work/sources_snapshot.json"
        if (AUDIT / relative).exists():
            integration_snapshot = read(relative)
        else:
            pending_postchecks.append(relative)
    origin_examples = {row["id"]: row for row in baseline["examples"]}
    origin_examples.update({row["example"]["id"]: row["example"]
                           for row in (integration_snapshot or {}).get("examples", [])})
    validation_examples = {**origin_examples, **final_examples}
    approved_rows, approved_files = [], []
    mapped_example_ids = {eid: entry["replacement"]["id"] for eid, entry in review_bundle["examples"].items()
                          if entry.get("replacement") is not None}
    for path in sorted(AUDIT.glob("*_work/missing_references.json")):
        value = read(path.relative_to(AUDIT))
        rows = value.get("references", []) if isinstance(value, dict) else value
        check(label(path) + ":explicit_approval", all(r.get("review_status") == "approved" for r in rows))
        normalized = []
        for row in rows:
            ref = {k: v for k, v in row.items() if k not in {"review_status", "review_reason"}}
            ref["id"] = reference_id_for(ref)
            normalized.append(ref)
        validate(label(path) + ":source_schema_and_anchors",
                 {**baseline, "examples": list(validation_examples.values()), "references": normalized}, baseline_items)
        absent = [r for r in rows if (mapped_example_ids.get(r["example_id"], r["example_id"]), r["target_id"]) not in pairs]
        check(label(path) + ":approved_pairs_reach_runtime", not absent,
              absent_pairs=[{"example_id": r["example_id"], "target_id": r["target_id"]} for r in absent])
        approved_files.append({"file": label(path), "approved_count": len(rows),
                               "unique_pairs": len({(r["example_id"], r["target_id"]) for r in rows}),
                               "span_count": sum(len(r["spans"]) for r in rows),
                               "source_rows_without_id": sum("id" not in r for r in rows),
                               "id_policy": "Compiler assigns immutable payload-derived reference IDs."})
        approved_rows.extend(rows)
    approved_pairs = {(r["example_id"], r["target_id"]) for r in approved_rows}
    baseline_pairs = {(r["example_id"], r["target_id"]) for r in baseline["references"]}

    runtime_by_pair = {(r["example_id"], r["target_id"]): r for r in final["references"]}
    current_input_by_pair = {(r["example_id"], r["target_id"]): r for r in approved_rows}
    final_item_by_id = {item["id"]: item for item in final_items}
    postchecks = {"counting_policy": "Separate focused-review records; never added to the original canonical finding count.",
                  "pending_files": pending_postchecks,
                  "source_hash_policy": "Review-time hashes remain provenance. Current payloads, mapped identities and exact quotes are verified independently; whole mutable source files need not retain their old hashes."}
    reviewed_runtime_amendments = {}

    def verify_final_reference(expected, *, absent=False, fields=None,
                               register_fields=(), use_later_reviews=False):
        """Verify a reviewed payload across the compiler's explicit text migration."""
        eid, target = expected["example_id"], expected["target_id"]
        final_eid = mapped_example_ids.get(eid, eid)
        actual = runtime_by_pair.get((final_eid, target))
        record = {"source_reference_id": expected.get("id"), "source_example_id": eid,
                  "runtime_example_id": final_eid, "target_id": target,
                  "runtime_reference_id": actual.get("id") if actual else None}
        if absent:
            record.update(passed=actual is None, expected="association_absent")
            return record
        if actual is None:
            return {**record, "passed": False, "error": "Reviewed association missing from runtime."}
        payload = {k: deepcopy(v) for k, v in expected.items()
                   if k not in {"id", "review_status", "review_reason"}}
        old_example = origin_examples.get(eid)
        new_example = final_examples.get(final_eid)
        if old_example is None or new_example is None:
            return {**record, "passed": False, "error": "Missing immutable example for span mapping."}
        payload["example_id"] = final_eid
        if old_example["text"] != new_example["text"]:
            blocks = SequenceMatcher(None, old_example["text"], new_example["text"], autojunk=False).get_matching_blocks()
            shifted = []
            for span in payload["spans"]:
                block = next((b for b in blocks if b.a <= span["start"] < span["end"] <= b.a + b.size), None)
                if block is None:
                    return {**record, "passed": False, "error": "Reviewed focus changed; no exact unchanged-block mapping.", "span": span}
                start = span["start"] + block.b - block.a
                shifted.append({"start": start, "end": start + len(span["quote"]), "quote": span["quote"]})
            payload["spans"] = shifted
            if old_example.get("translation"):
                payload["explanation"] = payload.get("explanation", "").replace(
                    old_example["translation"], new_example.get("translation", ""))
        item = final_item_by_id.get(target, {})
        if item.get("family") == "word" and payload.get("hint", {}).get("reading") != item.get("reading"):
            payload["reading_scope"] = "span"
        if register_fields:
            reviewed_runtime_amendments.setdefault((final_eid, target), {}).update(
                {key: deepcopy(payload[key]) for key in register_fields})
        if use_later_reviews:
            amendments = reviewed_runtime_amendments.get((final_eid, target), {})
            record["later_focused_review_fields_applied"] = sorted(amendments)
            payload.update(deepcopy(amendments))
        actual_payload = {k: v for k, v in actual.items() if k != "id"}
        differences = {k for k in payload.keys() | actual_payload.keys() if payload.get(k) != actual_payload.get(k)}
        compared = set(fields) if fields is not None else payload.keys() | actual_payload.keys()
        checked_differences = sorted(differences & compared)
        record.update(passed=not checked_differences, differing_fields=checked_differences,
                      checked_fields=sorted(compared), other_payload_differences=sorted(differences - compared),
                      expected_payload_sha256=fingerprint(payload), actual_payload_sha256=fingerprint(actual_payload),
                      expected_spans=payload["spans"], actual_spans=actual["spans"])
        return record

    if "morphology" in postcheck_documents:
        document = postcheck_documents["morphology"]
        verified = []
        for snapshot_name, decision_name in (
                ("morphological_review_rows.json", "morphology_decisions.json"),
                ("morphology_additional_rows.json", "morphology_additional_decisions.json")):
            snapshots = read("grammar_b_words_assist_work/" + snapshot_name)
            decisions = read("grammar_b_words_assist_work/" + decision_name)
            check("morphology_" + snapshot_name + "_decision_coverage",
                  set(decisions) == {str(row["index"]) for row in snapshots})
            for row in snapshots:
                decision = decisions.get(str(row["index"]), {})
                expected = deepcopy(row["reference"])
                if decision.get("decision") == "replace_spans":
                    expected["spans"] = decision["spans"]
                # This postcheck adjudicated the morphology and its anchors.
                # A prior corrected baseline association may win compiler pair
                # deduplication with its own independently reviewed explanation.
                verified.append(verify_final_reference(expected, absent=decision.get("decision") == "drop",
                    fields={"example_id", "example_revision", "target_id", "spans"}, register_fields={"spans"}))
        conservative_ids = {
            "example-ref:fbdbc26e09f0668e919cb14bafb4f45f",
            "example-ref:55a02df038d8650ad2405144f232d5d7",
            "example-ref:84d36fc1c540ddcc035fcbe44999b747",
        }
        removals = [r for r in document["corrections"] if r.get("after") is None]
        conservative = [r for r in removals if r["reference_id"] in conservative_ids]
        definite = [r for r in removals if r["reference_id"] not in conservative_ids]
        check("morphology_review_count_reconciled", len(verified) == document["reviewed_count"])
        check("morphology_final_payloads_and_withdrawals", all(r["passed"] for r in verified),
              failures=[r for r in verified if not r["passed"]])
        check("morphology_conservative_scope_withdrawals_identified",
              {r["reference_id"] for r in conservative} == conservative_ids)
        postchecks["morphology"] = {"file": postcheck_paths["morphology"],
            "reviewed_references": len(verified), "span_corrections": sum(r.get("field") == "spans" for r in document["corrections"]),
            "definite_incorrect_associations_removed": len(definite),
            "conservative_scope_withdrawals": len(conservative),
            "scope_withdrawal_note": "These three truncated conditional-form associations were withdrawn under the stated teaching boundary; the Japanese sentences themselves are not classified as erroneous.",
            "correction_records": document["corrections"], "runtime_verification": verified,
            "method": document["method"], "boundaries": document["boundaries"]}

    if "masu" in postcheck_documents:
        document = postcheck_documents["masu"]
        reviews = document.get("reviews", [])
        verified = []
        for row in reviews:
            key = (row["example_id"], row["target_id"])
            current = current_input_by_pair.get(key)
            expected = deepcopy(current) if current else {"id": row.get("reference_id"),
                "example_id": row["example_id"], "target_id": row["target_id"], "example_revision": "1", "weight": 1.0}
            after = row.get("after")
            if isinstance(after, dict):
                expected.update(deepcopy(after))
            result = verify_final_reference(expected, absent=row.get("status") in {"drop", "remove", "withdrawn"},
                                            register_fields={"spans", "hint", "explanation"})
            result["review_index"] = row.get("index")
            verified.append(result)
        check("masu_review_count_reconciled", len(reviews) == document["reviewed_count"])
        check("masu_reviewed_pairs_unique", len({(r["example_id"], r["target_id"]) for r in reviews}) == len(reviews))
        check("masu_final_payloads", all(r["passed"] for r in verified), failures=[r for r in verified if not r["passed"]])
        postchecks["masu"] = {"file": postcheck_paths["masu"], "reviewed_references": len(reviews),
            "status_counts": dict(Counter(r.get("status", "unspecified") for r in reviews)),
            "changed_span_count": sum(r.get("before", {}).get("spans") != r.get("after", {}).get("spans") for r in reviews),
            "method": document.get("method"), "source_urls": document.get("source_urls", []),
            "runtime_verification": verified}

    if integration_snapshot is not None:
        document = postcheck_documents["new_or_rewritten_examples"]
        snapshots = integration_snapshot["examples"]
        existing = [ref for row in snapshots for ref in row["references"]]
        added = read("integration_examples_work/missing_references.json")
        added = added.get("references", []) if isinstance(added, dict) else added
        example_verification = [{"example_id": row["example"]["id"],
            "passed": final_examples.get(row["example"]["id"]) == row["example"],
            "reviewed_payload_sha256": fingerprint(row["example"]),
            "runtime_payload_sha256": fingerprint(final_examples.get(row["example"]["id"]))} for row in snapshots]
        # The nine-example snapshot predates the focused morphology/masu pass.
        # Apply only explicitly reviewed later fields, and record their names.
        reference_verification = [verify_final_reference(ref, use_later_reviews=True) for ref in existing + added]
        check("integration_examples_curriculum_scope", set(document["reviewed_item_ids"]) == baseline_sets["item"])
        check("integration_examples_review_coverage", set(document["reviewed_example_ids"]) == {r["example"]["id"] for r in snapshots}
              and set(document["reviewed_reference_ids"]) == {r["id"] for r in existing})
        check("integration_examples_new_reference_count", len(added) == document["new_reference_count"])
        check("integration_examples_final_payloads", all(r["passed"] for r in example_verification),
              failures=[r for r in example_verification if not r["passed"]])
        check("integration_examples_reference_payloads", all(r["passed"] for r in reference_verification),
              failures=[r for r in reference_verification if not r["passed"]])
        postchecks["new_or_rewritten_examples"] = {"file": postcheck_paths["new_or_rewritten_examples"],
            "examples_reviewed": len(snapshots), "curriculum_items_considered": len(document["reviewed_item_ids"]),
            "existing_references_reviewed": len(existing), "new_approved_references": len(added),
            "method": document["method"], "remaining_uncertainties": document.get("remaining_uncertainties", []),
            "rejected_or_out_of_current_lesson_scope": document.get("rejected_or_out_of_current_lesson_scope", []),
            "example_verification": example_verification, "reference_verification": reference_verification}

    uncertain = read("grammar_a_work/uncertain_candidates.json")
    uncertain_promoted = [row for row in uncertain if (row["example_id"], row["item_id"]) in approved_pairs]
    check("grammar_a_107_uncertain_candidates_retained",
          len(uncertain) == ledgers["grammar_a"]["missing_link_statistics"]["uncertain_candidates"] == 107)
    check("grammar_a_uncertain_not_promoted", not uncertain_promoted, promoted=uncertain_promoted)
    uncertain_in_runtime = [row for row in uncertain
        if (mapped_example_ids.get(row["example_id"], row["example_id"]), row["item_id"]) in pairs]
    check("grammar_a_uncertain_absent_from_runtime", not uncertain_in_runtime, present=uncertain_in_runtime)
    word_candidates = read("words_pronunciation_work/candidate_pair_review.json")
    word_statuses = Counter(row["status"] for row in word_candidates)
    word_withheld = [r for r in word_candidates if r["status"] != "approved"]
    check("word_withheld_count_reconciled", len(word_withheld) == ledgers["words_pronunciation"]["withheld_candidate_count"])
    word_promoted = [r for r in word_withheld if (r["example_id"], r["item_id"]) in approved_pairs]
    check("word_withheld_not_promoted", not word_promoted, promoted=word_promoted)
    word_withheld_in_runtime = [row for row in word_withheld
        if (mapped_example_ids.get(row["example_id"], row["example_id"]), row["item_id"]) in pairs]
    check("word_withheld_absent_from_runtime", not word_withheld_in_runtime, present=word_withheld_in_runtime)
    helper_withheld = read("grammar_b_words_assist_work/withheld_candidates.json")
    # A pair can have an approved occurrence and a different, rejected homograph.
    helper_partial = sum((r["example_id"], r["target_id"]) in approved_pairs for r in helper_withheld)
    basic_candidates = read("grammar_b_work/basic_candidates.json")
    expected_helper_ids = {r["example"]["id"] for r in basic_candidates[2175:]}
    first_half_ids = set(ledgers["grammar_b"]["association_review"]["all_basic_owned_example_ids"])
    check("grammar_b_basic_candidate_sentence_review_complete",
          set(helper["reviewed_example_ids"]) == expected_helper_ids
          and first_half_ids == {r["example"]["id"] for r in basic_candidates[:2175]}
          and not first_half_ids & expected_helper_ids,
          first_half=len(first_half_ids), second_half=len(expected_helper_ids), total=len(basic_candidates))
    check("word_missing_association_all_baseline_sentences_read",
          set(ledgers["words_pronunciation"]["missing_association_scan_example_ids"]) == baseline_sets["example"])
    if ledgers["grammar_b"]["association_review"].get("basic_helper_status") != "complete":
        warnings.append("grammar_b_review.json retains a stale helper-status label; exact sentence IDs in the completed helper ledger independently reconcile the full assigned range.")

    provenance = read("lexical_provenance.json")
    for name, module in sorted(sys.modules.copy().items()):
        if name == "kanamemory" or name.startswith("kanamemory."):
            source = getattr(module, "__file__", None)
            if source and Path(source).suffix == ".py":
                track(Path(source))
    # Detect concurrent edits after all imports and validations, rather than
    # silently issuing a report combining several revisions of the input data.
    changed = [name for name, value in inputs.items() if sha256((ROOT / name).read_bytes()) != value["sha256"]]
    check("input_snapshot_stable_during_run", not changed, changed_files=changed)
    failures = [c["name"] for c in checks if not c["passed"]]
    report = {
        "format": "kanamemory-linguistic-review-report", "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if failures else "pending" if pending_postchecks else "passed", "failed_checks": failures,
        "meaning_of_passed": "Coverage evidence, integration reconciliation and catalogue structural checks passed; this is not proof of absolute semantic correctness or exhaustive Japanese associations.",
        "checks": checks, "warnings": warnings, "baseline_coverage": coverage,
        "focused_postchecks": postchecks,
        "runtime_catalogue": {"items": len(final_items), **runtime_counts,
            "unique_example_target_pairs": len(pairs), "exact_spans_checked": span_count,
            "minimum_unique_examples_per_item": min(counts.values(), default=0),
            "baseline_payload_integration": baseline_integration,
            "unique_examples_per_item": counts, "items_sha256": fingerprint(final_items),
            "pack_sha256": fingerprint(final)},
        "findings": {"canonical_file_row_count": raw_findings_count,
            "unique_finding_count": len(finding_rows), "duplicate_canonical_rows": raw_findings_count - len(finding_rows),
            "counting_policy": "Only the three canonical finding files; mirrored ledger findings are not recounted.",
            "disposition_counts": dict(disposition_counts), "records": finding_rows},
        "association_review": {"approved_files": approved_files,
            "approved_row_count": len(approved_rows), "unique_approved_pairs": len(approved_pairs),
            "duplicate_approved_pair_rows": len(approved_rows) - len(approved_pairs),
            "approved_pairs_already_in_baseline": len(approved_pairs & baseline_pairs),
            "baseline_pair_count": len(baseline_pairs),
            "grammar_a_statistics": ledgers["grammar_a"]["missing_link_statistics"],
            "grammar_b_basic_candidate_sentence_count": len(basic_candidates),
            "grammar_b_helper_scope": helper["scope"],
            "helper_statistics_at_review_time": helper["statistics"],
            "later_span_corrections": corrections,
            "boundaries_by_reviewer": {name: {"method": ledger["method"],
                "limits": ledger.get("limitations", ledger.get("missing_links_limits"))}
                for name, ledger in ledgers.items()}},
        "retained_candidates": {"grammar_a_uncertain_count": len(uncertain),
            "grammar_a_uncertain_records": uncertain,
            "word_candidate_pair_status_counts": dict(word_statuses),
            "word_withheld_count": len(word_withheld),
            "word_withheld_definition": "Rejected or conservatively withheld candidate pairs; not all are unresolved linguistic questions.",
            "grammar_b_original_uncertainty_groups": ledgers["grammar_b"]["uncertain_cases"],
            "helper_withheld_occurrence_groups": len(helper_withheld),
            "helper_groups_with_other_approved_occurrences": helper_partial,
            "helper_fully_withheld_pairs": len(helper_withheld) - helper_partial,
            "helper_withheld_definition": "A rejected occurrence does not reject another independently approved occurrence of the same item in that sentence."},
        "dictionary_provenance_not_semantic_review": {"scope": provenance["scope"],
            "counts": provenance["counts"], "database_sha256": provenance["database_sha256"],
            "limitations": provenance["limitations"],
            "explicit_boundary": "The 3003-word dictionary and its 8823 source examples received provenance/link-state checks, not the complete semantic review performed on the 524-course/4388-example baseline."},
        "limitations": [
            "Reviews are contextual model judgments with primary-source checks, not independent native-speaker certification.",
            "All baseline IDs are covered by review ledgers; this does not establish that every possible semantic error was found.",
            "Missing-link review covers the declared directly observable constructions and contextual lexical senses; candidate retrieval is not proof of all possible associations.",
            "Generic mora/intonation rules are associated only when explicitly demonstrated, not with every sentence by default.",
            "One verified representative span may document a rule; the report does not promise an exhaustive inventory of every token occurrence.",
            "No audio listening audit was performed; text, reading and pitch notation checks do not certify generated speech.",
        ],
        "inputs": dict(sorted(inputs.items())),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output.resolve()), "status": report["status"],
        "failed_checks": failures, "baseline": EXPECTED, "runtime": {"items": len(final_items), **runtime_counts},
        "findings": len(finding_rows), "dispositions": dict(disposition_counts),
        "pending_postchecks": pending_postchecks,
        "uncertain_candidates_retained": len(uncertain)}, ensure_ascii=False))
    return 1 if failures else 2 if pending_postchecks else 0


if __name__ == "__main__":
    raise SystemExit(main())
