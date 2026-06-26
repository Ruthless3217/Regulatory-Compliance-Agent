# Regulatory Compliance Agent — System Architecture

> Marketing-content compliance review for Bajaj Life. A document is pasted or uploaded,
> the system grades it against historical reviewer decisions and regulatory rules, and
> returns scored violations the user can interrogate via chat.
>
> **Core principle:** the engine never decides from raw model knowledge. Every finding is
> grounded in retrieved evidence — a past reviewer decision (precedent), an active rule, or
> expert judgment that must clear a high confidence bar. Anything it cannot evaluate fails
> closed to human review; nothing is ever silently graded clean.

---

## 1. System Overview

```mermaid
flowchart TB
    subgraph FE["Frontend — Next.js 15 / React 19 / Tailwind / Radix"]
        UI["lib/api.ts (typed fetch)"]
    end

    subgraph API["API Layer — FastAPI (every LLM route guarded by llm_rate_limit)"]
        R1["submissions"]
        R2["compliance"]
        R3["rules"]
        R4["chat"]
        R5["dashboard / knowledge_base / rag_health"]
    end

    subgraph ORCH["Orchestration — LangGraph 6-node pipeline (ComplianceEngine)"]
        G["preprocess → dispatch → analysis → disclosure → scoring → refinement"]
    end

    subgraph SVC["Supporting Services"]
        L["LLMService (multi-key failover)"]
        P["ContextEngineeringService (chunking)"]
        RG["RuleGeneratorService"]
        RL["Groq + HTTP RateLimiters"]
    end

    subgraph RAG["RAG Subsystem (pluggable)"]
        E["Embedders"]
        VS["Vector Stores"]
        RET["Retrievers"]
        IDX["Indexers"]
    end

    subgraph DB["PostgreSQL + pgvector"]
        T1["submissions · content_chunks"]
        T2["rules · compliance_checks · violations"]
        T3["rag_rules · rag_chunks · rag_source_docs · rag_compliance_examples"]
    end

    UI -->|REST + SSE| API
    API --> ORCH
    ORCH <--> SVC
    ORCH <--> RAG
    SVC --> DB
    RAG --> DB
    ORCH --> DB
```

The compliance engine is orchestrated as a **linear LangGraph state machine**. The RAG
subsystem supplies the evidence each tier needs. The data layer keeps everything
audit-traceable (versioned rules, citation locators, a suppressed-finding lane).

---

## 2. The Analysis Pipeline (LangGraph)

`ComplianceEngine.analyze_submission(submission_id, db)` (`engine.py:73`) claims the
submission under a row lock, runs the graph to `END`, then applies a **fail-closed
persistability gate** before writing anything.

```mermaid
flowchart LR
    START((START)) --> PRE["preprocess<br/>(Librarian)"]
    PRE --> DIS["dispatch<br/>(Brain)"]
    DIS --> ANA["analysis<br/>(Specialist)"]
    ANA --> DISC["disclosure<br/>(Checker)"]
    DISC --> SCO["scoring<br/>(Scorer)"]
    SCO --> REF["refinement<br/>(HITL no-op)"]
    REF --> END((END))

    PRE -.->|chunks| ST[(ComplianceState<br/>shared, append-reducers)]
    DIS -.->|rules + precedents<br/>+ degraded flags| ST
    ANA -.->|violations| ST
    DISC -.->|disclosure violations| ST
    SCO -.->|scores| ST
```

| Node | Role | What it does | LLM calls |
|------|------|--------------|-----------|
| **preprocess** | Librarian | Section-aware chunking; mirrors chunks into `rag_chunks` (non-fatal) | 0 |
| **dispatch** | Brain | Loads active rules (fail-closed); per-chunk RAG retrieval of **rules** + **precedents**; enriches rules with regulator quotes; sets degradation flags | 0 |
| **analysis** | Specialist | Grades each chunk in parallel — **three-tier grounding** + completeness sweep + critic + dedup | 1–2 / chunk |
| **disclosure** | Checker | Deterministically verifies mandated disclaimers (`backend/app/services/disclaimer/`) against the registry in `backend/data/disclaimers/` | 0 |
| **scoring** | Scorer | Absolute-deduction score, critical fail-cap, per-category subscores, grade + status | 0 |
| **refinement** | HITL | No-op passthrough — graph runs straight through so analysis completes in one call | 0 |

State flows forward via a shared `ComplianceState` TypedDict (`graph/state.py`); `violations`,
`active_agents`, and `messages` use `Annotated[..., operator.add]` reducers so node outputs
append. DB sessions reach nodes through a `ContextVar` (`graph/context.py`), not serialized state.

### Engine wrapper (fail-closed gate)

```mermaid
flowchart TD
    A["Row-lock + claim submission<br/>status = analyzing"] --> B["Run LangGraph → final_state"]
    B --> C{"evaluate_persistability()"}
    C -->|no_content / failed /<br/>degraded / failed chunk| D["status = needs_review / failed<br/>return None — NOT graded clean"]
    C -->|clean| E["Atomic transaction:<br/>ComplianceCheck + Violations<br/>+ submission = analyzed"]
    E --> F["Mark RAG chunks analyzed<br/>(non-fatal)"]
```

---

## 3. The Engines and How They Work Together

The "analysis node" is really **three grounding engines feeding one consolidation
stage**. A single LLM call per chunk (temperature 0, structured output
`PrecedentCitationsResult`) emits findings into all three lanes at once; the engines differ
in *what evidence backs them* and *how their output is mapped to a violation*.

```mermaid
flowchart TB
    CHUNK["Chunk text + retrieved evidence"] --> LLM["LLM grade<br/>(temp 0, structured)"]

    LLM --> P["PRECEDENT engine — Tier 1 (rank 3)<br/>'a reviewer flagged this exact thing before'"]
    LLM --> R["RULE engine — Tier 2 (rank 2)<br/>'an active regulatory rule covers this'"]
    LLM --> N["NOVEL engine — Tier 3 (rank 1)<br/>'no precedent/rule, expert judgment'"]

    P --> PMAP["_citation_to_violation()<br/>carries cited_anchor_text,<br/>cited_comment_verbatim, cited_final_text,<br/>similarity_score · grounding=precedent"]
    R --> RMAP["_rule_finding_to_violation()<br/>rule_id + regulator_quote<br/>severity remap high→moderate<br/>grounding=rule"]
    N --> NMAP["_novel_finding_to_violation()<br/>regulatory_basis required<br/>confidence < 0.75 → SUPPRESSED<br/>grounding=novel"]

    PMAP --> CON["CONSOLIDATION"]
    RMAP --> CON
    NMAP --> CON

    subgraph CON["Per-chunk consolidation"]
        direction TB
        S1["Corrective retry<br/>(out-of-range indices)"]
        S2["Completeness sweep<br/>(2nd pass, only-new findings)"]
        S3["Critic: verify_evidence_grounding<br/>drop fabricated current_text"]
        S4["Cross-tier dedup<br/>keep strongest by (severity, tier)"]
        S5["Structural suppression<br/>headings/brand lines → review lane"]
        S1 --> S2 --> S3 --> S4 --> S5
    end

    CON --> OUT["violations[] for this chunk"]
```

### Why three tiers, ranked

```mermaid
flowchart LR
    subgraph Evidence strength
        direction LR
        T1["TIER 1 PRECEDENT<br/>human-decided · highest trust"] --> T2["TIER 2 RULE<br/>regulator-cited"] --> T3["TIER 3 NOVEL<br/>model judgment · lowest trust"]
    end
    T1 -.grounded in.-> E1["rag_compliance_examples<br/>(5,323 reviewer decisions)"]
    T2 -.grounded in.-> E2["rag_rules + rag_source_docs<br/>(regulator passages)"]
    T3 -.must supply.-> E3["regulatory_basis + confidence ≥ 0.75<br/>else suppressed"]
```

When precedent, rule, and novel all flag the **same phrase**, cross-tier dedup keeps the
strongest: `precedent (3) > rule (2) > novel (1)`, then by severity rank. Structural
false-positives (headings, brand lines) are *suppressed, not dropped* — persisted to an
auditable review lane and kept out of scoring.

### 3a. Precedent Engine

```mermaid
sequenceDiagram
    participant D as dispatch_node
    participant PR as PrecedentRetriever
    participant VS as Vector Store (pgvector)
    participant A as analysis_node
    participant LLM as LLM
    participant CR as Critic (grounding)

    D->>PR: retrieve_per_chunk(chunks, top_k)
    PR->>VS: hybrid_search(rag_compliance_examples)
    VS-->>PR: precedent hits (cosine + BM25 → RRF)
    PR-->>D: filter thin/noise + same-document leakage
    Note over A,LLM: per chunk, with retrieved precedents
    A->>LLM: "cite precedents that apply to this section"
    LLM-->>A: PrecedentCitation[] (precedent_index, current_text,<br/>reviewer_comment, action_type, confidence)
    A->>CR: verify current_text is verbatim in chunk
    CR-->>A: drop fabricated citations
    A-->>A: map → violation w/ cited_* provenance fields
```

The precedent engine is the strongest tier because each finding is anchored to a *real past
reviewer decision* — it carries the original reviewer's comment, the approved rewrite, and a
similarity score, all persisted on the violation for audit.

### 3b. Rule Engine

```mermaid
sequenceDiagram
    participant D as dispatch_node
    participant RR as RulesRetriever
    participant VS as Vector Store
    participant SD as SourceDocsRetriever
    participant A as analysis_node
    participant LLM as LLM

    D->>RR: retrieve_per_chunk(chunks, categories, top_k)
    RR->>VS: hybrid_search(rag_rules, filters={category, is_active})
    VS-->>RR: rule hits per (chunk, category)
    RR-->>D: {chunk_id: {category: [rules]}} (cap 8/chunk)
    D->>SD: by_rule(rule_id) — enrich w/ regulator passage
    SD-->>D: source_quote
    A->>LLM: "violations NOT covered by precedent → rule_findings"
    LLM-->>A: RuleFinding[] (rule_index, current_text,<br/>reviewer_comment, confidence)
    A-->>A: map → violation w/ rule_id + regulator_quote<br/>severity remapped high→moderate
```

Rule findings cover the regulatory ground precedents miss. Severity is remapped
conservatively (`high → moderate`) so rule findings don't inflate the critical count that
drives the fail-closed cap. On RAG failure the dispatch node falls back to the full active-rule
set and flags `rag_degraded`.

### 3c. Novel Engine

The last resort: the LLM may only emit a novel finding when **no precedent and no rule
covers it**. It must supply a `regulatory_basis` and clear the `NOVEL_CONFIDENCE_FLOOR = 0.75`.
Anything below the floor is persisted with `suppressed=True` + reason, kept out of the score,
and routed to a human. Hard-coded `severity=moderate`, `category=regulatory`.

---

## 4. RAG Subsystem

A protocol-driven, multi-backend retrieval platform. Two abstractions in `rag/ports.py`
(`Embedder`, `VectorStore`) make backends swappable **by environment variable only** — this is
the pgvector-v1 → Azure-AI-Search-v2 roadmap materialized.

```mermaid
flowchart TB
    subgraph FAC["factory.py (LRU singletons, env-dispatched)"]
        EB["RAG_EMBEDDING_PROVIDER<br/>openai · azure_openai · cohere"]
        BK["RAG_VECTOR_BACKEND<br/>pgvector · azure_search · pinecone"]
    end

    subgraph HYB["Hybrid search (pgvector backend)"]
        direction TB
        QV["query vector"] --> VL["vector leg<br/>cosine on pgvector"]
        QT["query text"] --> KL["keyword leg<br/>BM25 on tsvector"]
        VL --> RRF["RRF fusion (rrf_k=60)"]
        KL --> RRF
        FL1["cosine floor"] -.guards.-> RRF
        FL2["BM25 floor"] -.guards.-> RRF
        RRF --> HITS["top-k SearchHits"]
    end

    subgraph IDXS["Four indexes"]
        I1["rag_rules ← rules table"]
        I2["rag_compliance_examples ← 5,323 precedents"]
        I3["rag_source_docs ← regulator PDFs"]
        I4["rag_chunks ← submission chunks"]
    end

    EB --> HYB
    BK --> HYB
    HYB --> IDXS
```

- **Embedding-model safety:** every upsert stamps `embedding_model`/`embedding_dim`; query
  time validates the corpus matches the active embedder and raises `RAGDegraded` on mismatch
  (guards silent cosine corruption).
- **Two quality floors** (cosine + BM25) prevent hallucinated grounding from spurious lexical
  matches.
- **Retrievers** (all async, parallelized via `asyncio.gather`): `PrecedentRetriever`,
  `RulesRetriever`, `ChatRetriever` (3-way + violation linking), `SourceDocsRetriever`,
  `SimilarSubmissionsRetriever`.

---

## 5. Scoring and the Feedback Loop

`ScoringService.calculate_scores` (`scoring.py:36`) uses an **absolute-deduction** model that
is independent of category count:

```
overall = 100 − Σ( severity_weight(v) × reliability(rule) × confidence(v) )   clamp [0,100]

severity_weight:  critical=20  high=10  moderate=8  medium=5  low=2  informational=2
```

```mermaid
flowchart TD
    V["violations[]"] --> F["filter suppressed<br/>(don't move the score)"]
    F --> EN["enrich points:<br/>base × reliability θ × confidence"]
    EN --> SUM["overall = 100 − Σ deductions"]
    SUM --> CAP{"critical ≥ 0.50 conf?"}
    CAP -->|yes| C70["cap at 70.0 → grade C max<br/>status = failed"]
    CAP -->|no| GR["grade A/B/C/D/F"]
    C70 --> GR
    GR --> ST["status: passed / flagged / failed"]
```

**Adaptive rule weights (HITL loop):** each rule's penalty is scaled by its Beta-Binomial
posterior mean `θ = α/(α+β)`, learned from reviewer accept/reject feedback. NULL counts → θ=1.0
(never under-penalize); floored at 0.30 (feedback discounts, never erases a rule).

```mermaid
flowchart LR
    REV["Reviewer verdict<br/>POST /violations/{id}/feedback"] --> AB["update rule.reliability_alpha / beta"]
    AB --> TH["θ = α/(α+β)"]
    TH --> SCORE["future scoring weights"]
    SCORE -.applied to.-> NEXT["next submission's violations"]
```

Reviewer *document* scores (`reviewer_score`) are stored for **evaluation only** and never
train the scorer.

---

## 6. Data Model

```mermaid
erDiagram
    submissions ||--o{ content_chunks : "chunked into"
    submissions ||--o{ compliance_checks : "graded by"
    compliance_checks ||--o{ violations : "contains"
    rules ||--o{ violations : "grounds"
    rules ||--o{ rules : "superseded_by"

    submissions {
        uuid id PK
        string status "uploaded→analyzing→analyzed/needs_review/failed"
        string product_line
        string jurisdiction
    }
    content_chunks {
        uuid submission_id FK
        int chunk_index
        text text
        jsonb chunk_metadata
    }
    compliance_checks {
        uuid id PK
        float overall_score
        string grade
        jsonb scores
        float reviewer_score "eval only — never trains"
    }
    violations {
        uuid id PK
        uuid rule_id FK
        string severity
        text current_text
        float confidence
        jsonb violation_metadata "grounding: precedent|rule|novel"
        uuid cited_precedent_id
        text cited_comment_verbatim
        int rule_version "snapshot"
        bool suppressed "review lane, not scored"
    }
    rules {
        uuid id PK
        string category
        text rule_text
        int version
        uuid superseded_by FK
        numeric points_deduction
        numeric reliability_alpha
        numeric reliability_beta
    }
```

**Audit-defensibility is baked in:** rules are versioned not mutated (edits create a new row,
old `superseded_by` new); violations snapshot `rule_version` and citation locators
(`cited_section`/`cited_page`/`cited_regulation_version`); suppressed findings persist for review
rather than vanishing.

---

## 7. End-to-End Sequence

```mermaid
sequenceDiagram
    actor U as User
    participant FE as Frontend
    participant API as FastAPI
    participant ENG as ComplianceEngine
    participant G as LangGraph
    participant RAG as RAG
    participant LLM as LLM
    participant DB as Postgres

    U->>FE: paste/upload content
    FE->>API: POST /submissions
    API->>DB: Submission (status=uploaded)
    FE->>API: POST /compliance/analyze/{id}/stream (SSE)
    API->>ENG: analyze_submission(id)
    ENG->>DB: row-lock + claim (status=analyzing)
    ENG->>G: run graph
    G->>G: preprocess (section-aware chunking)
    G->>RAG: dispatch — retrieve rules + precedents
    RAG-->>G: per-chunk evidence (+ degraded flags)
    loop each chunk (parallel)
        G->>LLM: 3-tier grade
        LLM-->>G: precedent/rule/novel findings
        G->>G: sweep → critic → dedup → suppress
    end
    G->>G: scoring (deduction × θ × confidence, critical cap)
    G-->>ENG: final_state
    ENG->>ENG: evaluate_persistability (fail-closed gate)
    alt clean
        ENG->>DB: atomic ComplianceCheck + Violations + status=analyzed
    else degraded
        ENG->>DB: status = needs_review / failed
    end
    API-->>FE: SSE stage/chunk/score/done
    U->>FE: ask question
    FE->>API: POST /chat (fenced, anti-injection)
    API->>RAG: ChatRetriever (rules + chunks + sources + violations)
    API->>LLM: stream grounded answer
    LLM-->>FE: token stream
```

---

## 8. Cross-Cutting Principles

1. **Fail closed everywhere** — RAG degradation, LLM unavailability, rule-load failure,
   schema-validation failure, and any failed chunk all block persistence and route to human
   review. Nothing is silently graded clean.
2. **Evidence-grounded, not model-opinion** — three engines ranked by evidence strength; the
   critic deterministically rejects fabricated quotes.
3. **Pluggable RAG** — protocol abstractions let pgvector → Azure → Pinecone swap by env var.
4. **Audit-traceable** — versioned rules, citation locators, suppressed-finding lane,
   reviewer-score isolation.
5. **Adaptive but bounded** — reviewer feedback tunes rule weights via Beta-Binomial, floored
   so feedback can discount but never erase a rule.

---

## Appendix — Key Source Locations

| Concern | File |
|---------|------|
| Engine entry / fail-closed gate | `backend/app/services/agents/compliance/engine.py` |
| Graph nodes / 3-tier analysis | `backend/app/services/agents/graph/nodes.py` |
| Graph wiring | `backend/app/services/agents/orchestrator.py` |
| State / context | `backend/app/services/agents/graph/state.py`, `graph/context.py` |
| Scoring + reliability | `backend/app/services/agents/compliance/scoring.py`, `reliability.py` |
| Critic | `backend/app/services/agents/compliance/critic.py` |
| RAG ports / factory | `backend/app/services/rag/ports.py`, `rag/factory.py` |
| Vector stores | `backend/app/services/rag/stores/*.py` |
| Retrievers | `backend/app/services/rag/retrievers/*.py` |
| LLM service | `backend/app/services/llm_service.py` |
| Preprocessing / chunking | `backend/app/services/preprocessing_service.py` |
| Rule generation | `backend/app/services/rule_generator_service.py` |
| API routes | `backend/app/api/routes/*.py` |
| Data models | `backend/app/models/*.py` |
