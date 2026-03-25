"""
Preprocessing Service: Handles document chunking and context engineering.
Token-based chunking for compliance analysis.
"""
import logging
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
            logger.info(f"Submission {submission_id} already has {existing_chunks} chunks")
            return existing_chunks

        # Update status
        submission.status = "preprocessing"
        self.db.commit()

        # Get content
        content = submission.original_content or ""
        if not content and submission.file_path:
            content = await self._extract_from_file(submission.file_path, submission.content_type)

        if not content:
            logger.warning(f"No content found for submission {submission_id}")
            submission.status = "preprocessed"
            self.db.commit()
            return 0

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
        Uses simple word-based chunking as fallback if tiktoken is unavailable.
        """
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
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
            # Fallback: paragraph-based chunking
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
            elif content_type in ("html", "markdown", "text"):
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
        except Exception as e:
            logger.error(f"Failed to extract content from {file_path}: {e}")
        return ""

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
                    else:
                        rule_text = getattr(rule, "rule_text", str(rule))
                        severity = getattr(rule, "severity", "medium")
                        rule_id = str(getattr(rule, "id", ""))

                    rules_text += f"{i}. [{severity.upper()}] (ID: {rule_id}) {rule_text}\n"

        prompt = f"""Analyze the following document content for regulatory compliance violations.

DOCUMENT CONTENT:
{content}

COMPLIANCE RULES TO CHECK AGAINST:
{rules_text}

For each violation found:
1. Identify the specific rule violated (include rule_id if possible)
2. Describe what the violation is
3. Point to the specific text causing the violation
4. Suggest a fix
5. Rate severity (critical/high/medium/low)
6. Note if it's auto-fixable

Be thorough but precise. Only flag actual violations, not minor stylistic issues."""

        return prompt


# Alias for backward compatibility
PreprocessingService = ContextEngineeringService
