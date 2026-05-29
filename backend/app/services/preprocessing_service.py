"""
Preprocessing Service: Handles document chunking and context engineering.
Token-based chunking for compliance analysis.
"""
import logging
import os
import uuid
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Token limits
MAX_TOKENS_PER_CHUNK = 1000
CHUNK_OVERLAP_TOKENS = 100


class ContextEngineeringService:
    """
    Context Engineering Service: Prepares document content for compliance analysis.
    Handles chunking, rule injection, and prompt construction.
    """

    def __init__(self, db: Session):
        self.db = db

    async def preprocess_submission(self, submission_id: uuid.UUID) -> int:
        """
        Preprocesses a submission into content chunks.
        Returns the number of chunks created.
        """
        from app.models.submission import Submission
        from app.models.content_chunk import ContentChunk

        submission = self.db.query(Submission).filter(Submission.id == submission_id).first()
        if not submission:
            raise ValueError(f"Submission {submission_id} not found")

        # Check if already preprocessed
        existing_chunks = self.db.query(ContentChunk).filter(
            ContentChunk.submission_id == str(submission_id)
        ).count()

        if existing_chunks > 0:
            # If we previously chunked a file-upload submission but never
            # persisted the extracted text on the row, backfill it now so
            # the Review tab can render the body. Cheap one-time fix-up.
            if (
                not submission.original_content
                and submission.file_path
                and os.path.exists(submission.file_path)
            ):
                try:
                    text = await self._extract_from_file(
                        submission.file_path, submission.content_type
                    )
                    if text:
                        submission.original_content = text
                        self.db.add(submission)
                        self.db.commit()
                        logger.info(
                            f"Backfilled original_content for {submission_id} "
                            f"({len(text)} chars from {submission.content_type})"
                        )
                except Exception as e:
                    logger.warning(f"Original-content backfill failed (non-fatal): {e}")
            logger.info(f"Submission {submission_id} already has {existing_chunks} chunks")
            return existing_chunks

        # Update status
        submission.status = "preprocessing"
        self.db.commit()

        # Get content
        content = submission.original_content or ""
        extracted_from_file = False
        if not content and submission.file_path:
            content = await self._extract_from_file(submission.file_path, submission.content_type)
            extracted_from_file = True
        elif content and submission.content_type == "html":
            # Pasted HTML — surface meta-tags so analysis covers SEO/social fields too
            content = self._extract_html(content)

        if not content:
            logger.warning(f"No content found for submission {submission_id}")
            submission.status = "preprocessed"
            self.db.commit()
            return 0

        # Persist the extracted text on the submission so the Review tab can
        # render it for uploaded files (PDF/DOCX/HTML/MD) — the file itself
        # stays on disk for download, but the user-visible body lives here.
        if extracted_from_file and not submission.original_content:
            submission.original_content = content
            self.db.add(submission)
            self.db.commit()

        # Chunk the content
        chunks = self._chunk_text(content, submission.content_type)

        # Save chunks
        chunk_objects = []
        for i, chunk in enumerate(chunks):
            chunk_obj = ContentChunk(
                submission_id=submission_id,
                chunk_index=i,
                text=chunk["text"],
                token_count=chunk.get("token_count"),
                chunk_metadata=chunk.get("metadata", {})
            )
            self.db.add(chunk_obj)
            chunk_objects.append(chunk_obj)

        submission.status = "preprocessed"
        self.db.commit()

        logger.info(f"Created {len(chunk_objects)} chunks for submission {submission_id}")
        return len(chunk_objects)

    def _chunk_text(self, content: str, content_type: str = "text") -> List[Dict]:
        """
        Chunk text into token-limited segments.
        Falls back to paragraph chunking if tiktoken is unavailable OR if its
        BPE files can't be downloaded (corporate firewall blocks
        openaipublic.blob.core.windows.net — common on Bajaj VPN).
        """
        try:
            import tiktoken
            try:
                enc = tiktoken.get_encoding("cl100k_base")
            except Exception as e:
                logger.warning(
                    f"tiktoken encoding download failed ({type(e).__name__}); "
                    f"falling back to paragraph chunking: {e}"
                )
                return self._chunk_by_paragraphs(content, content_type)

            tokens = enc.encode(content)

            chunks = []
            start = 0
            chunk_index = 0

            while start < len(tokens):
                end = min(start + MAX_TOKENS_PER_CHUNK, len(tokens))
                chunk_tokens = tokens[start:end]
                chunk_text = enc.decode(chunk_tokens)

                chunks.append({
                    "text": chunk_text,
                    "token_count": len(chunk_tokens),
                    "metadata": {
                        "chunk_index": chunk_index,
                        "start_token": start,
                        "end_token": end,
                        "content_type": content_type
                    }
                })

                start = end - CHUNK_OVERLAP_TOKENS if end < len(tokens) else end
                chunk_index += 1

            return chunks if chunks else [{"text": content, "metadata": {}}]

        except ImportError:
            return self._chunk_by_paragraphs(content, content_type)

    def _chunk_by_paragraphs(self, content: str, content_type: str) -> List[Dict]:
        """Fallback paragraph-based chunking."""
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [content]

        chunks = []
        current_chunk = ""
        chunk_index = 0

        for para in paragraphs:
            if len(current_chunk) + len(para) > 3000:  # ~750 tokens
                if current_chunk:
                    chunks.append({
                        "text": current_chunk.strip(),
                        "metadata": {"chunk_index": chunk_index, "content_type": content_type}
                    })
                    chunk_index += 1
                current_chunk = para
            else:
                current_chunk += "\n\n" + para if current_chunk else para

        if current_chunk:
            chunks.append({
                "text": current_chunk.strip(),
                "metadata": {"chunk_index": chunk_index, "content_type": content_type}
            })

        return chunks if chunks else [{"text": content, "metadata": {}}]

    async def _extract_from_file(self, file_path: str, content_type: str) -> str:
        """Extract text content from uploaded files."""
        try:
            if content_type == "pdf":
                return await self._extract_pdf(file_path)
            elif content_type == "docx":
                return await self._extract_docx(file_path)
            elif content_type == "html":
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
                return self._extract_html(raw)
            elif content_type in ("markdown", "text"):
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
        except Exception as e:
            logger.error(f"Failed to extract content from {file_path}: {e}")
        return ""

    @staticmethod
    def _extract_html(raw_html: str) -> str:
        """
        Pull out meta-tags + visible body text so compliance analysis sees both
        the SEO/social surface (title, description, og:*, twitter:*, keywords)
        AND the body copy. Meta-tags are surfaced as a structured prefix so the
        LLM can attribute violations to them precisely.
        """
        if not raw_html or not raw_html.strip():
            return ""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw_html, "html.parser")
        except Exception as e:
            logger.warning(f"HTML parse failed, falling back to raw: {e}")
            return raw_html

        meta_pairs: list[tuple[str, str]] = []

        if soup.title and soup.title.string:
            meta_pairs.append(("title", soup.title.string.strip()))

        for tag in soup.find_all("meta"):
            content_attr = tag.get("content") or ""
            if not content_attr.strip():
                continue
            key = tag.get("name") or tag.get("property") or tag.get("http-equiv")
            if not key:
                continue
            meta_pairs.append((key.strip().lower(), content_attr.strip()))

        # H1 / H2 are often the headline equivalents in marketing pages.
        for level in ("h1", "h2"):
            for h in soup.find_all(level):
                txt = h.get_text(" ", strip=True)
                if txt:
                    meta_pairs.append((level, txt))

        # Strip noise before extracting body
        for noise in soup(["script", "style", "noscript", "template"]):
            noise.decompose()
        body_text = soup.get_text("\n", strip=True)

        parts: list[str] = []
        if meta_pairs:
            parts.append("[META TAGS]")
            for k, v in meta_pairs:
                parts.append(f"{k}: {v}")
        if body_text:
            if parts:
                parts.append("")
                parts.append("[BODY CONTENT]")
            parts.append(body_text)
        return "\n".join(parts)

    async def _extract_pdf(self, file_path: str) -> str:
        try:
            import pdfplumber
            text = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text.append(page_text)
            return "\n\n".join(text)
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            return ""

    async def _extract_docx(self, file_path: str) -> str:
        try:
            from docx import Document
            doc = Document(file_path)
            return "\n\n".join(para.text for para in doc.paragraphs if para.text.strip())
        except Exception as e:
            logger.error(f"DOCX extraction failed: {e}")
            return ""

    def create_compliance_prompts(self, content: str, rules_dict: Dict[str, List]) -> str:
        """
        Build a compliance analysis prompt from content and rules.

        Each rule is shown with rule_id + a verbatim source passage when
        available — the LLM MUST cite both. Output is constrained by the
        ViolationSchema Pydantic model.
        """
        rules_text = ""
        for category, rules in rules_dict.items():
            if rules:
                rules_text += f"\n### {category.upper()} RULES:\n"
                for i, rule in enumerate(rules, 1):
                    if isinstance(rule, dict):
                        rule_text = rule.get("rule_text", str(rule))
                        severity = rule.get("severity", "medium")
                        rule_id = rule.get("id", "")
                        source_quote = rule.get("source_quote") or rule.get("regulator_quote")
                    else:
                        rule_text = getattr(rule, "rule_text", str(rule))
                        severity = getattr(rule, "severity", "medium")
                        rule_id = str(getattr(rule, "id", ""))
                        source_quote = getattr(rule, "source_quote", None) or getattr(rule, "regulator_quote", None)

                    rules_text += f"{i}. [{severity.upper()}] (ID: {rule_id}) {rule_text}\n"
                    if source_quote:
                        quote = str(source_quote).strip().replace("\n", " ")
                        if len(quote) > 240:
                            quote = quote[:237] + "…"
                        rules_text += f"   regulator_quote: \"{quote}\"\n"

        prompt = f"""You are auditing marketing content against insurance/financial compliance rules.
Be precise — flag only ACTUAL violations of the rules listed, not stylistic gripes.

DOCUMENT CONTENT:
{content}

COMPLIANCE RULES TO CHECK AGAINST (each rule has a stable `rule_id` UUID and
may include a regulator_quote — copy that quote verbatim into your output):
{rules_text}

For each violation:
1. rule_id — MUST be one of the UUIDs shown above. Never invent UUIDs. If you
   can't tie a finding to a specific listed rule, do not emit it.
2. category — copy from the rule's section header (lowercase: irdai|sebi|brand|regulatory)
3. severity — lowercase: critical|high|medium|low
4. description — what the violation is, in one sentence
5. current_text — the EXACT problematic phrase from the submission, verbatim
6. suggested_fix — a compliant rewrite of current_text
7. auto_fixable — true only if a simple find-and-replace suffices
8. confidence — your 0.0-1.0 confidence that this is a real violation.
   Use ≥0.9 for blatant violations with regulator backing, 0.7-0.89 for
   clear-but-debatable, 0.5-0.69 for borderline. Anything <0.5 should not
   be emitted at all.
9. regulator_quote — copy the rule's regulator_quote verbatim if shown.
   Leave null only if the rule didn't have one.

Constraints:
- Do not duplicate the same (rule_id, current_text) twice — collapse if the
  same phrase violates the same rule in multiple ways.
- Do not emit a violation just because a rule "could" apply — there must be
  specific text in the document that triggers it.
- Output ONLY valid JSON matching the required schema."""

        return prompt

    def create_precedent_prompts(self, content: str, precedents: List[Dict]) -> str:
        """Build a reviewer-voice precedent prompt (2026-05-28 design).

        The LLM writes commentary as a Bajaj compliance reviewer would write it
        about THIS document — naming the offending phrase, prescribing specific
        compliant text or naming a specific artifact, never as meta-commentary
        on the precedent ("similar to a precedent that…"). Severity, category,
        anchor, comment-verbatim and final-text are still carried over from the
        retrieved precedent by the application; the LLM supplies the on-document
        reviewer_comment, action_type and (when relevant) evidence_needed.

        When NO precedents are retrieved, the prompt switches to a novel-only
        mode: the model reviews the section itself and emits only novel_findings
        (each requiring a regulatory_basis and confidence ≥ 0.75). This closes
        the coverage gap where uncovered chunks previously produced nothing.

        This prompt is product-agnostic — the voice examples below teach STYLE;
        retrieval supplies the substance for whatever product is under review.
        Reviewer names are intentionally NOT included.
        """
        if precedents:
            blocks = []
            for i, p in enumerate(precedents):
                block = (
                    f"\n--- PRECEDENT {i} ---\n"
                    f"Historical chunk: {p.get('chunk_text') or ''}\n"
                    f"Reviewer-flagged phrase (anchor): {p.get('anchor_text') or ''}\n"
                    f"Reviewer comment: {p.get('comment_text') or ''}\n"
                    f"Violation type: {p.get('violation_category') or 'other'}\n"
                    f"Severity: {p.get('severity') or 'informational'}\n"
                )
                if p.get("final_text_chunk"):
                    block += f"Approved rewrite (for reference): {p['final_text_chunk']}\n"
                blocks.append(block)
            precedents_block = "".join(blocks)
            mode_instruction = (
                "(A) Decide which historical PRECEDENTS apply to this chunk. For each\n"
                "    one, write `reviewer_comment` AS THE REVIEWER would write it about\n"
                "    THIS chunk — name the offending phrase, state what's missing or\n"
                "    wrong, and if the past reviewer prescribed specific compliant text\n"
                "    or named a specific artifact, INCLUDE THOSE SPECIFICS. Emit one\n"
                "    `citations` entry per applicable precedent.\n\n"
                "(B) Separately, decide if any issue is clearly present in this chunk\n"
                "    that NO listed precedent covers. Emit those under `novel_findings`.\n"
            )
        else:
            precedents_block = (
                "(none retrieved for this section)\n"
            )
            mode_instruction = (
                "No historical precedents were retrieved for this section. Do NOT emit\n"
                "any `citations`. Review the section yourself and emit ONLY\n"
                "`novel_findings` for issues clearly present in the chunk.\n"
            )

        prompt = f"""You are a senior Bajaj Allianz Life compliance reviewer (Legal/Compliance/FPU).
Your past colleagues' comments on similar copy are below — they show the
substance you should be checking for AND the voice you should write in.

For the NEW DOCUMENT SECTION:

{mode_instruction}
Novel findings REQUIRE a `regulatory_basis` and confidence ≥ 0.75. Do not
invent findings.

DO NOT write meta-bridges like "this chunk is similar to a precedent that…"
or "the precedent flagged X". Write as if YOU are the reviewer reading this
document for the first time. The reader does not see the precedents.

PRECEDENTS:
{precedents_block}
NEW DOCUMENT SECTION:
{content}

ACTION TYPES (pick one per finding):
  rewrite         — use standardized terminology or insert prescribed text
  share-evidence  — produce an approval or source artifact (UW / Tax / PO / BI)
  add-disclaimer  — insert a missing regulatory disclaimer
  verify-source   — clarify provenance, match against authoritative document
  remove          — strip out non-compliant claim

VOICE EXAMPLES (these are the gold standard — match this style):

EXAMPLE 1 (rewrite — prescribes specific text):
  Precedent comment: "Include clear information Switching between fund under
    Investor Selectable Portfolio Strategy or investment portfolio strategies
    is free of the Miscellaneous Charge.. portfolio strategies can be switched
    only during policy anniversary"
  New chunk says: "...allows you to switch between different investment funds
    based on your financial goals and market outlook..."
  reviewer_comment: "Include clear information: switching between funds under
    Investor Selectable Portfolio Strategy is free of the Miscellaneous
    Charge; portfolio strategies can be switched only on policy anniversary."
  action_type: "rewrite"
  evidence_needed: null

EXAMPLE 2 (share-evidence):
  Precedent comment: "Has UW approved this? Pls share approval on tool"
  New chunk says: "...comprehensive life coverage up to ₹3 Crore..."
  reviewer_comment: "Has UW approved the ₹3 Crore SA? Pls share approval on tool."
  action_type: "share-evidence"
  evidence_needed: "UW approval"

EXAMPLE 3 (add-disclaimer):
  Precedent comment: "Lockin- period Ulip disclaimer missing"
  New chunk says: "...invest in our Equity Growth Fund for long-term wealth..."
  reviewer_comment: "ULIP lock-in period disclaimer missing for this Equity
    Growth Fund mention."
  action_type: "add-disclaimer"
  evidence_needed: "ULIP lock-in disclaimer"

EXAMPLE 4 (verify-source):
  Precedent comment: "Pl match it with latest fact sheet"
  New chunk says: "3.47 Crore Lives Covered | 99.33% Claim Settlement Ratio"
  reviewer_comment: "Match these stats with the latest fact sheet before
    publication."
  action_type: "verify-source"
  evidence_needed: "latest fact sheet"

EXAMPLE 5 (novel — no precedent retrieved, expanded reasoning):
  No precedent in the list covers GST claims.
  New chunk says: "GST is not applicable on individual life insurance premium
    as per Government Notification 16/2025."
  reviewer_comment: "Tax claim cites Notification 16/2025 — but this is an
    external regulatory notification, not a Bajaj product feature. Share Tax
    team approval substantiating both the notification number and the scope
    (does it cover ULIP, term, endowment, or all individual life?) before
    publication. If the scope is narrower than implied here, the claim must
    be qualified."
  action_type: "verify-source"
  evidence_needed: "Tax team approval + scope confirmation"
  regulatory_basis: "IRDAI Advertisement Regulations 2021 — tax claim substantiation requirement"
  confidence: 0.85

OUTPUT FIELDS
  citations[]      — precedent_index, current_text (verbatim from the NEW
                     section), reviewer_comment, action_type, evidence_needed,
                     confidence.
  novel_findings[] — current_text, reviewer_comment, action_type,
                     evidence_needed, regulatory_basis, confidence (≥ 0.75).

Return JSON matching the schema. A precedent only applies if the issue it
flagged is genuinely present in the NEW DOCUMENT SECTION; do not cite
precedents that don't apply just because they were retrieved. Output ONLY
valid JSON."""
        return prompt


# Alias for backward compatibility
PreprocessingService = ContextEngineeringService
