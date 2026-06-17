# Cross-Chunk Document Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each chunk be graded against a read-only view of the whole document so the grader stops raising false "missing disclaimer / reference" findings when the required element exists elsewhere (e.g. the footer).

**Architecture:** A new pure helper `build_document_context()` assembles an ordered, focal-marked view of all chunks (full when it fits a token budget, otherwise a window that always keeps the footer). `create_precedent_prompts()` and `create_completeness_sweep_prompt()` gain an optional `document_context` arg that renders a fenced, reference-only block with explicit "grade ONLY the focal section, quote ONLY from it" instructions. `grade_chunk` builds the context per focal chunk behind a config flag. `verify_evidence_grounding` is untouched — every `current_text` still comes from the focal chunk, so it keeps working unchanged.

**Tech Stack:** Python 3.10, FastAPI/SQLAlchemy app, pytest, Docker (backend image bakes code — no source mount).

---

## Testing mechanics (read first)

The backend image **bakes code at build time** (no source volume mount; only `uploads/`, `logs/` are writable bind mounts). For a fast TDD loop without a per-step rebuild:

```bash
# After editing a host file, copy it into the RUNNING container, then run pytest there:
docker cp backend/app/services/preprocessing_service.py compliance-backend:/app/app/services/preprocessing_service.py
docker cp backend/tests/services/test_cross_chunk_context.py compliance-backend:/app/tests/services/test_cross_chunk_context.py
docker compose exec -T backend python -m pytest tests/services/test_cross_chunk_context.py -v
```

`docker cp` overwrites the in-container file instantly (no rebuild). The **final task** does the real `docker compose build backend && docker compose up -d backend` to bake the code permanently and simultaneously apply the already-staged `CRITIC_ENABLED=true` env change. The `compliance-backend` container and `postgres` must be up (`docker compose up -d postgres backend`).

**Commits:** This project's standing rule is **never run `git commit` unless the user explicitly asks** (see memory `feedback_no_auto_commit`). The "Checkpoint" steps below therefore only **stage** (`git add`) and pause for the user. Do not commit autonomously.

## File structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/config.py` | Modify (~line 170) | Two new settings: `cross_chunk_context_enabled`, `cross_chunk_context_token_budget`. |
| `docker-compose.yml` | Modify (backend `environment:`) | Forward the two new vars so the eval can A/B them via env (compose ignores `.env` vars it doesn't list — memory `docker-env-ops`). |
| `backend/app/services/preprocessing_service.py` | Modify | New module-level `build_document_context()`; add `document_context` param to `create_precedent_prompts` and `create_completeness_sweep_prompt`. |
| `backend/app/services/agents/graph/nodes.py` | Modify (~line 699, 749) | Build context per focal chunk behind the flag; pass to both prompts. |
| `backend/tests/services/test_cross_chunk_context.py` | Create | Unit tests for builder + prompt rendering. |
| `backend/tests/services/test_grounding_with_context.py` | Create | Regression: grounding still drops fabricated quotes, passes real focal quotes, with context present. |

---

## Task 1: Config flags

**Files:**
- Modify: `backend/app/config.py` (near line 170, beside `completeness_sweep_enabled`)
- Modify: `docker-compose.yml` (backend service `environment:` block)
- Test: `backend/tests/services/test_cross_chunk_context.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/test_cross_chunk_context.py`:

```python
from app.config import settings


def test_cross_chunk_context_settings_defaults():
    assert settings.cross_chunk_context_enabled is True
    assert settings.cross_chunk_context_token_budget == 8000
```

- [ ] **Step 2: Run it, expect FAIL**

```bash
docker cp backend/tests/services/test_cross_chunk_context.py compliance-backend:/app/tests/services/test_cross_chunk_context.py
docker compose exec -T backend python -m pytest tests/services/test_cross_chunk_context.py::test_cross_chunk_context_settings_defaults -v
```
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'cross_chunk_context_enabled'`.

- [ ] **Step 3: Add the settings**

In `backend/app/config.py`, immediately after the line `completeness_sweep_enabled: bool = True` (~line 170), add:

```python
    # Cross-chunk context: grade each chunk against a read-only view of the whole
    # document so a disclaimer/reference present elsewhere (e.g. footer) isn't
    # falsely flagged as missing. Token budget caps the full-document mode; over
    # budget falls back to a window that always keeps the footer. See
    # docs/superpowers/specs/2026-06-15-cross-chunk-context-design.md.
    cross_chunk_context_enabled: bool = True
    cross_chunk_context_token_budget: int = 8000
```

- [ ] **Step 4: Forward the vars in compose**

In `docker-compose.yml`, in the backend service `environment:` block (where `LLM_MAX_TOKENS` etc. are listed), add:

```yaml
      CROSS_CHUNK_CONTEXT_ENABLED: ${CROSS_CHUNK_CONTEXT_ENABLED:-true}
      CROSS_CHUNK_CONTEXT_TOKEN_BUDGET: ${CROSS_CHUNK_CONTEXT_TOKEN_BUDGET:-8000}
```

- [ ] **Step 5: Run it, expect PASS**

```bash
docker cp backend/app/config.py compliance-backend:/app/app/config.py
docker compose exec -T backend python -m pytest tests/services/test_cross_chunk_context.py::test_cross_chunk_context_settings_defaults -v
```
Expected: PASS.

- [ ] **Step 6: Checkpoint (stage only)**

```bash
git add backend/app/config.py docker-compose.yml backend/tests/services/test_cross_chunk_context.py
# Do NOT commit — pause for user per project rule.
```

---

## Task 2: `build_document_context()` pure helper

**Files:**
- Modify: `backend/app/services/preprocessing_service.py` (add module-level function near the top, after imports, before the class)
- Test: `backend/tests/services/test_cross_chunk_context.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/services/test_cross_chunk_context.py`:

```python
from app.services.preprocessing_service import build_document_context


def _chunks(texts):
    return [{"id": f"c{i}", "chunk_index": i, "text": t} for i, t in enumerate(texts)]


def test_full_mode_includes_every_chunk_and_marks_focal():
    chunks = _chunks(["intro", "claim body", "more body", "Returns not guaranteed. T&C apply."])
    ctx = build_document_context(chunks, focal_index=1, token_budget=8000)
    # All non-focal texts present
    assert "intro" in ctx
    assert "Returns not guaranteed" in ctx
    # Focal shown as a position marker, not duplicated full text
    assert "[chunk 1]" in ctx
    assert "BEING GRADED" in ctx
    # No omitted-range marker in full mode
    assert "omitted" not in ctx


def test_windowed_mode_always_keeps_footer_even_when_focal_is_early():
    # 12 large chunks so the total blows the tiny budget; focal is chunk 1.
    big = "x" * 4000  # ~1000 tokens each by the char/4 estimate
    chunks = _chunks([big] * 11 + ["FOOTER DISCLAIMER: returns not guaranteed"])
    ctx = build_document_context(chunks, focal_index=1, token_budget=2000)
    # Footer (last chunk) is retained despite focal being far away
    assert "FOOTER DISCLAIMER" in ctx
    # A gap between the focal window and the footer is marked
    assert "omitted" in ctx
    # Focal marker present
    assert "[chunk 1]" in ctx and "BEING GRADED" in ctx


def test_single_chunk_is_just_the_focal_marker():
    ctx = build_document_context(_chunks(["only section"]), focal_index=0, token_budget=8000)
    assert "[chunk 0]" in ctx
    assert "BEING GRADED" in ctx
    assert "only section" not in ctx  # focal text not duplicated into context


def test_budget_boundary_just_under_stays_full():
    # Two chunks ~500 tokens each (2000 chars) → ~1000 tokens total, under 1500 budget.
    chunks = _chunks(["a" * 2000, "b" * 2000])
    ctx = build_document_context(chunks, focal_index=0, token_budget=1500)
    assert "omitted" not in ctx
    assert "b" * 2000 in ctx  # non-focal chunk fully present
```

- [ ] **Step 2: Run, expect FAIL**

```bash
docker cp backend/tests/services/test_cross_chunk_context.py compliance-backend:/app/tests/services/test_cross_chunk_context.py
docker compose exec -T backend python -m pytest tests/services/test_cross_chunk_context.py -v -k "mode or single or boundary"
```
Expected: FAIL — `ImportError: cannot import name 'build_document_context'`.

- [ ] **Step 3: Implement the helper**

In `backend/app/services/preprocessing_service.py`, add this module-level function **after the imports and before the first class definition**:

```python
def build_document_context(
    chunks: "List[Dict]",
    focal_index: int,
    token_budget: int = 8000,
) -> str:
    """Render an ordered, focal-marked view of the whole document for read-only
    reference during grading.

    The focal chunk is shown as a position marker only (its full text is already
    the graded "NEW DOCUMENT SECTION"), so it is never duplicated. When the whole
    document fits ``token_budget`` (char/4 estimate), every chunk is included;
    otherwise a window keeps chunk 0, focal ±2, and the LAST TWO chunks (footers /
    disclaimers live at the end), inserting "[… chunks A–B omitted …]" markers for
    gaps. See docs/superpowers/specs/2026-06-15-cross-chunk-context-design.md.
    """
    def _est_tokens(s: str) -> int:
        return max(1, len(s or "") // 4)

    ordered = sorted(chunks, key=lambda c: c.get("chunk_index", 0))
    if not ordered:
        return ""
    indices = [c.get("chunk_index", i) for i, c in enumerate(ordered)]
    total = sum(_est_tokens(c.get("text", "")) for c in ordered)

    if total <= token_budget:
        keep = set(indices)
    else:
        keep = {indices[0], indices[-1]}
        if len(indices) >= 2:
            keep.add(indices[-2])
        for idx in indices:
            if focal_index - 2 <= idx <= focal_index + 2:
                keep.add(idx)

    parts: "List[str]" = []
    prev_kept = None
    for c in ordered:
        idx = c.get("chunk_index", 0)
        if idx not in keep:
            continue
        if prev_kept is not None and idx - prev_kept > 1:
            parts.append(f"[… chunks {prev_kept + 1}–{idx - 1} omitted …]")
        if idx == focal_index:
            parts.append(f"[chunk {idx}] >>> THIS IS THE SECTION BEING GRADED (shown above) <<<")
        else:
            parts.append(f"[chunk {idx}] {(c.get('text') or '').strip()}")
        prev_kept = idx
    return "\n\n".join(parts)
```

(`List` and `Dict` are already imported at the top of this module from `typing`.)

- [ ] **Step 4: Run, expect PASS**

```bash
docker cp backend/app/services/preprocessing_service.py compliance-backend:/app/app/services/preprocessing_service.py
docker compose exec -T backend python -m pytest tests/services/test_cross_chunk_context.py -v
```
Expected: PASS (all builder tests + the settings test).

- [ ] **Step 5: Checkpoint (stage only)**

```bash
git add backend/app/services/preprocessing_service.py backend/tests/services/test_cross_chunk_context.py
# Do NOT commit.
```

---

## Task 3: `document_context` param on `create_precedent_prompts`

**Files:**
- Modify: `backend/app/services/preprocessing_service.py:532` (signature) and the prompt f-string (~line 679)
- Test: `backend/tests/services/test_cross_chunk_context.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/services/test_cross_chunk_context.py`:

```python
from app.services.preprocessing_service import ContextEngineeringService


def _svc():
    return ContextEngineeringService(db=None)


def test_prompt_without_context_is_unchanged():
    svc = _svc()
    base = svc.create_precedent_prompts("some copy", precedents=[], rules=[])
    assert "DOCUMENT CONTEXT" not in base


def test_prompt_with_context_adds_reference_block_and_instructions():
    svc = _svc()
    ctx = "[chunk 0] intro\n\n[chunk 1] >>> THIS IS THE SECTION BEING GRADED (shown above) <<<\n\n[chunk 2] Returns not guaranteed."
    p = svc.create_precedent_prompts("some copy", precedents=[], rules=[], document_context=ctx)
    assert "DOCUMENT CONTEXT" in p
    assert "Returns not guaranteed." in p
    # Must instruct grade-only-focal / quote-only-focal
    assert "ONLY from" in p
    assert "do NOT raise it" in p
```

- [ ] **Step 2: Run, expect FAIL**

```bash
docker cp backend/tests/services/test_cross_chunk_context.py compliance-backend:/app/tests/services/test_cross_chunk_context.py
docker compose exec -T backend python -m pytest tests/services/test_cross_chunk_context.py -v -k "prompt_with_context or without_context"
```
Expected: FAIL — `create_precedent_prompts() got an unexpected keyword argument 'document_context'`.

- [ ] **Step 3: Change the signature**

In `backend/app/services/preprocessing_service.py`, change the method signature at line 532 from:

```python
    def create_precedent_prompts(
        self, content: str, precedents: List[Dict], rules: Optional[List[Dict]] = None
    ) -> str:
```

to:

```python
    def create_precedent_prompts(
        self, content: str, precedents: List[Dict], rules: Optional[List[Dict]] = None,
        document_context: Optional[str] = None,
    ) -> str:
```

- [ ] **Step 4: Build the context block**

In the same method, immediately **after** the line `fence = f"UNTRUSTED-{_uuid.uuid4().hex[:12]}"` (~line 643) and before `prompt = f"""You are a senior...`, insert:

```python
        if document_context:
            document_context_block = (
                "DOCUMENT CONTEXT (the rest of this document, for REFERENCE ONLY —\n"
                "do NOT grade it). Use it solely to check whether a required disclaimer,\n"
                "footnote, reference or substantiation already appears ELSEWHERE in the\n"
                "document. If a finding's required remedy is already present elsewhere\n"
                "here, do NOT raise it. Grade ONLY the NEW DOCUMENT SECTION below and\n"
                "quote `current_text` ONLY from that section — never from DOCUMENT CONTEXT.\n"
                f"«{fence}»\n{document_context}\n«{fence}»\n\n"
            )
        else:
            document_context_block = ""
```

- [ ] **Step 5: Render the block in the prompt**

In the prompt f-string, change the `NEW DOCUMENT SECTION:` region (line 679) from:

```python
NEW DOCUMENT SECTION:
«{fence}»
{content}
«{fence}»
```

to:

```python
{document_context_block}NEW DOCUMENT SECTION:
«{fence}»
{content}
«{fence}»
```

- [ ] **Step 6: Run, expect PASS**

```bash
docker cp backend/app/services/preprocessing_service.py compliance-backend:/app/app/services/preprocessing_service.py
docker compose exec -T backend python -m pytest tests/services/test_cross_chunk_context.py -v
```
Expected: PASS. Also run the existing prompt tests to confirm no regression:

```bash
docker compose exec -T backend python -m pytest tests/services/test_precedent_prompt.py tests/test_precedent_prompt_v2.py -v
```
Expected: PASS (the `document_context=None` default keeps the old prompt intact).

- [ ] **Step 7: Checkpoint (stage only)**

```bash
git add backend/app/services/preprocessing_service.py backend/tests/services/test_cross_chunk_context.py
# Do NOT commit.
```

---

## Task 4: Thread `document_context` through the completeness sweep

**Files:**
- Modify: `backend/app/services/preprocessing_service.py:763` (`create_completeness_sweep_prompt`)
- Test: `backend/tests/services/test_cross_chunk_context.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/services/test_cross_chunk_context.py`:

```python
def test_sweep_prompt_forwards_document_context():
    svc = _svc()
    ctx = "[chunk 2] Returns not guaranteed."
    p = svc.create_completeness_sweep_prompt(
        "some copy", precedents=[], rules=[], already_found=["x"], document_context=ctx
    )
    assert "DOCUMENT CONTEXT" in p
    assert "Returns not guaranteed." in p
    assert "COMPLETENESS SWEEP" in p  # still the sweep prompt
```

- [ ] **Step 2: Run, expect FAIL**

```bash
docker cp backend/tests/services/test_cross_chunk_context.py compliance-backend:/app/tests/services/test_cross_chunk_context.py
docker compose exec -T backend python -m pytest tests/services/test_cross_chunk_context.py::test_sweep_prompt_forwards_document_context -v
```
Expected: FAIL — `create_completeness_sweep_prompt() got an unexpected keyword argument 'document_context'`.

- [ ] **Step 3: Add and forward the param**

In `backend/app/services/preprocessing_service.py`, change the signature at line 763 from:

```python
    def create_completeness_sweep_prompt(
        self,
        content: str,
        precedents: List[Dict],
        rules: Optional[List[Dict]] = None,
        already_found: Optional[List[str]] = None,
    ) -> str:
```

to:

```python
    def create_completeness_sweep_prompt(
        self,
        content: str,
        precedents: List[Dict],
        rules: Optional[List[Dict]] = None,
        already_found: Optional[List[str]] = None,
        document_context: Optional[str] = None,
    ) -> str:
```

Then change the line that builds `base` (~line 775) from:

```python
        base = self.create_precedent_prompts(content, precedents, rules=rules)
```

to:

```python
        base = self.create_precedent_prompts(
            content, precedents, rules=rules, document_context=document_context
        )
```

- [ ] **Step 4: Run, expect PASS**

```bash
docker cp backend/app/services/preprocessing_service.py compliance-backend:/app/app/services/preprocessing_service.py
docker compose exec -T backend python -m pytest tests/services/test_cross_chunk_context.py -v
```
Expected: PASS.

- [ ] **Step 5: Checkpoint (stage only)**

```bash
git add backend/app/services/preprocessing_service.py backend/tests/services/test_cross_chunk_context.py
# Do NOT commit.
```

---

## Task 5: Wire context into `grade_chunk` (and the sweep call)

**Files:**
- Modify: `backend/app/services/agents/graph/nodes.py` (import + ~line 699 + ~line 749)

- [ ] **Step 1: Add the import**

Near the other `from app.services...` imports at the top of `backend/app/services/agents/graph/nodes.py`, add:

```python
from app.services.preprocessing_service import build_document_context
```

- [ ] **Step 2: Build context and pass it to the main grading prompt**

In `grade_chunk`, change the prompt-building line (line 699) from:

```python
                prompt = context_service.create_precedent_prompts(chunk_text, precedents, rules=rules)
```

to:

```python
                document_context = None
                if _settings.cross_chunk_context_enabled:
                    document_context = build_document_context(
                        chunks_data, chunk_index, _settings.cross_chunk_context_token_budget
                    )
                prompt = context_service.create_precedent_prompts(
                    chunk_text, precedents, rules=rules, document_context=document_context
                )
```

(`chunks_data` is the enclosing-scope list of all chunk dicts that `grade_chunk` is mapped over at `tasks = [grade_chunk(c) for c in chunks_data]`; `chunk_index` is already bound at the top of `grade_chunk`.)

- [ ] **Step 3: Pass context to the completeness-sweep prompt**

In the same function, change the sweep-prompt build (line 749) from:

```python
                        sweep_prompt = context_service.create_completeness_sweep_prompt(
                            chunk_text, precedents, rules=rules, already_found=already
                        )
```

to:

```python
                        sweep_prompt = context_service.create_completeness_sweep_prompt(
                            chunk_text, precedents, rules=rules, already_found=already,
                            document_context=document_context,
                        )
```

- [ ] **Step 4: Sanity-import in the container**

```bash
docker cp backend/app/services/agents/graph/nodes.py compliance-backend:/app/app/services/agents/graph/nodes.py
docker compose exec -T backend python -c "from app.services.agents.graph import nodes; print('import ok')"
```
Expected: `import ok` (no ImportError / NameError).

- [ ] **Step 5: Checkpoint (stage only)**

```bash
git add backend/app/services/agents/graph/nodes.py
# Do NOT commit.
```

---

## Task 6: Grounding regression with context present

**Files:**
- Create: `backend/tests/services/test_grounding_with_context.py`

Confirms the safety invariant: even though the model now sees other chunks, `verify_evidence_grounding` still drops a finding whose `current_text` is only in the context (not the focal chunk), and still keeps a real focal quote.

- [ ] **Step 1: Confirm the grounding function name/signature**

```bash
docker compose exec -T backend python -c "from app.services.agents.graph.nodes import verify_evidence_grounding; import inspect; print(inspect.signature(verify_evidence_grounding))"
```
Expected: prints a 2-arg signature `(violations, chunk_text)` (list of violation dicts, focal chunk text). If the parameter names differ, adjust the test below to match.

- [ ] **Step 2: Write the test**

Create `backend/tests/services/test_grounding_with_context.py`:

```python
from app.services.agents.graph.nodes import verify_evidence_grounding


def test_quote_only_in_context_is_dropped():
    focal = "100% Guaranteed Early Income for your child."
    violations = [
        {"current_text": "Returns are not guaranteed", "description": "from a NEIGHBOR chunk, not focal"},
        {"current_text": "100% Guaranteed Early Income", "description": "real focal quote"},
    ]
    kept = verify_evidence_grounding(violations, focal)
    kept_texts = [v["current_text"] for v in kept]
    assert "100% Guaranteed Early Income" in kept_texts          # real focal quote kept
    assert "Returns are not guaranteed" not in kept_texts        # context-only quote dropped


def test_structural_empty_current_text_is_kept():
    focal = "Some heading"
    violations = [{"current_text": "", "description": "structural/novel finding has no quote"}]
    kept = verify_evidence_grounding(violations, focal)
    assert len(kept) == 1  # empty current_text is not treated as fabricated
```

- [ ] **Step 3: Run, expect PASS**

```bash
docker cp backend/tests/services/test_grounding_with_context.py compliance-backend:/app/tests/services/test_grounding_with_context.py
docker compose exec -T backend python -m pytest tests/services/test_grounding_with_context.py -v
```
Expected: PASS. (These assert *existing* behavior — they should pass without touching `verify_evidence_grounding`. If `test_structural_empty_current_text_is_kept` fails, that reveals a real pre-existing gap; stop and report rather than editing grounding, since the spec says grounding is unchanged.)

- [ ] **Step 4: Checkpoint (stage only)**

```bash
git add backend/tests/services/test_grounding_with_context.py
# Do NOT commit.
```

---

## Task 7: Bake the image, apply CRITIC env, full regression

**Files:** none (build + run)

- [ ] **Step 1: Rebuild and recreate the backend (bakes code + applies CRITIC_ENABLED=true)**

```bash
cd D:/Regulatory-Compliance-Agent
docker compose build backend
docker compose up -d --force-recreate backend
```

- [ ] **Step 2: Verify the env and a clean import in the baked image**

```bash
docker compose exec -T backend printenv CRITIC_ENABLED CROSS_CHUNK_CONTEXT_ENABLED LLM_MODEL
docker compose exec -T backend python -c "from app.services.agents.graph import nodes; from app.services.preprocessing_service import build_document_context; print('ok')"
```
Expected: `CRITIC_ENABLED=true`, `CROSS_CHUNK_CONTEXT_ENABLED=true`, `LLM_MODEL=gemma4:31b-cloud`, then `ok`.

- [ ] **Step 3: Run the new + adjacent suites in the baked image**

```bash
docker compose exec -T backend python -m pytest \
  tests/services/test_cross_chunk_context.py \
  tests/services/test_grounding_with_context.py \
  tests/services/test_precedent_prompt.py \
  tests/test_precedent_prompt_v2.py -v
```
Expected: all PASS.

- [ ] **Step 4: Checkpoint (stage only) + report**

```bash
git add -A
git status
# Do NOT commit. Summarize what changed and ask the user whether to commit and/or run the A/B eval.
```

---

## Optional follow-up (not part of this plan): A/B eval

Re-run the precedent replay once with the flag off and once on, to quantify the false-positive reduction. Note Ollama's free-tier **session usage limit** (it bailed the last run at doc 37/40) — an A/B needs an Ollama upgrade or a non-Ollama provider to complete cleanly. Command shape:

```bash
# OFF:
MSYS_NO_PATHCONV=1 docker compose exec -T -e CROSS_CHUNK_CONTEXT_ENABLED=false backend \
  python -m scripts.eval_precedent_replay --folder /app/uploads/dataset_2.1_rl --eval-frac 0.1
# (save logs/eval_replay.json elsewhere, then re-run with =true)
```

---

## Self-review

- **Spec coverage:** full-document reference with token guard (Task 2) ✓; footer always kept (Task 2 windowed test) ✓; read-only / grade-only-focal / quote-only-focal instructions (Task 3) ✓; grounding unchanged + regression (Task 6) ✓; sweep gets context (Task 4) ✓; config flag default True + compose passthrough (Task 1) ✓; suppress-only scope, no contradiction detection ✓ (nothing adds it). 
- **Placeholders:** none — every code step has concrete code and exact commands.
- **Type/name consistency:** `build_document_context(chunks, focal_index, token_budget)` and `document_context` kwarg used identically across Tasks 2–5; `_settings` is the existing alias in `nodes.py`; `chunks_data`/`chunk_index` are existing locals; `«{fence}»` matches the existing fence syntax.
