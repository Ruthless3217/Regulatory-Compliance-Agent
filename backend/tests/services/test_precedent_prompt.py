import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.preprocessing_service import ContextEngineeringService


def test_precedent_prompt_includes_examples_and_new_section():
    svc = ContextEngineeringService(db=None)
    precedents = [
        {
            "reviewer_name": "Shailja Saklani/Pune HO/Legal Compliance and FPU/Life",
            "chunk_text": "A Rs.1 crore term plan is like any other plan with high sum assured.",
            "comment_text": "Logic is incorrect; do not claim high sum assured.",
            "violation_category": "legal language",
            "severity": "critical",
            "final_text_chunk": "A Rs.1 crore term plan is like any other term plan.",
        }
    ]
    content = "Buy our guaranteed 1 crore plan today!"
    prompt = svc.create_precedent_prompts(content, precedents)
    assert "Shailja Saklani" in prompt
    assert "Logic is incorrect" in prompt
    assert "legal language" in prompt
    assert "critical" in prompt
    assert "NEW DOCUMENT SECTION" in prompt
    assert content in prompt
    assert "current_text" in prompt


def test_precedent_prompt_handles_empty_precedents():
    svc = ContextEngineeringService(db=None)
    prompt = svc.create_precedent_prompts("some text", [])
    assert "NEW DOCUMENT SECTION" in prompt
    assert "some text" in prompt
