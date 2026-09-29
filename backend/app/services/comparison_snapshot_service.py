"""Immutable Comparison Snapshot and Audit Trail Service.

Produces deterministic, canonical, schema-versioned snapshots and audit exports
for the Document Comparison pipeline.
"""
import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

SNAPSHOT_SCHEMA_VERSION = "1.0"
ENGINE_VERSION = "1.0.0"
STRUCTURAL_ALIGNMENT_VERSION = "1.5"
SEMANTIC_CLASSIFIER_VERSION = "3.2"

ALLOWED_EXPORT_FILTERS: Set[str] = {
    "all",
    "numeric",
    "numeric_only",
    "identifier",
    "identifier_only",
    "replacement",
    "insertion",
    "deletion",
    "reordered",
    "formatting",
}

SEMANTIC_LABEL_MAP: Dict[str, str] = {
    "numeric_only": "Numeric",
    "identifier_only": "Identifier",
    "replacement": "Replacement",
    "insertion": "Insertion",
    "deletion": "Deletion",
    "reordered": "Reordered",
    "punctuation_only": "Formatting (Punctuation)",
    "whitespace_only": "Formatting (Whitespace)",
}


def compute_content_sha256(file_path: Optional[str], raw_content: Optional[str]) -> Optional[str]:
    """Compute SHA-256 hash of exact source binary bytes on disk or raw text string."""
    if file_path and os.path.exists(file_path):
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()
    if raw_content is not None:
        return hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
    return None


def _get_file_size(file_path: Optional[str], raw_content: Optional[str]) -> Optional[int]:
    """Get exact source size in bytes."""
    if file_path and os.path.exists(file_path):
        return os.path.getsize(file_path)
    if raw_content is not None:
        return len(raw_content.encode("utf-8"))
    return None


def _sort_locations(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministically sort bounding box locations by (side, page, bbox, box_id)."""
    return sorted(
        locations,
        key=lambda loc: (
            str(loc.get("side", "")),
            int(loc.get("page", 0)),
            [round(float(coord), 3) for coord in (loc.get("bbox") or [0, 0, 0, 0])],
            str(loc.get("box_id") or ""),
        ),
    )


def compute_snapshot_content_sha256(canonical_payload: Dict[str, Any]) -> str:
    """Compute deterministic SHA-256 content hash over canonical engine snapshot.

    Excludes reviewer annotations, export timestamps, and filter parameters to ensure
    the hash reflects purely the immutable engine comparison truth.
    """
    canonical_json = json.dumps(
        canonical_payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _compute_summary_stats(changes: List[Dict[str, Any]], pages_set: Optional[Set[int]] = None, anchors_set: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Calculate summary statistics and breakdown counts for a change list."""
    by_semantic_type: Dict[str, int] = {
        "numeric_only": 0,
        "identifier_only": 0,
        "replacement": 0,
        "insertion": 0,
        "deletion": 0,
        "reordered": 0,
        "punctuation_only": 0,
        "whitespace_only": 0,
    }

    insertions = 0
    deletions = 0
    replacements = 0
    reordered_count = 0
    collected_pages = set(pages_set or set())
    collected_anchors = set(anchors_set or set())

    for c in changes:
        ct = c.get("change_type", "replacement")
        if ct in by_semantic_type:
            by_semantic_type[ct] += 1
        else:
            by_semantic_type[ct] = by_semantic_type.get(ct, 0) + 1

        k = c.get("kind", "modified")
        if k == "added" or ct == "insertion":
            insertions += 1
        elif k == "removed" or ct == "deletion":
            deletions += 1
        elif k == "moved" or ct == "reordered":
            reordered_count += 1
        else:
            replacements += 1

        for loc in c.get("locations", []):
            if "page" in loc:
                collected_pages.add(loc["page"])
        if c.get("structure", {}).get("title"):
            collected_anchors.add(c["structure"]["title"])

    return {
        "total_changes": len(changes),
        "insertions": insertions,
        "deletions": deletions,
        "replacements": replacements,
        "reordered": reordered_count,
        "by_semantic_type": by_semantic_type,
        "pages_affected": sorted(list(collected_pages)),
        "structural_regions_affected": len(collected_anchors),
    }


def build_comparison_snapshot(
    comparison: Any,
    annotations: Optional[List[Any]] = None,
    filter_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an immutable, fully-specified comparison snapshot dictionary.

    Guarantees:
    - Deterministic schema-versioned structure (schema_version 1.0)
    - Canonical snapshot content hash (snapshot_content_sha256)
    - Full document byte SHA-256 verification
    - Preserves all multi-location bboxes and structural anchors
    - Segregates append-only reviewer audit events without mutating engine data
    - Full vs Filtered export distinction with baseline_summary vs export_summary
    """
    if filter_type is not None and filter_type not in ALLOWED_EXPORT_FILTERS:
        raise ValueError(
            f"Unsupported export filter: '{filter_type}'. Allowed filters: {sorted(list(ALLOWED_EXPORT_FILTERS))}"
        )

    comp_id = str(comparison.id)
    created_at = getattr(comparison, "created_at", None)
    created_at_iso = (
        created_at.isoformat()
        if created_at
        else datetime.now(timezone.utc).isoformat()
    )

    # 1. Document Identifiers & Content Hashes (exact file bytes)
    old_file_path = getattr(comparison, "old_file_path", None)
    new_file_path = getattr(comparison, "new_file_path", None)
    old_content = getattr(comparison, "old_original_content", getattr(comparison, "old_text", None))
    new_content = getattr(comparison, "new_original_content", getattr(comparison, "new_text", None))

    old_sha256 = compute_content_sha256(old_file_path, old_content)
    new_sha256 = compute_content_sha256(new_file_path, new_content)

    old_doc = {
        "filename": getattr(comparison, "old_filename", None) or "Original Document",
        "content_type": getattr(comparison, "old_content_type", "application/pdf"),
        "sha256": old_sha256,
        "size_bytes": _get_file_size(old_file_path, old_content),
    }

    new_doc = {
        "filename": getattr(comparison, "new_filename", None) or "Revised Document",
        "content_type": getattr(comparison, "new_content_type", "application/pdf"),
        "sha256": new_sha256,
        "size_bytes": _get_file_size(new_file_path, new_content),
    }

    # 2. Extract and Normalize Baseline Raw Changes
    raw_changes: List[Dict[str, Any]] = []
    rr = getattr(comparison, "render_result", None) or {}
    render_status = getattr(comparison, "render_status", None)
    diff_result = getattr(comparison, "diff_result", None) or []
    pages_affected_set: Set[int] = set()
    anchors_affected_set: Set[str] = set()

    if render_status == "completed" and rr and rr.get("changes"):
        for c in rr["changes"]:
            cid = c.get("id")
            kind = c.get("kind", "modified")
            meta = dict(c.get("metadata") or {})
            ctype = c.get("change_type") or c.get("semantic_type") or meta.get("category")
            if not ctype:
                ctype = {
                    "removed": "deletion",
                    "added": "insertion",
                    "moved": "reordered",
                }.get(kind, "replacement")

            struct = dict(c.get("structure") or {})
            if c.get("section_anchor") and not struct.get("title"):
                struct["title"] = c["section_anchor"]
            if struct.get("title"):
                anchors_affected_set.add(struct["title"])

            old_ref = c.get("old") or {}
            new_ref = c.get("new") or {}
            old_text = old_ref.get("text", "")
            new_text = new_ref.get("text", "")

            # Multi-location mapping
            locations: List[Dict[str, Any]] = []
            if old_ref.get("locations"):
                for loc in old_ref["locations"]:
                    pages_affected_set.add(loc["page"])
                    locations.append({
                        "side": "old",
                        "page": loc["page"],
                        "bbox": loc["bbox"],
                        "text": loc.get("text"),
                        "box_id": loc.get("box_id"),
                    })
            elif old_ref.get("page") and old_ref.get("bbox"):
                pages_affected_set.add(old_ref["page"])
                locations.append({
                    "side": "old",
                    "page": old_ref["page"],
                    "bbox": old_ref["bbox"],
                    "text": old_text,
                    "box_id": f"{cid}-old",
                })

            if new_ref.get("locations"):
                for loc in new_ref["locations"]:
                    pages_affected_set.add(loc["page"])
                    locations.append({
                        "side": "new",
                        "page": loc["page"],
                        "bbox": loc["bbox"],
                        "text": loc.get("text"),
                        "box_id": loc.get("box_id"),
                    })
            elif new_ref.get("page") and new_ref.get("bbox"):
                pages_affected_set.add(new_ref["page"])
                locations.append({
                    "side": "new",
                    "page": new_ref["page"],
                    "bbox": new_ref["bbox"],
                    "text": new_text,
                    "box_id": f"{cid}-new",
                })

            raw_changes.append({
                "change_id": cid,
                "kind": kind,
                "change_type": ctype,
                "semantic_label": SEMANTIC_LABEL_MAP.get(ctype, ctype.title()),
                "old_text": old_text,
                "new_text": new_text,
                "metadata": meta,
                "structure": struct,
                "locations": _sort_locations(locations),
            })
    else:
        # Text-only / non-rendered diff blocks fallback
        for i, b in enumerate(diff_result):
            t = b.get("type")
            if t == "equal":
                continue
            is_moved = bool(b.get("moved"))
            kind = "moved" if is_moved else {"delete": "removed", "insert": "added"}.get(t, "modified")
            ctype = "reordered" if is_moved else {"delete": "deletion", "insert": "insertion"}.get(t, "replacement")

            if t == "delete":
                old_text, new_text = b.get("old_text", ""), ""
            elif t == "insert":
                old_text, new_text = "", b.get("new_text", "")
            elif t == "replace":
                old_text = " ".join(w["text"] for w in b.get("old_words", []) if w.get("changed"))
                new_text = " ".join(w["text"] for w in b.get("new_words", []) if w.get("changed"))
            else:
                old_text = new_text = ""

            raw_changes.append({
                "change_id": f"b{i}",
                "kind": kind,
                "change_type": ctype,
                "semantic_label": SEMANTIC_LABEL_MAP.get(ctype, ctype.title()),
                "old_text": old_text,
                "new_text": new_text,
                "metadata": {"category": ctype},
                "structure": {},
                "locations": [],
            })

    # 3. Compute Baseline Summary
    baseline_summary = _compute_summary_stats(raw_changes, pages_affected_set, anchors_affected_set)

    # 4. Extraction & Rendering Provenance
    old_pages_count = len((rr.get("old") or {}).get("pages", []))
    new_pages_count = len((rr.get("new") or {}).get("pages", []))
    provenance = {
        "total_pages_original": old_pages_count,
        "total_pages_revised": new_pages_count,
        "is_pixel_render_available": comparison.render_status == "completed",
        "render_status": comparison.render_status,
    }

    engine_info = {
        "name": "Regulatory Compliance Agent Compare Engine",
        "version": ENGINE_VERSION,
        "structural_alignment_version": STRUCTURAL_ALIGNMENT_VERSION,
        "semantic_classifier_version": SEMANTIC_CLASSIFIER_VERSION,
    }

    # 5. Build Canonical Engine Snapshot Payload (for Hashing)
    canonical_payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "engine": engine_info,
        "documents": {
            "original": old_doc,
            "revised": new_doc,
        },
        "provenance": provenance,
        "summary": baseline_summary,
        "changes": raw_changes,
    }

    snapshot_content_sha256 = compute_snapshot_content_sha256(canonical_payload)
    snapshot_id = snapshot_content_sha256[:32]

    # 6. Append-Only Reviewer Audit Trail
    review_events: List[Dict[str, Any]] = []
    for idx, a in enumerate(annotations or [], 1):
        evt_id = str(getattr(a, "id", None) or f"evt-{idx:04d}")
        note = getattr(a, "note", None)
        tags = sorted(list(getattr(a, "tags", []) or []))
        actor_id = str(getattr(a, "created_by", None)) if getattr(a, "created_by", None) else None

        created_dt = getattr(a, "created_at", None)
        updated_dt = getattr(a, "updated_at", None)
        evt_type = "UPDATE_NOTE" if (updated_dt and created_dt and updated_dt != created_dt) else "ADD_NOTE"
        ts = updated_dt.isoformat() if updated_dt else (created_dt.isoformat() if created_dt else datetime.now(timezone.utc).isoformat())

        review_events.append({
            "event_id": evt_id,
            "snapshot_id": snapshot_id,
            "change_id": getattr(a, "change_id", None),
            "event_type": evt_type,
            "actor": actor_id,
            "note": note,
            "tags": tags,
            "timestamp": ts,
            "previous_event_id": getattr(a, "previous_event_id", None),
        })

    # 7. Construct Full or Filtered Snapshot
    is_filtered = filter_type and filter_type != "all"
    if is_filtered:
        export_scope = {"type": "filtered", "filter": filter_type}
        if filter_type in ("numeric", "numeric_only"):
            filtered_changes = [c for c in raw_changes if c["change_type"] == "numeric_only"]
        elif filter_type in ("identifier", "identifier_only"):
            filtered_changes = [c for c in raw_changes if c["change_type"] == "identifier_only"]
        elif filter_type == "replacement":
            filtered_changes = [c for c in raw_changes if c["change_type"] == "replacement"]
        elif filter_type == "insertion":
            filtered_changes = [c for c in raw_changes if c["change_type"] == "insertion"]
        elif filter_type == "deletion":
            filtered_changes = [c for c in raw_changes if c["change_type"] == "deletion"]
        elif filter_type == "reordered":
            filtered_changes = [c for c in raw_changes if c["change_type"] == "reordered"]
        elif filter_type == "formatting":
            filtered_changes = [
                c for c in raw_changes if c["change_type"] in ("punctuation_only", "whitespace_only")
            ]
        else:
            filtered_changes = raw_changes

        export_summary = _compute_summary_stats(filtered_changes)

        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot": {
                "snapshot_id": snapshot_id,
                "snapshot_content_sha256": snapshot_content_sha256,
                "comparison_id": comp_id,
                "title": comparison.title or "Document Comparison",
                "created_at": created_at_iso,
                "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),
                "export_scope": export_scope,
                "engine": engine_info,
            },
            "documents": {
                "original": old_doc,
                "revised": new_doc,
            },
            "provenance": provenance,
            "baseline_summary": baseline_summary,
            "export_summary": export_summary,
            "summary": export_summary,  # Backward compatibility alias
            "changes": filtered_changes,
            "review_audit_trail": review_events,
        }

    # Full Export Scope
    export_scope = {"type": "full"}
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot": {
            "snapshot_id": snapshot_id,
            "snapshot_content_sha256": snapshot_content_sha256,
            "comparison_id": comp_id,
            "title": comparison.title or "Document Comparison",
            "created_at": created_at_iso,
            "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),
            "export_scope": export_scope,
            "engine": engine_info,
        },
        "documents": {
            "original": old_doc,
            "revised": new_doc,
        },
        "provenance": provenance,
        "summary": baseline_summary,
        "changes": raw_changes,
        "review_audit_trail": review_events,
    }


def export_snapshot_json(
    comparison: Any,
    annotations: Optional[List[Any]] = None,
    filter_type: Optional[str] = None,
) -> str:
    """Serialize comparison snapshot to deterministic, indented UTF-8 JSON string."""
    snapshot = build_comparison_snapshot(comparison, annotations, filter_type)
    return json.dumps(snapshot, indent=2, ensure_ascii=False)
