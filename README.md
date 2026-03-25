# Regulatory Compliance Agent

An AI-powered regulatory compliance checking system built with LangGraph, Gemini 2.0 Flash, and FastAPI.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI REST API                          │
│  /submissions  /compliance  /rules  /dashboard              │
└────────────────────┬───────────────────────────────────────-┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│              ComplianceEngine (Orchestrator)                 │
│                                                              │
│  LangGraph Workflow:                                         │
│  START → preprocess → dispatch → analysis → scoring → END    │
│                          ↑                                   │
│                    interrupt_before                          │
│                   (HITL Review Point)                        │
└────────────────────┬────────────────────────────────────────┘
                     │
     ┌───────────────┼───────────────────┐
     ▼               ▼                   ▼
┌─────────┐   ┌──────────────┐   ┌───────────────┐
│Preprocess│   │RuleGenerator  │   │StandardAgent  │
│  Node    │   │(dispatch node)│   │(analysis node)│
│(Librarian│   │  (Teacher)   │   │  per category │
└─────────┘   └──────────────┘   └───────────────┘
     │               │                   │
     ▼               ▼                   ▼
┌─────────┐   ┌──────────────┐   ┌───────────────┐
│ContentChunk│  │  Rule (DB)   │   │ LLM Service   │
│ (Chunked) │  │  active rules│   │(Gemini Flash) │
└─────────┘   └──────────────┘   └───────────────┘
```

## Core Components

| Component | Description |
|-----------|-------------|
| `ComplianceEngine` | Main orchestrator, entry point for analysis |
| `ScoringService` | Calculates compliance scores (0-100) and grades (A-F) |
| `StandardComplianceAgent` | LLM-powered per-category analysis agent |
| `ContextEngineeringService` | Document chunking and prompt construction |
| `RuleGeneratorService` | Manages rules and extracts them from documents |
| `ComplianceOrchestrator` | LangGraph state machine manager |

## Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL 15+
- Redis 7+ (optional, falls back to in-memory)
- Google Gemini API key

### 1. Setup Environment

```bash
cd backend
cp .env.example .env
# Edit .env and add your LLM_API_KEY (Google Gemini API key)
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Start with Docker Compose (Recommended)

```bash
# Add your API key to the environment
export LLM_API_KEY=your_google_gemini_api_key

docker-compose up -d
```

### 4. Run Locally (without Docker)

```bash
# Start PostgreSQL and Redis separately, then:
cd backend
uvicorn app.main:app --reload --port 8000
```

### 5. Access the API

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## API Endpoints

### Submissions
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/submissions/` | Create submission (text or file upload) |
| GET | `/submissions/` | List all submissions |
| GET | `/submissions/{id}` | Get specific submission |
| DELETE | `/submissions/{id}` | Delete submission |

### Compliance Analysis
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/compliance/analyze/{id}` | Trigger async analysis |
| POST | `/compliance/analyze/{id}/sync` | Trigger sync analysis |
| GET | `/compliance/results/{id}` | Get analysis results |
| GET | `/compliance/check/{check_id}` | Get check details |
| POST | `/compliance/resume/{id}` | Resume HITL workflow |

### Rules Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/rules/` | Create rule manually |
| GET | `/rules/` | List all rules |
| PATCH | `/rules/{id}` | Update rule |
| DELETE | `/rules/{id}` | Delete rule |
| POST | `/rules/generate-from-document` | AI rule generation from document |

### Dashboard
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dashboard/summary` | Overall stats |
| GET | `/dashboard/violations-by-category` | Violations by category |
| GET | `/dashboard/violations-by-severity` | Violations by severity |

## Typical Workflow

1. **Create Rules**: Add compliance rules either manually or generate from regulatory documents
2. **Submit Document**: POST to `/submissions/` with your content
3. **Run Analysis**: POST to `/compliance/analyze/{id}/sync`
4. **Review Results**: GET `/compliance/results/{id}` for violations and scores

## Example: Submit and Analyze

```bash
# 1. Create a submission
curl -X POST "http://localhost:8000/submissions/" \
  -F "title=Insurance Policy Draft" \
  -F "content_type=text" \
  -F "content=This policy provides coverage for life insurance..."

# 2. Analyze it (replace {id} with submission ID from step 1)
curl -X POST "http://localhost:8000/compliance/analyze/{id}/sync"

# 3. Get results
curl "http://localhost:8000/compliance/results/{id}"
```

## LangGraph Workflow

The compliance analysis uses a 5-node LangGraph pipeline:

1. **preprocess_node** (Librarian): Chunks the document into token-limited segments
2. **dispatch_node** (Brain): Loads active rules grouped by category
3. **analysis_node** (Specialist): Runs LLM analysis on each chunk×category in parallel
4. **scoring_node** (Evaluator): Calculates scores and grades
5. **refinement_node** (HITL): Human review checkpoint (graph pauses here for review)

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_API_KEY` | Google Gemini API key | Required |
| `LLM_MODEL` | LLM model to use | `gemini-2.0-flash` |
| `DATABASE_URL` | PostgreSQL connection URL | Auto-constructed |
| `REDIS_URL` | Redis URL for LangGraph persistence | `redis://localhost:6379` |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing | `false` |
| `LANGCHAIN_API_KEY` | LangSmith API key | Optional |

## Design Principles

This implementation follows **12-Factor Agent** principles:
- **Stateless Reducer Pattern**: `State_n+1 = f(State_n, Input)`
- **Small, Focused Agents**: Each agent handles exactly one rule category
- **Immutable Observability**: Every agent action is traced and logged
- **Parallel Execution**: chunks × categories analyzed concurrently
