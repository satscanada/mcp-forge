# UI_BUILD.md — MCP Forge Web UI

**Phase 5 build specification for the React/Vite/Tailwind frontend**

This document is the complete implementation guide for the MCP Forge web UI.
It covers project setup, component architecture, FastAPI backend bridge,
state management, and every screen in the pipeline flow.

---

## 1. What We Are Building

A single-page web application that wraps the existing CLI pipeline in a polished UI,
matching the workflow of MCP Blacksmith:

```
Upload Spec → View Spec → Validate → Configure (Auth + Rate Limit) → Generate → Download
```

The UI does NOT replace the Python scripts. It is a thin orchestration layer that:
1. Accepts a spec file upload
2. Calls a FastAPI backend that invokes the existing Python scripts
3. Streams results back to the browser in real time via Server-Sent Events (SSE)
4. Presents the generated file tree and allows download as a ZIP

---

## 2. Technology Stack

| Layer | Choice | Reason |
|---|---|---|
| Frontend framework | React 18 + Vite | Fast HMR, modern tooling |
| Styling | Tailwind CSS v3 | Utility-first, dark theme trivial |
| UI components | shadcn/ui | Accessible, unstyled base, Tailwind-compatible |
| Icons | lucide-react | Already in shadcn/ui stack |
| Syntax highlighting | @uiw/react-codemirror + yaml lang | Spec viewer with line numbers |
| State management | Zustand | Lightweight, no boilerplate |
| HTTP client | axios | Multipart upload + SSE |
| SSE streaming | native EventSource API | No extra dep needed |
| Code diff / file viewer | @uiw/react-codemirror | Same dep, reuse for generated files |
| Router | react-router-dom v6 | Single route really, but needed for tab state |
| Backend bridge | FastAPI (Python) | Thin wrapper around existing scripts |
| Backend SSE | fastapi + sse-starlette | Stream generation logs |

---

## 3. Project Structure

```
mcp-forge/
├── ui/                          ← all frontend code lives here
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── package.json
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx              ← root, top nav, pipeline stepper
│   │   ├── store.ts             ← Zustand global state
│   │   ├── api.ts               ← all axios + SSE calls
│   │   ├── types.ts             ← shared TypeScript interfaces
│   │   ├── components/
│   │   │   ├── layout/
│   │   │   │   ├── TopNav.tsx
│   │   │   │   └── PipelineStepper.tsx
│   │   │   ├── upload/
│   │   │   │   ├── UploadPane.tsx
│   │   │   │   └── DropZone.tsx
│   │   │   ├── viewer/
│   │   │   │   ├── SpecViewer.tsx
│   │   │   │   ├── OperationsList.tsx
│   │   │   │   └── ProblemsPanel.tsx
│   │   │   ├── validation/
│   │   │   │   ├── ValidationPane.tsx
│   │   │   │   ├── ValidationResult.tsx
│   │   │   │   └── IssueRow.tsx
│   │   │   ├── config/
│   │   │   │   ├── ConfigPane.tsx
│   │   │   │   ├── AuthConfig.tsx
│   │   │   │   └── RateLimitConfig.tsx
│   │   │   ├── generation/
│   │   │   │   ├── GenerationPane.tsx
│   │   │   │   ├── ConsoleOutput.tsx
│   │   │   │   ├── ProgressStages.tsx
│   │   │   │   └── FileTreePreview.tsx
│   │   │   └── download/
│   │   │       └── DownloadPane.tsx
│   │   └── lib/
│   │       ├── utils.ts
│   │       └── sse.ts           ← SSE client helper
├── api_server/                  ← FastAPI backend bridge
│   ├── main.py                  ← FastAPI app
│   ├── routers/
│   │   ├── upload.py
│   │   ├── validate.py
│   │   ├── generate.py
│   │   └── download.py
│   └── services/
│       ├── spec_service.py      ← wraps validate_spec.py functions
│       ├── gen_service.py       ← wraps generate_server.py functions
│       └── session_service.py  ← temp file management per session
└── scripts/                     ← existing CLI scripts (unchanged)
    ├── validate_spec.py
    ├── generate_server.py
    └── forge.py
```

---

## 4. Visual Design

### Theme
- **Dark only** — `#0d0d0f` background (near-black), not pure black
- Accent: `#f97316` (orange-500) — matches the forge/fire metaphor
- Secondary accent: `#22d3ee` (cyan-400) — used for active states, links
- Success: `#4ade80` (green-400)
- Error: `#f87171` (red-400)
- Warning: `#fbbf24` (amber-400)
- Card surfaces: `#18181b` (zinc-900)
- Borders: `#27272a` (zinc-800)
- Muted text: `#71717a` (zinc-500)

### Typography
- Font: `JetBrains Mono` for all code/spec/console content
- Font: `Inter` for UI chrome (nav, labels, buttons)
- Import both via `@fontsource/jetbrains-mono` and `@fontsource/inter`

### Key UI patterns
- All panels follow a two-column layout: **left = config/controls**, **right = console/output**
- This mirrors MCP Blacksmith exactly and feels natural for a pipeline tool
- Use `tabs` for sub-sections within a panel (e.g. Config / Tools / Auth tabs in Generation)
- The pipeline stepper at the top is always visible, showing which step is active/complete

---

## 5. Global State (Zustand — `store.ts`)

```typescript
// src/store.ts
import { create } from 'zustand'
import type { SpecMeta, ValidationResult, GenConfig, GenerationState, GeneratedFile } from './types'

interface ForgeStore {
  // ── Step tracking ──────────────────────────────────────────
  activeStep: 'upload' | 'viewer' | 'validation' | 'config' | 'generation' | 'download'
  setStep: (step: ForgeStore['activeStep']) => void

  // ── Session ────────────────────────────────────────────────
  sessionId: string | null               // backend temp dir key
  setSessionId: (id: string) => void

  // ── Spec ──────────────────────────────────────────────────
  specFile: File | null
  specRaw: string                        // raw YAML/JSON text for viewer
  specMeta: SpecMeta | null              // parsed: title, version, op count, etc
  setSpec: (file: File, raw: string, meta: SpecMeta) => void

  // ── Validation ────────────────────────────────────────────
  validationResult: ValidationResult | null
  strictMode: boolean
  setValidationResult: (r: ValidationResult) => void
  setStrictMode: (v: boolean) => void

  // ── Generation config ─────────────────────────────────────
  genConfig: GenConfig
  setGenConfig: (partial: Partial<GenConfig>) => void

  // ── Generation state ──────────────────────────────────────
  generationState: GenerationState
  consoleLines: ConsoleLine[]
  generatedFiles: GeneratedFile[]
  appendConsoleLine: (line: ConsoleLine) => void
  setGeneratedFiles: (files: GeneratedFile[]) => void
  setGenerationState: (state: GenerationState) => void

  // ── Reset ─────────────────────────────────────────────────
  reset: () => void
}
```

---

## 6. TypeScript Interfaces (`types.ts`)

```typescript
// src/types.ts

export interface SpecMeta {
  title: string
  version: string
  openapi: string
  description: string
  operationCount: number
  pathCount: number
  authSchemes: string[]          // e.g. ['apiKeyAuth']
  servers: string[]
}

export interface ValidationIssue {
  level: 'error' | 'warning'
  message: string
  path: string
  fix: string
}

export interface ValidationResult {
  canGenerate: boolean
  structuralErrors: ValidationIssue[]
  qualityIssues: ValidationIssue[]
  elapsedMs: number
}

export interface Operation {
  operationId: string
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS'
  path: string
  summary: string
  description: string
  tags: string[]
  paramCount: number
  hasBody: boolean
  requiresAuth: boolean
  selected: boolean              // for tool selection in config
}

export interface GenConfig {
  serverName: string
  forceApiKey: boolean           // inject API Key auth even if not in spec
  apiKeyHeader: string           // default: 'X-API-Key'
  apiKeyLocation: 'header' | 'query' | 'cookie'
  rateLimitRps: number           // default: 10
  maxRetries: number             // default: 3
  circuitBreakerThreshold: number// default: 5
  responseValidationMode: 'off' | 'warn' | 'strict'
  sanitizationLevel: 'DISABLED' | 'LOW' | 'MEDIUM' | 'HIGH'
  selectedOperationIds: string[] // empty = all selected
}

export type GenerationStage =
  | 'idle'
  | 'schema_validation'
  | 'metadata_filter'
  | 'code_generation'
  | 'complete'
  | 'error'

export interface GenerationState {
  status: 'idle' | 'running' | 'complete' | 'error'
  currentStage: GenerationStage
  stages: StageResult[]
  totalElapsedMs: number
}

export interface StageResult {
  name: string
  status: 'pending' | 'running' | 'done' | 'error'
  elapsedMs: number | null
  summary: string
}

export type ConsoleLine = {
  type: 'info' | 'success' | 'error' | 'warning' | 'dim'
  text: string
  ts: number
}

export interface GeneratedFile {
  name: string
  content: string
  sizeBytes: number
  language: 'python' | 'yaml' | 'json' | 'dockerfile' | 'text' | 'env'
}
```

---

## 7. Backend: FastAPI Bridge (`api_server/main.py`)

The FastAPI server is a thin adapter. It imports the existing Python script functions
directly (not via subprocess) for speed, and exposes a clean REST API.

### Install

```bash
pip install fastapi uvicorn sse-starlette python-multipart aiofiles
```

### Run

```bash
uvicorn api_server.main:app --reload --port 8001
```

### Endpoints

```
POST   /api/upload              Upload spec file → returns session_id + spec_meta
POST   /api/validate            Run validation → returns ValidationResult
GET    /api/validate/stream     SSE stream of validation log lines
POST   /api/generate            Start generation (async) → returns job_id
GET    /api/generate/stream     SSE stream of generation progress
GET    /api/session/{id}/files  List generated files with metadata
GET    /api/session/{id}/file/{name}  Get single file content
GET    /api/session/{id}/download  Download ZIP of all generated files
DELETE /api/session/{id}        Clean up temp files
```

### `api_server/main.py` — full implementation sketch

```python
# api_server/main.py
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse
import asyncio, json, shutil, tempfile, uuid
from pathlib import Path

# Import existing script functions directly
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from validate_spec import load_spec, validate_structure, check_quality
from generate_server import generate, extract_operations, detect_auth, slugify

app = FastAPI(title="MCP Forge API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: dict[str, dict] = {}   # in-memory session store
TEMP_ROOT = Path(tempfile.gettempdir()) / "mcp_forge_sessions"
TEMP_ROOT.mkdir(exist_ok=True)


# ── Upload ──────────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_spec(file: UploadFile = File(...)):
    """Accept a YAML or JSON spec file. Returns session_id and parsed spec metadata."""
    if not file.filename.endswith((".yaml", ".yml", ".json")):
        raise HTTPException(400, "File must be .yaml, .yml, or .json")

    session_id = str(uuid.uuid4())
    session_dir = TEMP_ROOT / session_id
    session_dir.mkdir()

    spec_path = session_dir / file.filename
    content = await file.read()
    spec_path.write_bytes(content)

    try:
        spec = load_spec(spec_path)
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(422, f"Cannot parse spec: {e}")

    info = spec.get("info", {})
    ops  = extract_operations(spec)
    auth = detect_auth(spec)
    servers = [s.get("url", "") for s in spec.get("servers", [])]

    meta = {
        "title":          info.get("title", ""),
        "version":        info.get("version", ""),
        "openapi":        spec.get("openapi", ""),
        "description":    info.get("description", ""),
        "operationCount": len(ops),
        "pathCount":      len(spec.get("paths", {})),
        "authSchemes":    list(auth["schemes"].keys()),
        "servers":        servers,
    }

    SESSIONS[session_id] = {
        "spec_path": str(spec_path),
        "spec_raw":  content.decode("utf-8"),
        "meta":      meta,
        "ops":       ops,
        "output_dir": None,
    }

    return {"session_id": session_id, "meta": meta, "spec_raw": content.decode("utf-8")}


# ── Validate ────────────────────────────────────────────────────────────────

@app.post("/api/validate")
async def validate(session_id: str, strict: bool = False):
    """Run full validation. Returns structured ValidationResult."""
    session = _get_session(session_id)
    spec = load_spec(Path(session["spec_path"]))

    import time
    t0 = time.monotonic()
    struct_errors  = validate_structure(spec)
    quality_issues = check_quality(spec)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    blocking = struct_errors + [i for i in quality_issues if i["level"] == "error"]
    if strict:
        blocking += [i for i in quality_issues if i["level"] == "warning"]

    return {
        "canGenerate":       len(blocking) == 0,
        "structuralErrors":  struct_errors,
        "qualityIssues":     quality_issues,
        "elapsedMs":         elapsed_ms,
    }


# ── Generate ────────────────────────────────────────────────────────────────

@app.get("/api/generate/stream")
async def generate_stream(session_id: str, body: str):
    """
    SSE endpoint. Accepts config as JSON query param.
    Streams progress events while running generation.
    Event format:  data: {"type": "stage"|"log"|"complete"|"error", ...}
    """
    config = json.loads(body)
    session = _get_session(session_id)

    async def event_generator():
        spec_path  = Path(session["spec_path"])
        output_dir = TEMP_ROOT / session_id / "output"
        output_dir.mkdir(exist_ok=True)

        server_name = config.get("serverName") or slugify(session["meta"]["title"])
        force_api_key = config.get("forceApiKey", False)

        SESSIONS[session_id]["output_dir"] = str(output_dir)

        # Stage 1: schema validation
        yield _sse("stage", {"name": "Schema Validation", "status": "running"})
        await asyncio.sleep(0)
        struct_errors = validate_structure(load_spec(spec_path))
        if struct_errors:
            yield _sse("stage", {"name": "Schema Validation", "status": "error",
                                  "summary": f"{len(struct_errors)} structural errors"})
            yield _sse("error", {"message": "Schema validation failed"})
            return
        yield _sse("stage", {"name": "Schema Validation", "status": "done",
                              "summary": "Specification is structurally valid"})
        yield _sse("log", {"type": "success", "text": "✓ Schema validation passed"})

        # Stage 2: generation
        yield _sse("stage", {"name": "Code Generation", "status": "running"})
        await asyncio.sleep(0)
        try:
            # Apply config overrides to environment before generating
            import os
            os.environ["RATE_LIMIT_REQUESTS_PER_SECOND"] = str(config.get("rateLimitRps", 10))
            os.environ["MAX_RETRIES"]                    = str(config.get("maxRetries", 3))
            os.environ["CIRCUIT_BREAKER_FAILURE_THRESHOLD"] = str(config.get("circuitBreakerThreshold", 5))
            os.environ["RESPONSE_VALIDATION_MODE"]       = config.get("responseValidationMode", "warn")
            os.environ["SANITIZATION_LEVEL"]             = config.get("sanitizationLevel", "DISABLED")

            generate(spec_path, output_dir, server_name, force_api_key)

            files = list(output_dir.iterdir())
            yield _sse("stage", {"name": "Code Generation", "status": "done",
                                  "summary": f"{len(files)} files generated"})
            yield _sse("log", {"type": "success", "text": f"✓ Generated {len(files)} files"})

            # Emit file list
            file_list = []
            for f in files:
                file_list.append({
                    "name":      f.name,
                    "sizeBytes": f.stat().st_size,
                    "language":  _detect_language(f.name),
                })
            yield _sse("complete", {"files": file_list, "serverName": server_name})

        except Exception as e:
            yield _sse("stage", {"name": "Code Generation", "status": "error"})
            yield _sse("error", {"message": str(e)})

    return EventSourceResponse(event_generator())


def _sse(event_type: str, data: dict) -> dict:
    return {"event": event_type, "data": json.dumps(data)}

def _detect_language(filename: str) -> str:
    ext_map = {".py": "python", ".yaml": "yaml", ".yml": "yaml",
               ".json": "json", ".md": "text", ".txt": "text"}
    if filename == "Dockerfile": return "dockerfile"
    if filename == ".env":       return "env"
    return ext_map.get(Path(filename).suffix, "text")

def _get_session(session_id: str) -> dict:
    if session_id not in SESSIONS:
        raise HTTPException(404, f"Session not found: {session_id}")
    return SESSIONS[session_id]


# ── File access ─────────────────────────────────────────────────────────────

@app.get("/api/session/{session_id}/files")
async def list_files(session_id: str):
    session = _get_session(session_id)
    output_dir = session.get("output_dir")
    if not output_dir or not Path(output_dir).exists():
        return {"files": []}
    files = []
    for f in sorted(Path(output_dir).iterdir()):
        files.append({
            "name":      f.name,
            "sizeBytes": f.stat().st_size,
            "language":  _detect_language(f.name),
            "content":   f.read_text(errors="replace"),
        })
    return {"files": files}


@app.get("/api/session/{session_id}/download")
async def download_zip(session_id: str):
    session = _get_session(session_id)
    output_dir = Path(session["output_dir"])
    zip_path = TEMP_ROOT / session_id / f"{session_id}.zip"
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", output_dir)
    return FileResponse(zip_path, filename=f"mcp_server_{session_id[:8]}.zip",
                        media_type="application/zip")


@app.delete("/api/session/{session_id}")
async def cleanup(session_id: str):
    if session_id in SESSIONS:
        del SESSIONS[session_id]
    shutil.rmtree(TEMP_ROOT / session_id, ignore_errors=True)
    return {"deleted": session_id}
```

---

## 8. Frontend API Client (`src/api.ts`)

```typescript
// src/api.ts
import axios from 'axios'
import type { ValidationResult, GenConfig, GeneratedFile } from './types'

const BASE = 'http://localhost:8001/api'

export const api = {
  async upload(file: File) {
    const form = new FormData()
    form.append('file', file)
    const { data } = await axios.post(`${BASE}/upload`, form)
    return data  // { session_id, meta, spec_raw }
  },

  async validate(sessionId: string, strict: boolean): Promise<ValidationResult> {
    const { data } = await axios.post(`${BASE}/validate`, null, {
      params: { session_id: sessionId, strict }
    })
    return data
  },

  streamGenerate(sessionId: string, config: GenConfig, handlers: {
    onStage: (name: string, status: string, summary?: string) => void
    onLog:   (type: string, text: string) => void
    onComplete: (files: GeneratedFile[], serverName: string) => void
    onError: (message: string) => void
  }) {
    const url = `${BASE}/generate/stream?session_id=${sessionId}&body=${
      encodeURIComponent(JSON.stringify(config))
    }`
    const es = new EventSource(url)

    es.addEventListener('stage',    e => handlers.onStage(...JSON.parse(e.data)))
    es.addEventListener('log',      e => { const d = JSON.parse(e.data); handlers.onLog(d.type, d.text) })
    es.addEventListener('complete', e => { const d = JSON.parse(e.data); handlers.onComplete(d.files, d.serverName); es.close() })
    es.addEventListener('error',    e => { const d = JSON.parse(e.data); handlers.onError(d.message); es.close() })

    return () => es.close()  // cleanup fn
  },

  async getFiles(sessionId: string) {
    const { data } = await axios.get(`${BASE}/session/${sessionId}/files`)
    return data.files as GeneratedFile[]
  },

  downloadUrl(sessionId: string) {
    return `${BASE}/session/${sessionId}/download`
  },

  async cleanup(sessionId: string) {
    await axios.delete(`${BASE}/session/${sessionId}`)
  }
}
```

---

## 9. Screen-by-Screen Component Spec

### 9.1 Layout — `App.tsx`

```
┌─────────────────────────────────────────────────────────────┐
│  🔥 MCP Forge                              [GitHub] [Docs]  │  ← TopNav
├─────────────────────────────────────────────────────────────┤
│  ① Upload  ──●──  ② Viewer  ──●──  ③ Validate  ──●──  ④ Config  ──●──  ⑤ Generate  │  ← PipelineStepper
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                   [active panel]                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

The `PipelineStepper` always shows all 5 steps. Completed steps show a green check.
The active step is highlighted orange. Future steps are greyed out.

Clicking a completed step navigates back to it. Clicking a future step is disabled.

---

### 9.2 Step 1 — Upload (`UploadPane.tsx`)

**Left panel (full-width on this step):**

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│         ┌───────────────────────────────────┐              │
│         │                                   │              │
│         │   📄  Drop your OpenAPI spec here  │              │
│         │                                   │              │
│         │   YAML or JSON  •  OAS 3.0 / 3.1  │              │
│         │                                   │              │
│         │   [  Browse file  ]               │              │
│         │                                   │              │
│         └───────────────────────────────────┘              │
│                                                             │
│   ─── or try a sample ─────────────────────────────────    │
│                                                             │
│   [Banking API]   [Petstore]   [Custom YAML paste]         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Behaviour:**
- `DropZone.tsx` handles drag-and-drop and click-to-browse
- On drop/select: immediately POST `/api/upload`, show spinner
- On success: populate `specFile`, `specRaw`, `specMeta` in store, advance to Step 2
- On error: show inline error banner, allow retry
- **Sample buttons** load the bundled `examples/banking_api.yaml` from the public folder
- **Custom YAML paste** opens a `<Textarea>` — user pastes raw YAML, treated as a virtual file upload

**State touched:** `sessionId`, `specFile`, `specRaw`, `specMeta`, `activeStep`

---

### 9.3 Step 2 — Viewer (`SpecViewer.tsx` + `OperationsList.tsx`)

Two-column layout:

```
┌─────────────────────────────────┬───────────────────────────┐
│  Viewer  |  Operations  |  Raw  │  PROBLEMS  (0 errors)     │
├─────────────────────────────────┤                           │
│                                 │  [no issues yet]          │
│  Title: Retail Banking API      │                           │
│  Version: 1.0.0  •  OAS 3.0.3  │                           │
│  Ops: 10  •  Auth: apiKeyAuth   │                           │
│                                 │                           │
│  [Viewer tab — CodeMirror YAML] │                           │
│                                 │                           │
│  OR [Operations tab]:           │                           │
│  GET  /accounts          ✓ auth │                           │
│  GET  /accounts/{id}     ✓ auth │                           │
│  ...                            │                           │
└─────────────────────────────────┴───────────────────────────┘
                                   [→ Validate Spec]
```

**Tabs:**
- **Viewer** — `@uiw/react-codemirror` with YAML language, read-only, line numbers,
  dark theme (`vscodeDark`), highlights `operationId`, `$ref`, `securitySchemes`
- **Operations** — table of all operations: method badge (coloured by verb),
  path, operationId, summary, param count, auth indicator
- **Raw** — plain `<pre>` with the raw text, copyable

**Right panel — Problems:**
- Empty state: `No issues — run validation to check`
- After validation: populated with errors/warnings from `ValidationResult`
- Clicking an issue jumps the CodeMirror view to the relevant path (bonus feature)

**State touched:** read-only on this step

---

### 9.4 Step 3 — Validation (`ValidationPane.tsx`)

Two-column layout:

```
┌────────────────────────────┬────────────────────────────────┐
│  Config                    │  Console                       │
│                            │                                │
│  Mode                      │  ✓ Validation completed  <1s  │
│  [Standard ▼]              │                                │
│                            │  ────────────────────────────  │
│  ☑ Include OWASP rules     │  ✓ Schema validation passed   │
│                            │  ✓ No duplicate operationIds  │
│  ─────────────────────     │  ✓ All $refs resolved         │
│                            │  ⚠  Missing info.description  │
│  [ Validate Specification ]│                                │
│                            │  ────────────────────────────  │
│                            │  PASS  •  0 errors  •  1 warn  │
│                            │                                │
│                            │  [ View Issues in Viewer → ]  │
└────────────────────────────┴────────────────────────────────┘
                              [← Back]     [→ Configure →]
```

**Left panel controls:**
- Mode dropdown: `Standard` | `Strict` (maps to `--strict` flag)
- `Include OWASP security rules` checkbox (future: vacuum integration)
- `Validate Specification` button — triggers POST `/api/validate`

**Right panel console:**
- Renders `ValidationResult` as a structured log
- Each structural error: red `✗` row
- Each quality issue: colour-coded by level
- Summary bar: green PASS or red FAIL with counts
- `View Issues in Viewer` button → navigates back to Step 2, opens Problems panel

**Issue rows (`IssueRow.tsx`):**
```
┌──────────────────────────────────────────────────────────┐
│  ⚠  Missing info.description                 [expand ▾] │
│     path: info.description                               │
│     fix:  Add a description explaining what the API does │
└──────────────────────────────────────────────────────────┘
```

Expandable — shows `path` and `fix` hint when opened.

**Advance rule:** `→ Configure` button is enabled only if `canGenerate === true`.
Show a tooltip if disabled: `Fix blocking errors before configuring`.

**State touched:** `validationResult`, `strictMode`

---

### 9.5 Step 4 — Configure (`ConfigPane.tsx`)

Two-column layout with **three sub-tabs**: Config | Tools | Auth

```
┌────────────────────────────┬────────────────────────────────┐
│  Config  |  Tools  |  Auth │  Console                       │
│                            │                                │
│  [Config tab:]             │  ✓ Schema validation completed │
│                            │  ✓ Metadata Filter completed   │
│  Server Name               │  ✓ Generation completed        │
│  [banking_server_______]   │                                │
│                            │  Tools: 10                     │
│  ─── Security ──────────   │  Params: 3.2 avg, 8 max       │
│                            │  Auth: API Key                 │
│  ☑ Enable Rate Limiting    │  12 tools with auth, 0 public  │
│    Requests/sec: [10 ___]  │                                │
│                            │                                │
│  ☐ Enable mTLS             │                                │
│                            │                                │
│  ─── Resilience ─────────  │                                │
│                            │                                │
│  Max Retries:   [3 _____]  │                                │
│  Circuit Breaker: [5 ___]  │                                │
│  Response Validation:      │                                │
│  [warn ▼]                  │                                │
│  Sanitization:             │                                │
│  [DISABLED ▼]              │                                │
└────────────────────────────┴────────────────────────────────┘
                              [← Back]    [Generate Server →]
```

**Config tab (`ConfigPane.tsx`):**
- Server Name text input (pre-filled from spec title via `slugify`)
- Rate Limiting toggle + `Requests/sec` number input (shows when enabled)
- mTLS toggle (Phase 3 — show as disabled/coming soon)
- Max Retries number input (1–10)
- Circuit Breaker Threshold number input (1–20)
- Response Validation Mode select: `off` | `warn` | `strict`
- Sanitization Level select: `DISABLED` | `LOW` | `MEDIUM` | `HIGH`

**Tools tab (`OperationsList.tsx` reused):**
- Same operation table as Viewer, but each row has a checkbox
- `Select All` / `Deselect All` controls
- Selected count shown: `10 / 10 tools selected`
- Deselected operations are excluded from generation (future — Phase 2)

**Auth tab (`AuthConfig.tsx`):**
```
┌──────────────────────────────────────────────────────────┐
│  Name                                          [×]       │
│  [_________________________]                             │
│                                                          │
│  Type              Scheme                                │
│  [HTTP (Bearer/Basic) ▼]   [Bearer Token ▼]             │
│                                                          │
│  Operations                                              │
│  ☐ Select All                                            │
│  ☐ listAccounts                                          │
│  ☐ getAccount                                            │
│  ☐ getTransactions                                       │
│  ...                                                     │
│                                                          │
│  + Add auth configuration                                │
└──────────────────────────────────────────────────────────┘
```

- Pre-populated from `securitySchemes` detected in spec
- Supports: API Key (header/query/cookie), Bearer Token (Phase 1)
- `Force API Key` toggle: inject API Key auth even if not declared in spec

**Estimated cost section:**
```
Estimated cost:  $0.00   ← always $0.00 (local, no LLM enhancement)
Actual cost:     $0.00
```

**State touched:** `genConfig`

---

### 9.6 Step 5 — Generation (`GenerationPane.tsx`)

Two-column layout:

```
┌────────────────────────────┬────────────────────────────────┐
│  Config  |  Tools  |  Auth │  Console                       │
│                            │                                │
│  Server Name: banking_srv  │  ✓ Schema validation completed │
│  LLM Model: (local gen)    │     <1s                        │
│                            │                                │
│  Enhancement Passes        │  ✓ Metadata Filter completed   │
│  ☑ Metadata Filter         │     <1s                        │
│    ☑ Filter Parameters     │     No parameters excluded     │
│    ☑ Filter Operations     │                                │
│  ☐ Parameter Filter        │  ✓ Generation completed        │
│  ☐ Parameter Consolidator  │     1.3s                       │
│  ☐ Tool Enhancer           │                                │
│                            │  Tools                         │
│  ─── Advanced ───────────  │  10 MCP tools                  │
│  Security                  │  Parameters: 3.2 avg, 8 max   │
│  ☑ Enable Rate Limiting    │                                │
│  ☐ Enable mTLS             │  Authentication                │
│                            │  Type: API Key                 │
│  Estimated cost: $0.00     │  10 tools with auth, 0 public  │
│  Actual cost:    $0.00     │                                │
│                            │  ┌──────────────────────────┐ │
│  [ Generate server ]       │  │  ⬇  Download Server      │ │
│                            │  └──────────────────────────┘ │
└────────────────────────────┴────────────────────────────────┘
```

**Left panel:**
- Read-only summary of the config set in Step 4
- Enhancement passes checkboxes (visual only for Phase 1 — Metadata Filter always on,
  others are greyed out "Phase 2" with a badge)
- `Generate server` button (orange, prominent) — triggers SSE stream

**Right panel — Console (`ConsoleOutput.tsx`):**
- Live streaming log output from the SSE endpoint
- Each line has a timestamp and type-based colour
- Auto-scrolls to bottom on new lines
- `ProgressStages.tsx` shows the stage pipeline above the log:
  ```
  ✓ Schema Validation  ──●──  ✓ Code Generation  ──●──  ⊙ Complete
  ```

**After completion — `FileTreePreview.tsx`:**
```
  Generated files (9):

  🐍  server.py          18 KB    [Preview]
  🐍  _auth.py            3 KB    [Preview]
  🐍  _models.py          3 KB    [Preview]
  🐍  _validators.py      6 KB    [Preview]
  ⚙   .env                3 KB    [Preview]
  📄  requirements.txt   95  B    [Preview]
  🐳  Dockerfile         387  B   [Preview]
  {}  .mcp.json          127  B   [Preview]
  📝  README.md            2 KB   [Preview]

  ┌──────────────────────────────────────────────┐
  │  ⬇  Download Server (ZIP)                    │
  └──────────────────────────────────────────────┘
```

Clicking `[Preview]` opens a modal with CodeMirror viewer for that file.

---

### 9.7 File Preview Modal

```
┌──────────────────────────────────────────────────────────────┐
│  server.py                                        [×  Close] │
├──────────────────────────────────────────────────────────────┤
│  1   #!/usr/bin/env python3                                  │
│  2   """                                                     │
│  3   banking_server MCP Server                               │
│  ...                                                         │
├──────────────────────────────────────────────────────────────┤
│  [Copy]                              18,432 bytes            │
└──────────────────────────────────────────────────────────────┘
```

- Full-height modal (90vh)
- CodeMirror in the relevant language (Python, YAML, JSON, etc.)
- Copy button copies to clipboard
- No editing — read-only

---

## 10. Component Implementation Details

### `PipelineStepper.tsx`

```tsx
const STEPS = [
  { id: 'upload',     label: 'Upload',     icon: UploadCloud },
  { id: 'viewer',     label: 'Viewer',     icon: FileText    },
  { id: 'validation', label: 'Validation', icon: ShieldCheck },
  { id: 'config',     label: 'Configure',  icon: Settings    },
  { id: 'generation', label: 'Generate',   icon: Zap         },
]

// Step is: 'completed' | 'active' | 'upcoming'
// completed → green ring + checkmark
// active    → orange ring + step number
// upcoming  → zinc ring + step number (pointer-events-none)
```

### `ConsoleOutput.tsx`

```tsx
// Line type → Tailwind class map
const LINE_COLOURS = {
  success: 'text-green-400',
  error:   'text-red-400',
  warning: 'text-amber-400',
  info:    'text-cyan-400',
  dim:     'text-zinc-500',
}

// Auto-scroll hook
useEffect(() => {
  containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight, behavior: 'smooth' })
}, [lines])
```

### `DropZone.tsx`

```tsx
// Uses HTML5 drag events + react-dropzone (or custom)
// Accepted MIME types: .yaml, .yml, .json
// Visual states: idle | dragging-over | uploading | success | error
// On success: green border pulse animation, show filename + size
```

### `AuthConfig.tsx`

```tsx
// Phase 1: only API Key type is fully wired
// Phase 2+: Bearer, OAuth2 rows shown but disabled with "Coming in Phase 3" badge

const AUTH_TYPES = [
  { value: 'apiKey',  label: 'API Key',              available: true  },
  { value: 'bearer',  label: 'HTTP (Bearer / Basic)', available: false },
  { value: 'oauth2',  label: 'OAuth 2.0',             available: false },
  { value: 'jwt',     label: 'JWT',                   available: false },
  { value: 'mtls',    label: 'Mutual TLS (mTLS)',      available: false },
]
```

---

## 11. Setup Commands

### Frontend

```bash
cd mcp-forge/ui

# Scaffold
npm create vite@latest . -- --template react-ts

# Core deps
npm install zustand axios react-router-dom lucide-react

# Tailwind
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p

# shadcn/ui
npx shadcn@latest init
# When prompted: Dark theme, zinc base colour, CSS variables: yes

# shadcn components needed
npx shadcn@latest add button card tabs badge input label select
npx shadcn@latest add dialog progress separator tooltip switch

# CodeMirror
npm install @uiw/react-codemirror @codemirror/lang-yaml @codemirror/lang-python
npm install @codemirror/lang-json @codemirror/theme-one-dark

# Fonts
npm install @fontsource/inter @fontsource/jetbrains-mono
```

### `tailwind.config.ts`

```ts
import type { Config } from 'tailwindcss'

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        forge: {
          bg:      '#0d0d0f',
          surface: '#18181b',
          border:  '#27272a',
          orange:  '#f97316',
          cyan:    '#22d3ee',
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      }
    },
  },
  plugins: [require('tailwindcss-animate')],
} satisfies Config
```

### `vite.config.ts`

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      }
    }
  }
})
```

### Backend

```bash
cd mcp-forge
pip install fastapi uvicorn sse-starlette python-multipart aiofiles

# Run backend
uvicorn api_server.main:app --reload --port 8001

# Run frontend (separate terminal)
cd ui && npm run dev
```

---

## 12. Development Sequence

Build in this order to always have a runnable app at each step:

```
Phase 5.1 — Scaffold + layout
  [ ] Vite + Tailwind + shadcn setup
  [ ] TopNav + PipelineStepper (static, no logic)
  [ ] App.tsx shell with step routing
  [ ] Dark theme applied globally

Phase 5.2 — Upload
  [ ] FastAPI /api/upload endpoint
  [ ] DropZone.tsx (drag + click)
  [ ] Upload call in api.ts
  [ ] Store: sessionId, specMeta, specRaw
  [ ] Advance to Viewer on success

Phase 5.3 — Viewer
  [ ] CodeMirror YAML viewer (read-only)
  [ ] SpecMeta summary card (title, version, op count, auth)
  [ ] Operations tab (table, method badges)
  [ ] Raw tab

Phase 5.4 — Validation
  [ ] FastAPI /api/validate endpoint
  [ ] ValidationPane.tsx (controls + console)
  [ ] IssueRow.tsx (expandable)
  [ ] Gate: advance only if canGenerate === true

Phase 5.5 — Config
  [ ] ConfigPane.tsx (server name, rate limit, resilience settings)
  [ ] AuthConfig.tsx (API Key wired, others shown disabled)
  [ ] Tools tab (checkbox list, select all)
  [ ] Store: genConfig

Phase 5.6 — Generation + Download
  [ ] FastAPI /api/generate/stream SSE endpoint
  [ ] ConsoleOutput.tsx (live streaming lines)
  [ ] ProgressStages.tsx (stage pipeline)
  [ ] FileTreePreview.tsx (after completion)
  [ ] File Preview Modal (CodeMirror per file)
  [ ] Download ZIP button → /api/session/{id}/download
  [ ] Cleanup on session end
```

---

## 13. Error Handling & Edge Cases

| Scenario | Handling |
|---|---|
| Upload fails (malformed file) | Inline banner in DropZone, allow retry |
| Validation blocks generation | `→ Configure` button disabled + tooltip |
| SSE connection drops mid-generation | Show "Connection lost — retry" button |
| Generation fails in Python | Error stage turns red, error message in console |
| Session expired (backend restarted) | 404 on any API call → redirect to Step 1 with toast |
| File > 10MB | Client-side rejection before upload, size shown |
| Browser refresh | Warn user that session will be lost (beforeunload) |

---

## 14. Future UI Features (post-Phase 5)

- **Spec editor** — make the Viewer CodeMirror editable, save changes back to session
- **Diff view** — show what changed between spec upload and generation
- **History** — local storage of previous generation sessions
- **Dark/light toggle** — currently dark-only
- **Enhancement passes** — when Phase 4 is built, wire in the LLM tool enhancer
  with a real `Actual cost: $X.XX` readout
- **Connecting to AI Agents** — Step 6 after Download: show how to wire `.mcp.json`
  into Claude Desktop / Claude Code / Cursor

---

## 15. File Checklist for Cursor

When starting Phase 5.1 in Cursor, create these files in order:

```
ui/
├── index.html                      ← update title to "MCP Forge"
├── vite.config.ts                  ← proxy /api to :8001
├── tailwind.config.ts              ← dark mode, forge colours, JetBrains Mono
├── src/
│   ├── main.tsx                    ← import fonts, set document class="dark"
│   ├── App.tsx                     ← step router shell
│   ├── store.ts                    ← full Zustand store from Section 6
│   ├── types.ts                    ← all interfaces from Section 6
│   ├── api.ts                      ← all API calls from Section 8
│   ├── components/layout/
│   │   ├── TopNav.tsx
│   │   └── PipelineStepper.tsx
│   ├── components/upload/
│   │   ├── UploadPane.tsx
│   │   └── DropZone.tsx
│   ├── components/viewer/
│   │   ├── SpecViewer.tsx
│   │   ├── OperationsList.tsx
│   │   └── ProblemsPanel.tsx
│   ├── components/validation/
│   │   ├── ValidationPane.tsx
│   │   └── IssueRow.tsx
│   ├── components/config/
│   │   ├── ConfigPane.tsx
│   │   ├── AuthConfig.tsx
│   │   └── RateLimitConfig.tsx
│   ├── components/generation/
│   │   ├── GenerationPane.tsx
│   │   ├── ConsoleOutput.tsx
│   │   ├── ProgressStages.tsx
│   │   └── FileTreePreview.tsx
│   └── lib/
│       └── utils.ts
api_server/
├── __init__.py
├── main.py                         ← full FastAPI app from Section 7
├── routers/                        ← split routes here in Phase 5.3+
└── services/                       ← spec_service.py, gen_service.py
```
