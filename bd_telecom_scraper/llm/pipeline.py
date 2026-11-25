"""Incremental, failure-isolated article AI processing pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..filtering import extract_entities
from .client import LocalOllamaClient, OllamaUnavailableError, StructuredLLMError
from .config import LLMConfig, load_llm_config
from .prompts import ARTICLE_SYSTEM_PROMPT, build_article_user_prompt
from .repository import IntelligenceRepository
from .schemas import ArticleIntelligence

logger = logging.getLogger(__name__)

_OPERATOR_ENTITY_SET = frozenset({"Grameenphone", "Robi", "Banglalink", "Teletalk", "BTCL"})


@dataclass
class BatchProcessResult:
    """Outcome summary of an LLM batch processing run."""

    attempted: int = 0
    completed: int = 0
    failed: int = 0
    skipped_locked: bool = False
    ollama_unavailable: bool = False
    model_name: str = ""
    errors: list[str] = field(default_factory=list)


class ArticleIntelligencePipeline:
    """Processes eligible scraped articles through local Ollama with Pydantic validation."""

    def __init__(
        self,
        db_path: str | Path = "data/bd_telecom_news.db",
        *,
        config: LLMConfig | None = None,
        client: LocalOllamaClient | None = None,
    ) -> None:
        self.config = config or load_llm_config()
        self.repo = IntelligenceRepository(db_path)
        self.client = client or LocalOllamaClient(self.config)

    def process_batch(
        self,
        *,
        limit: int | None = None,
        article_ids: list[int] | None = None,
        force_reprocess: bool = False,
    ) -> BatchProcessResult:
        """Process pending/changed articles or explicit `article_ids` with lock & failure isolation."""
        result = BatchProcessResult(model_name=self.config.model_name)
        if not self.repo.acquire_job_lock("article_analysis"):
            result.skipped_locked = True
            result.errors.append("Another AI processing job is already running.")
            return result

        try:
            batch_limit = limit if limit is not None else self.config.batch_size
            articles = self.repo.get_eligible_articles(
                limit=batch_limit,
                max_retries=self.config.max_retries,
                article_ids=article_ids,
                force_reprocess=force_reprocess,
            )
            for art in articles:
                result.attempted += 1
                art_id = int(art["id"])
                content_hash = str(art.get("content_hash") or "")
                raw_content = str(art.get("content") or art.get("summary") or "").strip()

                if len(raw_content) < 40:
                    err = "Article has insufficient text content (< 40 chars) for grounded analysis."
                    self.repo.mark_failed(
                        article_id=art_id,
                        content_hash=content_hash,
                        model_name=self.config.model_name,
                        prompt_version=self.config.prompt_version,
                        error_message=err,
                    )
                    result.failed += 1
                    result.errors.append(f"article_id={art_id}: {err}")
                    continue

                self.repo.mark_processing(art_id, content_hash)
                trimmed = raw_content[: self.config.max_input_chars]
                user_prompt = build_article_user_prompt(
                    publisher=str(art.get("publisher") or "Unknown"),
                    language=str(art.get("language") or "en"),
                    published_at=art.get("published_at"),
                    headline=str(art.get("headline") or ""),
                    content=trimmed,
                )

                try:
                    intel, elapsed_ms, _attempts = self.client.generate_structured(
                        system_prompt=ARTICLE_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        schema_cls=ArticleIntelligence,
                    )
                    # Grounded bilingual entity fusion: never miss operators/regulators explicitly present in source text
                    grounded_entities = extract_entities(f"{art.get('headline') or ''}\n{trimmed}")
                    merged_orgs = list(dict.fromkeys([*intel.organizations, *grounded_entities]))[:15]
                    grounded_ops = [e for e in grounded_entities if e in _OPERATOR_ENTITY_SET]
                    merged_ops = list(dict.fromkeys([*intel.operators_mentioned, *grounded_ops]))[:10]
                    intel = intel.model_copy(
                        update={"organizations": merged_orgs, "operators_mentioned": merged_ops}
                    )
                    self.repo.save_analysis(
                        article_id=art_id,
                        content_hash=content_hash,
                        intel=intel,
                        model_name=self.config.model_name,
                        prompt_version=self.config.prompt_version,
                        processing_ms=elapsed_ms,
                    )
                    result.completed += 1
                    logger.info(
                        "Analyzed article %d (%s) in %dms -> %s [%s]",
                        art_id,
                        art.get("language"),
                        elapsed_ms,
                        intel.main_category,
                        intel.industry_impact,
                    )
                except OllamaUnavailableError as exc:
                    self.repo.mark_failed(
                        article_id=art_id,
                        content_hash=content_hash,
                        model_name=self.config.model_name,
                        prompt_version=self.config.prompt_version,
                        error_message=str(exc),
                    )
                    result.failed += 1
                    result.ollama_unavailable = True
                    result.errors.append(str(exc))
                    break
                except (StructuredLLMError, Exception) as exc:
                    # Isolate failure: record and continue to next article
                    err_msg = f"{type(exc).__name__}: {exc}"
                    self.repo.mark_failed(
                        article_id=art_id,
                        content_hash=content_hash,
                        model_name=self.config.model_name,
                        prompt_version=self.config.prompt_version,
                        error_message=err_msg,
                    )
                    result.failed += 1
                    result.errors.append(f"article_id={art_id}: {err_msg}")
        finally:
            self.repo.release_job_lock("article_analysis")

        return result
