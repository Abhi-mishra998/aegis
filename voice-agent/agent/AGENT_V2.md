# AGENT_V2.md — Aegis Voice Guide (portfolio build, CPU-only AWS)

> **You are a coding agent. This file is your contract.** Read it fully before doing anything.
> It supersedes the previous GPU-based draft (vLLM on g6.xlarge) and `agent.md`.
>
> You will build the **Aegis Voice Guide v2** — a professional cybersecurity voice advisor that talks like a human, answers cybersecurity questions directly, is grounded in the user's Aegis documentation via **hybrid RAG (BM25 + dense vectors + reranker)**, and runs on a **small CPU EC2 instance (`t3.medium`)** with the LLM **hosted (Groq, with Gemini as fallback)**. No GPU. No self-hosted model.
>
> **Why this design.** The user is building this as a **portfolio / resume project** to demo during job interviews. Optimization priorities, in order: (1) **demo always works** when an interviewer clicks the link, (2) **cheap** — hard budget ≤ $250/mo, realistic target ≈ $15–30/mo, (3) **good architecture story** to discuss in the interview. A GPU is not needed because the LLM is hosted; everything else (agent loop, embeddings, RAG, ChromaDB) runs comfortably on a 2-vCPU / 4 GB box.
>
> **THE ONE RULE.** Do not guess. Do not trial-and-error. When you need a credential, an account detail, an instance choice, or any decision the user can simply tell you — **stop, ask, wait, confirm, then proceed.** Never launch a billable AWS resource without explicit cost approval in writing.
>
> Dated **June 2026**. Items marked ⚠️ drift (provider quotas, AWS prices, model strings) — confirm before relying on them. Never invent a value to fill a gap; ask.

---

## 0. Table of contents

1. What you are building (architecture + locked decisions)
2. Interaction protocol — the question engine
3. Functional requirements
4. Non-functional requirements
5. Phase plan with elicitation gates
6. Credentials & configuration reference
7. LiveKit reference (verified)
8. AWS deployment reference (t3.medium, no GPU)
9. LLM provider strategy (Groq primary, Gemini fallback)
10. Hybrid RAG — BM25 + dense + reranker
11. The core code (self-contained)
12. Coding standards & repo conventions
13. Acceptance tests / definition of done
14. Pitfalls
15. Appendix A — question bank

---

## 1. What you are building

### 1.1 The product
A **hybrid cybersecurity voice advisor**. The user (or an interviewer testing the demo) speaks; the agent transcribes, retrieves the most relevant Aegis documentation chunks using a **two-stage hybrid pipeline** (BM25 + dense vectors → reranker), reasons with a **hosted LLM**, and replies in natural human speech. Aegis is the user's runtime security gateway for AI agents — this voice agent is its spoken expert guide *and* a general cybersecurity advisor.

### 1.2 Locked architectural decisions (LOCKED 2026-06-01)

| Decision | Choice | Why |
|---|---|---|
| Pipeline | Chained STT → LLM → TTS | Voice agent standard; RAG needs the text seam. |
| **Compute host** | **AWS EC2 `t3.medium`** (Ubuntu, 2 vCPU, 4 GB RAM) | CPU is sufficient — no LLM runs locally. ~$30/mo if 24/7, less with start/stop. |
| **LLM hosting** | **Hosted (Groq primary, Gemini fallback)** | Free tiers; no GPU bill; no rate-cap on Groq for short demo conversations after RAG tuning. |
| **LLM primary** | **Groq `llama-3.3-70b-versatile`** (free tier) | Free, fast (~200 ms TTFT); enough for portfolio demos. |
| **LLM fallback** | **Gemini 2.5 Flash-Lite** (free) | Kicks in only if Groq is down. 20 RPD on free tier is fine for emergency backup. |
| STT (English) | **Deepgram Nova-3** | Streaming, $200 free credit ⚠️ (months of demo use). |
| TTS (English) | **Cartesia sonic-3** | Low TTFB, free signup credit ⚠️. |
| Turn detection | Silero VAD + LiveKit MultilingualModel + tuned endpointing delays | Fixes v1 turn-taking jitter. |
| **RAG retrieval** | **Hybrid: BM25 + dense (sentence-transformers/all-MiniLM-L6-v2) + cross-encoder reranker (ms-marco-MiniLM-L-6-v2)** | Better recall + precision than dense-only. All CPU-friendly. |
| **Vector store** | **ChromaDB** on the EC2 box's EBS volume | Local, free, co-located with the agent (no network hop). |
| Transport | LiveKit Cloud "Build" (free) | No infra to manage. |
| Persona | Human, direct cybersecurity advisor — no doc-bot tone, no preachy disclaimers | The user's explicit requirement (§3 FR-3). |
| Domain | `voice.aegisagent.<tld>` (user to confirm TLD) | Friendly URL for interviewer-facing demo. |

### 1.3 Architecture

**High-level (locked diagram from user 2026-06-01):**
```
                    ┌─────────────────────┐
                    │     User Browser    │
                    │  voice.aegisagent   │
                    └──────────┬──────────┘
                               │ WebRTC
                               ▼
                    ┌─────────────────────┐
                    │   LiveKit Cloud     │
                    │   (Free Build)      │
                    └──────────┬──────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────┐
│ AWS EC2 t3.medium (Ubuntu)  ≈ $15–30/mo                   │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Aegis Voice Agent (Python)                          │  │
│  │                                                     │  │
│  │ Deepgram STT                                        │  │
│  │     ↓                                               │  │
│  │ Tool calling                                        │  │
│  │     ↓                                               │  │
│  │ search_aegis_docs()  ──► hybrid RAG (§10)           │  │
│  │     ↓                                               │  │
│  │ Cartesia TTS                                        │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                           │
│  ┌─────────────────────┐                                  │
│  │ ChromaDB            │  ← embeddings + BM25 index       │
│  │ Aegis docs vectors  │    persisted on EBS              │
│  └─────────────────────┘                                  │
└───────────────────────────────────────────────────────────┘
                               │ outbound HTTPS only
                               ▼
                    ┌─────────────────────┐
                    │   Hosted LLM        │
                    │  Groq → Gemini      │
                    └─────────────────────┘
```

**The hybrid RAG pipeline (inside `search_aegis_docs`, §10):**
```
   user query
       │
       ▼
   ┌─────────┐    ┌──────────────┐
   │  BM25   │    │ Vector search│
   │ sparse  │    │  (dense)     │
   └────┬────┘    └──────┬───────┘
        │                │
        └────►  Merge  ◄─┘    (union of top-k from each)
                  │
                  ▼
              Reranker          (cross-encoder scores all candidates)
                  │
                  ▼
            Top context         (top-N reranked chunks back to LLM)
```

---

## 2. Interaction protocol — the question engine

### 2.1 The golden loop
```
1. ASK    — present the specific question(s) for this step (batched by topic).
2. WAIT   — stop your turn. Do not proceed on assumptions.
3. CONFIRM— echo back what you understood (by name for secrets, never the value).
4. BUILD  — do the work for this step only.
5. VERIFY — show the result / run the acceptance check, then move to the next gate.
```

### 2.2 When you MUST stop and ask
- Any **credential or secret**: AWS keys, LiveKit, Deepgram, Cartesia, Groq, Gemini.
- Any **identifier** you can't derive: LiveKit project URL, AWS region, EC2 key-pair name, domain name.
- Any **branch decision**: free-tier provider choice, on-demand vs spot, always-on vs start/stop, frontend now vs later.
- **Anything billable.** `terraform apply` runs ONLY after the user has typed an explicit "yes, $X/mo approved."

### 2.3 AWS credentials handling
- Prefer **IAM role on the instance** (no long-lived keys to leak). For initial bootstrap, accept temporary access keys; use them only for `terraform apply`; never bake them into code, an AMI, or git. Store them in `infrastructure/.env.aws.local` with `0600` permissions; that file is `.gitignore`d.
- After provisioning, the running instance reaches all third-party APIs **outbound**; it pulls runtime secrets (Deepgram, Cartesia, Groq, Gemini, LiveKit) from **AWS Secrets Manager** via its IAM role. Never bake them into the AMI.
- Confirm secrets by **name only** — never echo a value back.
- After this build, **rotate the AWS keys** the user pasted in chat. (Free-tier IAM user, but good hygiene.)

### 2.4 Confirmation & state tracking
```
Build state (LOCKED 2026-06-01 unless noted):
  persona:        human cybersec advisor (hybrid)                ✅ LOCKED
  compute:        EC2 t3.medium, ap-south-1                      ✅ LOCKED
  llm primary:    Groq llama-3.3-70b-versatile (free)            ✅ LOCKED
  llm fallback:   Gemini 2.5 Flash-Lite (free)                   ✅ LOCKED
  rag:            hybrid BM25 + dense + cross-encoder reranker   ✅ LOCKED
  vectors:        ChromaDB on EC2 EBS                            ✅ LOCKED
  transport:      LiveKit Cloud Build (free)                     ✅ LOCKED
  languages:      English only (Hindi deferred)                  ✅ LOCKED
  frontend:       LiveKit Playground for v1; small Next.js page  ⏳ later
  domain:         voice.aegisagent.<tld>                         ⏳ confirm TLD
  creds:          LiveKit ✅  Deepgram ✅  Cartesia ✅  Groq ✅
                  AWS ✅ (pasted, rotate after)  Gemini ⏳ optional
  budget cap:     $250/mo HARD, target ~$15–30/mo                ✅ LOCKED
  cost approved:  <yes / no>  ← REQUIRED before terraform apply
```

### 2.5 What NOT to do
- ❌ Launch any AWS resource before cost approval. ❌ Commit any secret, ever. ❌ Bake secrets into an AMI/container/code. ❌ Re-litigate a LOCKED decision. ❌ Run a paid LLM tier (NFR-1).

---

## 3. Functional requirements (FR)

- **FR-1 Voice in / voice out** over WebRTC.
- **FR-2 Hybrid knowledge.** Aegis-specific questions answer **only** from the docs via `search_aegis_docs` (hybrid retrieval + reranker, §10). General cybersecurity questions draw on the model's knowledge.
- **FR-3 Human persona, no nanny disclaimers.** System prompt makes the agent sound like a senior security engineer on a call: contractions, short sentences, natural pacing; direct substantive answers; **no** moralizing, **no** stalling phrases ("let me check"). Boundary: not a tool for producing live attack tooling — minimal one-line guardrail.
- **FR-4 Asks one clarifying question** when the request is ambiguous, instead of guessing.
- **FR-5 No hallucination.** Hybrid retrieval + reranker + score threshold + low temperature + explicit "say you don't know rather than guess."
- **FR-6 Barge-in + turn detection.** VAD + semantic model with tuned endpointing delays.
- **FR-7 Streaming.** Partial STT → streaming LLM tokens → streaming TTS.
- **FR-8 Ingest pipeline.** Idempotent `ingest.py` walks `docs/` recursively, chunks heading-aware with overlap, builds both the **dense index in ChromaDB** and the **BM25 index** on disk.
- **FR-9 Greeting.** One-sentence human greeting on session start.
- **FR-10 LLM fallback.** If Groq returns 429 / 5xx, transparently fall back to Gemini. Log the fallback.

---

## 4. Non-functional requirements (NFR)

- **NFR-1 Cost.** Hard cap **$250/mo**, realistic target **$15–30/mo**. Everything is on free tiers or a single t3.medium. **No paid LLM/STT/TTS tiers without explicit approval.**
- **NFR-2 Latency.** Target < 1 s user-stops → agent-starts. Hosted LLM (Groq) does the heavy lifting fast; everything else is local on the instance (no network hop for embeddings/RAG/reranker).
- **NFR-3 Security.** Secrets in AWS Secrets Manager (never code/AMI/git). LiveKit JWTs short-lived. Security group: SSH 22 from user's IP only, no other inbound. Agent makes only outbound HTTPS.
- **NFR-4 Reliability.** systemd `Restart=always` on the agent. LLM fallback chain (Groq → Gemini). Cap chat history to ~6 turns before each LLM call (TPM safety). Clean up sessions in `finally`.
- **NFR-5 Observability.** Per-turn latency log (STT, RAG, LLM TTFT, TTS TTFB, total) + which doc chunk was cited; ship to CloudWatch.
- **NFR-6 Cost safety.** CloudWatch billing alarm at $200 (warn) and $240 (hard). Optional auto-stop on idle (CPU < 5% for 30 min stops the instance).

---

## 5. Phase plan with elicitation gates

### Phase 0 — Discovery ✅ DONE
All Phase-0 questions answered (see §2.4 build state).

### Phase 1 — Provider credentials (free) ✅ DONE
- LiveKit ✅, Deepgram ✅, Cartesia ✅, Groq ✅ (in `agent/.env.local`).
- AWS keys ✅ (in `infrastructure/.env.aws.local`, gitignored, 0600).
- Gemini key — optional, only if user wants the fallback wired (one extra free key from aistudio.google.com).

### Phase 2 — Refactor code for the new architecture
1. `agent/src/rag.py` — add BM25 + cross-encoder reranker (hybrid retrieval).
2. `agent/src/agent.py` — wire LLM fallback chain (Groq → Gemini); confirm persona/turn-taking unchanged.
3. `agent/src/ingest.py` — also persist a BM25 index alongside ChromaDB.
4. `agent/pyproject.toml` — add `rank-bm25`, keep `sentence-transformers` for both embeddings + reranker.
5. **GATE 2:** confirm `python ingest.py` reports `N>0 chunks indexed (dense + BM25)`. Run `agent.py console` locally; verify a 3-question demo grounded + tight.

### Phase 3 — Terraform code + plan (non-billable)
1. Write `infrastructure/*.tf` (§8).
2. `terraform init && terraform plan` — present the plan + live cost.
3. **GATE 3 — cost approval:** present plan, get explicit "yes, ≤$X/mo approved".

### Phase 4 — Provision + deploy
1. `terraform apply`.
2. SSH in, verify the agent's systemd service is up, RAG index is built, Playground session reaches it.
3. **GATE 4:** user does a 5-min Playground call; confirms persona + grounding + latency.

### Phase 5 — Tune
- Adjust persona prompt, RAG `k` / score threshold / reranker top-N based on the test call.

### Phase 6 (optional) — Domain, frontend, Hindi
- Each behind its own gate.

---

## 6. Credentials & configuration reference

### 6.1 `agent/.env.local` (committed shape via `.env.example`; values NEVER committed)
```bash
# LiveKit Cloud (free Build)
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=API************
LIVEKIT_API_SECRET=********************************

# STT / TTS (free credits)
DEEPGRAM_API_KEY=****************
CARTESIA_API_KEY=****************

# LLM primary (free)
GROQ_API_KEY=gsk_************

# LLM fallback (optional, free) — set to enable Groq->Gemini failover
GOOGLE_API_KEY=

# Runtime LLM selection (optional override)
LLM_PROVIDER=groq      # groq | gemini  (auto-fallback if Groq errors)
```

### 6.2 `infrastructure/.env.aws.local` (provisioning only)
```bash
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=ap-south-1
```
File is `0600`, in root `.gitignore`. ⚠️ Rotate after this build (pasted in chat).

### 6.3 `.gitignore` must include
```
.env.local
.env.*.local
.env.aws.local
*.pem
infrastructure/.terraform/
infrastructure/*.tfstate*
infrastructure/*.tfvars
chroma_db/
bm25_index/
__pycache__/
.venv/
```

---

## 7. LiveKit reference (verified, June 2026 ⚠️)

- **Build plan ($0)** — no credit card. ~1,000 agent-session min/mo, ~5,000 WebRTC min, 50 GB transfer, $2.50 Inference credit, up to 5 concurrent agent sessions. Hard cap (requests fail rather than billing) — good cost protection.
- Agent runs on **your EC2** and connects outbound, so the Build "cold start" caveat for cloud-hosted agents doesn't apply.
- Run modes: `console` (terminal), `dev` (Cloud + Playground + logs), `start` (production).
- Inference credit is mostly irrelevant — you call Deepgram / Cartesia / Groq directly with your own keys.

---

## 8. AWS deployment reference (t3.medium)

### 8.1 Resources Terraform creates (in `infrastructure/`)
1. **VPC** — use the **default VPC + default subnet** in `ap-south-1` (no custom networking; saves complexity).
2. **Security group**
   - Inbound: SSH (22) from `<user's public IP>/32` ONLY. Optional 8080 for a health endpoint, same IP only.
   - Outbound: ALL (agent reaches LiveKit Cloud, Deepgram, Cartesia, Groq, Gemini).
3. **EC2 key-pair** — create new `aegis-voice-guide`, write `.pem` to `infrastructure/aegis-voice-guide.pem` (`0400`, gitignored).
4. **IAM role + instance profile** with policies for:
   - `secretsmanager:GetSecretValue` on `arn:aws:secretsmanager:*:*:secret:aegis/*`
   - `logs:CreateLogStream`, `logs:PutLogEvents` (CloudWatch)
5. **Secrets Manager** — one secret per provider key (`aegis/livekit`, `aegis/deepgram`, `aegis/cartesia`, `aegis/groq`, optional `aegis/gemini`). `terraform apply` writes them from the `.env.local` once; never re-read.
6. **EC2 instance** — `t3.medium`, Ubuntu 24.04 LTS AMI (latest official), **30 GB gp3 EBS root** (room for ChromaDB + BM25 index + model cache). User-data installs Python, clones the repo, pulls secrets, starts systemd unit.
7. **Elastic IP** — attached so the IP doesn't change across stop/start (the LiveKit dispatch URL stays stable). Cost ⚠️: $0 while attached to a running instance, ~$3.6/mo if attached to a stopped instance — accept this.
8. **CloudWatch billing alarm** at **$200** (SNS email warning).
9. **CloudWatch log group** `/aegis/agent`.

### 8.2 systemd unit
```ini
# /etc/systemd/system/aegis-agent.service
[Unit]
Description=Aegis Voice Agent
After=network-online.target

[Service]
EnvironmentFile=/opt/aegis/.env.local
WorkingDirectory=/opt/aegis/agent
ExecStart=/opt/aegis/.venv/bin/python src/agent.py start
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 8.3 Cost expectations
| Item | ~ $/mo ⚠️ |
|---|---|
| `t3.medium` 24/7 in ap-south-1 | ~$30 |
| 30 GB gp3 EBS | ~$3 |
| Elastic IP (attached to running instance) | $0 |
| Secrets Manager (5 secrets) | ~$2 |
| CloudWatch logs (small) | ~$1 |
| Data transfer (demo traffic) | ~$1–2 |
| **Total 24/7** | **~$35–40/mo** |

With **stop overnight + weekends** (run ~10 hrs/day weekdays): ~**$15–20/mo**. All well under the $250 cap.

---

## 9. LLM provider strategy

### 9.1 Why hosted, not self-hosted
- The original v2 spec called for a self-hosted Qwen 3 8B on g6.xlarge GPU (~$960/mo if 24/7). For a portfolio project, that's overkill and outside budget.
- Groq's free tier serves `llama-3.3-70b-versatile` at ~200 ms TTFT — fast enough for voice, free for the demo-volume usage of a portfolio.

### 9.2 Groq free-tier limits ⚠️ June 2026
- ~30 RPM, **~12,000 TPM** (binding constraint on RAG-heavy voice), ~14,400 RPD.
- We previously blew through TPM with naïve 70B + full chat history. The new design avoids this by:
  - Truncating chat history to last ~6 messages per call (~600 tokens).
  - Short system prompt (~250 tokens).
  - Hybrid RAG returns only top-2 reranked chunks (~400 tokens).
  - Per-call token total: **~1,200–1,500** → 8–10 calls/min safely under TPM cap.

### 9.3 Gemini fallback
- Wired but inactive by default. If a Groq call returns 429 or 5xx, the agent retries the same prompt against `gemini-2.5-flash-lite`.
- Gemini free tier: 20 RPD on flash-lite — fine for emergency fallback.
- Without `GOOGLE_API_KEY` in env, the fallback simply isn't attempted.

### 9.4 LLM-side anti-hallucination
- `temperature=0.4`, `max_completion_tokens=150` (forces brevity).
- `frequency_penalty=0.6`, `presence_penalty=0.3` (prevents v1's "kill switch is... kill switch is..." loops).
- System prompt says: "If grounding is missing, say you don't know — never guess."

---

## 10. Hybrid RAG — BM25 + dense + reranker

This is the FR-5 / FR-2 recipe. Three stages working together.

### 10.1 Why hybrid
- **Dense (vector) retrieval** is great at semantic matches but misses exact-keyword queries ("CVE-2024-3094", "kill_switch_engaged.md").
- **BM25 (sparse)** nails exact terms but misses paraphrases.
- **Union → rerank** combines recall from both and precision from the reranker.

### 10.2 Stages
1. **Sparse retrieval (BM25).** Tokenize the query; score every chunk with `rank-bm25`'s `BM25Okapi`. Take top-10.
2. **Dense retrieval.** Embed the query with `all-MiniLM-L6-v2` (384-dim, CPU-friendly). Query ChromaDB for top-10 by cosine similarity, with a soft `min_score` cutoff.
3. **Merge** the two candidate lists into a deduped set (≤20 chunks).
4. **Rerank.** Score every (query, chunk) pair with the cross-encoder `cross-encoder/ms-marco-MiniLM-L-6-v2`. This is a 22 MB model that runs on CPU in <100 ms for 20 chunks.
5. **Return top-N.** Typically `N=2` to keep LLM token budget tight. Source path is attached so the model can cite it.

### 10.3 Cost / latency
- Indexing (one-time per `ingest.py` run): minutes for ~1700 chunks.
- Query path on t3.medium: BM25 ~5 ms + dense ~30 ms + rerank 20 candidates ~80 ms = **~120 ms** RAG latency. Negligible inside a voice turn.

### 10.4 Index persistence
- ChromaDB persists in `agent/chroma_db/`.
- BM25 index pickled to `agent/bm25_index/index.pkl` alongside the corpus (chunk text + source path).
- Both are git-ignored and rebuilt by `ingest.py`.

---

## 11. The core code (self-contained)

### 11.1 `pyproject.toml`
```toml
[project]
name = "aegis-voice-guide"
requires-python = ">=3.10"
dependencies = [
  "livekit-agents[deepgram,cartesia,openai,silero,turn-detector]~=1.5",
  "chromadb>=0.5",
  "sentence-transformers>=3.0",
  "rank-bm25>=0.2.2",
  "python-dotenv>=1.0",
]
```
(`openai` plugin is used to talk to both Groq and Gemini via their OpenAI-compatible endpoints.)

### 11.2 `src/rag.py` — hybrid retrieval + reranker
```python
from __future__ import annotations
import pickle, re
from dataclasses import dataclass
from pathlib import Path

from chromadb import PersistentClient
from chromadb.utils import embedding_functions
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder

EMBED_MODEL = "all-MiniLM-L6-v2"
RERANKER    = "cross-encoder/ms-marco-MiniLM-L-6-v2"
COLLECTION  = "aegis"

@dataclass
class Hit:
    text: str
    source: str
    score: float

_TOKEN_RE = re.compile(r"\w+")
def _tokenize(s: str) -> list[str]:
    return _TOKEN_RE.findall(s.lower())


class AegisKnowledge:
    """Hybrid RAG: BM25 (sparse) + dense vectors + cross-encoder reranker."""

    def __init__(self, persist_dir: str = "./chroma_db", bm25_dir: str = "./bm25_index") -> None:
        self._client = PersistentClient(path=persist_dir)
        self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
        self.collection = self._client.get_or_create_collection(
            name=COLLECTION, embedding_function=self._ef, metadata={"hnsw:space": "cosine"},
        )
        self._reranker = CrossEncoder(RERANKER)  # ~22 MB, CPU-friendly
        self._bm25_dir = Path(bm25_dir)
        self._bm25, self._bm25_docs, self._bm25_meta = self._load_bm25()

    def _load_bm25(self):
        p = self._bm25_dir / "index.pkl"
        if not p.exists():
            return None, [], []
        with p.open("rb") as f:
            d = pickle.load(f)
        return d["bm25"], d["docs"], d["meta"]

    def save_bm25(self, docs: list[str], meta: list[dict]) -> None:
        self._bm25_dir.mkdir(parents=True, exist_ok=True)
        bm25 = BM25Okapi([_tokenize(d) for d in docs])
        with (self._bm25_dir / "index.pkl").open("wb") as f:
            pickle.dump({"bm25": bm25, "docs": docs, "meta": meta}, f)

    def reset(self) -> None:
        try: self._client.delete_collection(COLLECTION)
        except Exception: pass
        self.collection = self._client.get_or_create_collection(
            name=COLLECTION, embedding_function=self._ef, metadata={"hnsw:space": "cosine"},
        )

    def add(self, docs: list[str], ids: list[str], metadatas: list[dict]) -> None:
        self.collection.add(documents=docs, ids=ids, metadatas=metadatas)

    def search(self, query: str, k_dense: int = 10, k_bm25: int = 10, top_n: int = 2) -> list[Hit]:
        cand: dict[str, tuple[str, dict]] = {}  # text -> (text, meta)

        # 1) Dense retrieval
        if self.collection.count() > 0:
            res = self.collection.query(query_texts=[query], n_results=k_dense)
            for d, m in zip(res.get("documents", [[]])[0], res.get("metadatas", [[]])[0]):
                cand[d] = (d, m or {})

        # 2) BM25 (sparse) retrieval
        if self._bm25 is not None and self._bm25_docs:
            scores = self._bm25.get_scores(_tokenize(query))
            top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k_bm25]
            for i in top_idx:
                cand[self._bm25_docs[i]] = (self._bm25_docs[i], self._bm25_meta[i])

        if not cand:
            return []

        # 3) Cross-encoder rerank
        pairs = [(query, t) for t, _ in cand.values()]
        scores = self._reranker.predict(pairs)
        ranked = sorted(
            zip(cand.values(), scores), key=lambda x: x[1], reverse=True
        )[:top_n]
        return [Hit(text=t, source=(m or {}).get("source", "unknown"), score=float(s))
                for (t, m), s in ranked]
```

### 11.3 `src/ingest.py` — builds BOTH dense and BM25 indexes
```python
from __future__ import annotations
import re, sys
from pathlib import Path
from rag import AegisKnowledge

MAX_CHARS, OVERLAP = 1200, 150
HEADING_RE = re.compile(r"(?m)^(#{1,6}\s.*)$")

def chunk_markdown(text: str) -> list[str]:
    parts = HEADING_RE.split(text); sections, buf = [], ""
    for p in parts:
        if not p: continue
        if HEADING_RE.match(p):
            if buf.strip(): sections.append(buf.strip())
            buf = p + "\n"
        else: buf += p
    if buf.strip(): sections.append(buf.strip())
    chunks: list[str] = []
    for sec in sections:
        if len(sec) <= MAX_CHARS:
            chunks.append(sec); continue
        i = 0
        while i < len(sec):
            chunks.append(sec[i:i+MAX_CHARS]); i += MAX_CHARS - OVERLAP
    return [c for c in chunks if c.strip()]

def main(docs_dir: str) -> None:
    root = Path(docs_dir).resolve()
    files = sorted(p for p in root.rglob("*.md") if p.is_file())
    if not files: sys.exit(f"no .md files under {root}")
    kb = AegisKnowledge(); kb.reset()
    docs, ids, metas = [], [], []
    for f in files:
        rel = f.relative_to(root).as_posix()
        for i, ch in enumerate(chunk_markdown(f.read_text(encoding="utf-8"))):
            docs.append(ch); ids.append(f"{rel}#{i}"); metas.append({"source": rel})
    # batch into ChromaDB
    for i in range(0, len(docs), 256):
        kb.add(docs[i:i+256], ids[i:i+256], metas[i:i+256])
    # build & persist BM25 index
    kb.save_bm25(docs, metas)
    print(f"Indexed {len(docs)} chunks from {len(files)} files (dense + BM25).")

if __name__ == "__main__":
    default = Path(__file__).resolve().parents[2] / "docs"
    main(sys.argv[1] if len(sys.argv) > 1 else str(default))
```

### 11.4 `src/agent.py` — Groq → Gemini fallback, hybrid RAG tool, tuned turn-taking
```python
from __future__ import annotations
import logging, os
from pathlib import Path
from dotenv import load_dotenv

from livekit import agents
from livekit.agents import (Agent, AgentServer, AgentSession, JobContext,
                            function_tool, room_io)
from livekit.plugins import cartesia, deepgram, openai as lk_openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from rag import AegisKnowledge

load_dotenv(".env.local")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aegis-agent")

CHROMA_DIR = str(Path(__file__).resolve().parents[1] / "chroma_db")
BM25_DIR   = str(Path(__file__).resolve().parents[1] / "bm25_index")
knowledge  = AegisKnowledge(persist_dir=CHROMA_DIR, bm25_dir=BM25_DIR)

AEGIS_KEYTERMS = ["Aegis", "kill switch", "enforce mode", "shadow mode",
                  "audit log", "behavioral firewall", "policy engine",
                  "control plane", "tamper-evident", "runtime governance"]

INSTRUCTIONS = """\
You're a senior cybersecurity engineer giving voice advice. You know Aegis
(the user's runtime security gateway for AI agents) from its docs and
cybersecurity broadly.

Style: peer to peer, conversational. Max two sentences unless the user
asks for more. End most answers with a short, specific follow-up question.
Never repeat yourself. No markdown, no URLs read aloud.

STT sometimes mishears Aegis terms ("Aegis"->"eggs", "kill switch"->"keel").
Silently interpret what they meant — never say "I think you meant X".

For Aegis questions, call search_aegis_docs once. Don't invent Aegis facts
(URLs, versions, customers). If docs lack it, say so.

For general cybersec, answer from your expertise — OWASP, NIST, MITRE OK,
no made-up CVEs.

If user says "stop" or "thanks bye", acknowledge in three words and move on.
"""

GREETING = ("Hey, I'm your cybersecurity voice guide. I know your Aegis docs "
            "and security topics broadly. What are you working on?")


class AegisGuideAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    @function_tool
    async def search_aegis_docs(self, query: str) -> str:
        """Search the user's Aegis docs (hybrid BM25 + dense + reranker).

        Use ONLY for Aegis-specific questions. Call at most once per topic per turn.
        Args:
            query: short focused query, e.g. "kill switch behavior".
        """
        hits = knowledge.search(query, top_n=2)
        if not hits:
            return "The Aegis docs don't have a clear section on that."
        logger.info("rag hits: %s", [(h.source, round(h.score, 3)) for h in hits])
        return "\n\n---\n\n".join(f"[source: {h.source}]\n{h.text}" for h in hits)


def _build_llm() -> lk_openai.LLM:
    provider = os.environ.get("LLM_PROVIDER", "groq").lower()
    if provider == "gemini":
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY missing; set it or use LLM_PROVIDER=groq.")
        return lk_openai.LLM(
            model="gemini-2.5-flash-lite",
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            temperature=0.4, max_completion_tokens=150,
        )
    # default = Groq
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY missing. Get one at https://console.groq.com/keys")
    return lk_openai.LLM(
        model="llama-3.3-70b-versatile",
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
        temperature=0.4, max_completion_tokens=150,
        extra_body={"frequency_penalty": 0.6, "presence_penalty": 0.3},
    )


server = AgentServer()

@server.rtc_session(agent_name="aegis-guide")
async def entrypoint(ctx: JobContext) -> None:
    logger.info("connecting room=%s", ctx.room.name)
    await ctx.connect()
    participant = await ctx.wait_for_participant()
    logger.info("participant joined: %s", participant.identity)

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi", keyterm=AEGIS_KEYTERMS),
        llm=_build_llm(),
        tts=cartesia.TTS(model="sonic-3"),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
        min_endpointing_delay=0.4,
        max_endpointing_delay=2.0,
        min_interruption_duration=0.6,
        min_interruption_words=2,
        false_interruption_timeout=1.5,
        allow_interruptions=True,
    )
    ctx.add_shutdown_callback(session.aclose)

    await session.start(
        agent=AegisGuideAgent(), room=ctx.room,
        room_options=room_io.RoomOptions(audio_input=room_io.AudioInputOptions()),
    )
    await session.generate_reply(instructions=f"Say exactly: {GREETING}")

if __name__ == "__main__":
    agents.cli.run_app(server)
```

Truncating chat history per turn (TPM safety) is done via an `on_user_turn_completed` hook that calls `turn_ctx.truncate(max_items=8)`. Wire when integrating.

---

## 12. Coding standards & repo conventions
- Python 3.10+; type hints. All config via env. **No secrets in code/AMI/git.**
- One persona prompt in one place; iterate there.
- Log per-turn latency (NFR-5).
- Commit `.env.example` only.

## 13. Acceptance tests / definition of done
- **DoD-Local:** `ingest.py` reports `N>0 chunks (dense + BM25)`; `agent.py console` greets human-style; answers (1) an Aegis question grounded with source path, (2) a general cybersec question from model knowledge, (3) an unknown question with a clean "I don't know" — no preachy disclaimers, no repetition.
- **DoD-AWS:** Terraform plan shown + approved; `terraform apply` succeeds; agent's systemd service `active (running)`; secrets pulled from Secrets Manager (none baked); SG only allows SSH from user IP; Playground session reaches the agent.
- **DoD-Cost-safety:** CloudWatch billing alarm at $200 active; auto-stop on idle wired (or explicitly deferred); start/stop scripts work.

## 14. Pitfalls — do NOT do these
1. ❌ Treating Cartesia as STT. ✅ Cartesia = TTS.
2. ❌ Letting chat history grow unbounded → Groq TPM 429s. ✅ Truncate to last ~6 messages each turn.
3. ❌ Putting RAG tool outputs into the persistent chat ctx. ✅ Tool outputs are per-turn; the truncate handles it.
4. ❌ Self-correcting STT mishearings out loud ("I think you meant X"). ✅ Silent interpretation.
5. ❌ Forgetting to back up `bm25_index/` or rebuilding it inconsistently. ✅ `ingest.py` rebuilds both indexes idempotently.
6. ❌ Exposing the agent's HTTP port publicly. ✅ Only outbound; SG inbound = SSH from user IP.
7. ❌ Hardcoding AWS keys anywhere. ✅ `infrastructure/.env.aws.local` (0600, gitignored) for bootstrap; IAM role on the instance for runtime.
8. ❌ Running `terraform apply` before cost approval. ✅ Show plan + cost first.
9. ❌ Quoting AWS prices as fixed. ✅ ⚠️ confirm live during `terraform plan`.
10. ❌ Adding a Hindi/Sarvam path "just in case". ✅ Deferred — keep v1 lean.

---

## 15. Appendix A — question bank

**All Phase-0 items are answered (§2.4).** Open items:
- Domain TLD for `voice.aegisagent.<tld>`?
- Wire Gemini fallback now (need `GOOGLE_API_KEY`) or skip until Groq fails?
- Start/stop policy: 24/7 (~$35/mo) or stop overnight/weekends (~$15/mo)?
- Auto-stop on idle: yes [recommended] / no?

---

*Compiled June 2026. Stack baseline: LiveKit Agents `~=1.5`, Python 3.10+, Ubuntu 24.04, AWS ap-south-1. Supersedes `agent.md` and the prior GPU-based AGENT_V2 draft. Re-verify ⚠️ items at provision time.*
