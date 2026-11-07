"""Groq LLM integration for structured incident investigation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from groq import Groq
from pydantic import BaseModel, Field, ValidationError

from ..config import Settings, settings as default_settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when the LLM cannot produce a valid investigation."""


class DraftCause(BaseModel):
    cause: str
    likelihood: str = "medium"
    reasoning: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    category: str | None = None


class DraftEvidence(BaseModel):
    observation: str
    source: str = "ai_inference"
    reference: str | None = None
    memory_id: str | None = None


class DraftAction(BaseModel):
    step: str
    rationale: str = ""
    risk: str = "read_only"
    command: str | None = None


class InvestigationDraft(BaseModel):
    summary: str = Field(min_length=10)
    severity: str = "high"
    probable_causes: list[DraftCause] = Field(min_length=1)
    supporting_evidence: list[DraftEvidence] = Field(default_factory=list)
    recommended_actions: list[DraftAction] = Field(default_factory=list)
    prevention_recommendations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_human_confirmation: bool = True
    memory_assessment: str | None = None


SYSTEM_PROMPT = """You are the investigation engine inside OpsMind AI, an incident response \
assistant for software engineers. You produce structured diagnostic hypotheses from the \
incident report, deployment context, logs and VERIFIED HISTORICAL MEMORIES supplied to you.

Absolute rules:
1. You have NOT inspected any server, dashboard, database or monitoring system. Never claim \
to have observed live state. You only have the text provided in this request.
2. Historical memories are supplied by the Hindsight retrieval layer. Only reference memory \
IDs that appear in the provided memory list. Never invent a memory, an incident ID, a metric \
or a log line.
3. Distinguish evidence types in `supporting_evidence.source`:
   - "incident_report": stated by the reporter (facts from the incident fields)
   - "error_log": verbatim from the provided logs
   - "deployment_context": stated deployment/timing information
   - "historical_memory": from a retrieved memory (set memory_id)
   - "ai_inference": your own reasoning (label hypotheses honestly)
4. probable_causes are HYPOTHESES. Rank them with likelihood high/medium/low and explain the \
reasoning. Never present a hypothesis as confirmed.
5. Never claim a fix was successful. A resolution is only a fact if it appears in a retrieved \
verified-memory.
6. Recommended actions must be safe diagnostics (read-only): inspect logs, metrics, config, \
pool settings, dashboards or read-only queries. Do NOT suggest restarts, deploys, scaling, \
deletions, writes or any destructive production action. Remediation always requires a human.
7. If no relevant memory was retrieved, say so plainly in `memory_assessment` and reduce \
confidence. Do not pretend memory exists.
8. `confidence` is your calibrated confidence (0.0-1.0) that the top hypothesis is correct, \
based only on the supplied evidence and memory relevance.

Return ONLY one JSON object with exactly these keys:
{
  "summary": "2-4 sentence factual incident summary and leading hypothesis",
  "severity": "critical|high|medium|low",
  "probable_causes": [
    {"cause": "...", "likelihood": "high|medium|low", "reasoning": "...",
     "category": "...", "evidence_refs": ["short evidence labels"]}
  ],
  "supporting_evidence": [
    {"observation": "...", "source": "incident_report|error_log|deployment_context|historical_memory|ai_inference",
     "reference": "optional label", "memory_id": "only for historical_memory"}
  ],
  "recommended_actions": [
    {"step": "...", "rationale": "...", "risk": "read_only|low|requires_approval",
     "command": "optional read-only command"}
  ],
  "prevention_recommendations": ["..."],
  "confidence": 0.0,
  "requires_human_confirmation": true,
  "memory_assessment": "whether the retrieved memories are relevant and why"
}"""


def _incident_payload(incident) -> dict[str, Any]:
    return {
        "incident_id": incident.public_id,
        "title": incident.title,
        "service": incident.service,
        "environment": incident.environment,
        "severity_reported": incident.severity,
        "description": incident.description[:4000],
        "error_logs": (incident.error_logs or "")[:5000],
        "deployment_info": (incident.deployment_info or "")[:1500],
        "occurred_at": incident.occurred_at.isoformat() if incident.occurred_at else None,
    }


class GroqLLM:
    provider = "Groq"

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or default_settings

    @property
    def available(self) -> bool:
        return self.cfg.llm_configured

    def _client(self) -> Groq:
        return Groq(
            api_key=self.cfg.groq_api_key,
            base_url=self.cfg.groq_base_url,
            timeout=self.cfg.groq_timeout,
            max_retries=1,
        )

    def investigate(
        self,
        *,
        incident,
        memories: list[dict],
        reflect_text: str | None,
        memory_query: str,
    ) -> tuple[InvestigationDraft, str]:
        if not self.available:
            raise LLMError("GROQ_API_KEY is not configured.")

        memory_block = []
        for index, memory in enumerate(memories[:8], start=1):
            memory_block.append(
                {
                    "memory_id": memory.get("memory_id"),
                    "source_incident": (memory.get("linked_incident") or {}).get("public_id"),
                    "text": (memory.get("text") or "")[:1200],
                    "evidence_chunk": (memory.get("source_chunk") or "")[:1200] or None,
                    "relevance_score": memory.get("score"),
                }
            )

        user_prompt = json.dumps(
            {
                "incident": _incident_payload(incident),
                "hindsight_recall_query": memory_query,
                "hindsight_memories": memory_block,
                "hindsight_reflect_answer": (reflect_text or "")[:3000] or None,
                "note": (
                    "If hindsight_memories is empty, no memory was retrieved. Do not invent one. "
                    "The reflect answer, if present, is Hindsight's synthesis of those memories."
                ),
            },
            indent=2,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            client = self._client()
            response = client.chat.completions.create(
                model=self.cfg.groq_model,
                messages=messages,
                temperature=0.2,
                max_tokens=3200,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Groq request failed: {type(exc).__name__}: {str(exc)[:300]}") from exc

        raw = (response.choices[0].message.content or "").strip()
        try:
            return self._parse(raw), self.cfg.groq_model
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("LLM JSON invalid, retrying once: %s", exc)
            repair_messages = messages + [
                {"role": "assistant", "content": raw[:6000]},
                {
                    "role": "user",
                    "content": (
                        "That response was not valid JSON matching the required schema. "
                        f"Validation error: {str(exc)[:800]}. Return ONLY the corrected JSON "
                        "object with every required key."
                    ),
                },
            ]
            try:
                retry = client.chat.completions.create(
                    model=self.cfg.groq_model,
                    messages=repair_messages,
                    temperature=0.0,
                    max_tokens=3200,
                    response_format={"type": "json_object"},
                )
                return self._parse((retry.choices[0].message.content or "").strip()), self.cfg.groq_model
            except Exception as retry_exc:  # noqa: BLE001
                raise LLMError(f"Groq returned an unusable investigation: {retry_exc}") from retry_exc

    @staticmethod
    def _parse(raw: str) -> InvestigationDraft:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("LLM did not return a JSON object.")
        return InvestigationDraft.model_validate(data)
