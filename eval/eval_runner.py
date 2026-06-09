#!/usr/bin/env python3
"""
Agent EVAL Runner — A/B blind comparison of main vs embedding-branch Agent.

Usage:
  python eval/eval_runner.py --seed-vault <path> --video-list <path> --output-dir <path>

For each video in the list:
  1. Creates isolated vaults (main_vault, emb_vault) copied from seed vault
  2. Runs main-branch pipeline (no embedding) on one, embedding-branch on the other
  3. Extracts outputs, calls LLM Reviewer for blind comparison
  4. Saves per-pair review JSON
After all pairs: runs aggregator to produce summary.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from openai import OpenAI  # noqa: E402
from src.models.config import EmbeddingConfig, LLMConfig, PipelineConfig  # noqa: E402
from src.services.asr_service import ASREngine  # noqa: E402
from src.services.pipeline_service import process_pipeline  # noqa: E402
from src.utils.db import DatabaseManager  # noqa: E402

from eval.aggregator import VideoPairResult, aggregate_results, decide_merge  # noqa: E402
from eval.reviewer_prompt import (  # noqa: E402
    build_system_prompt,
    build_user_message,
    parse_review_response,
)

logger = logging.getLogger("vidbrain.eval")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


def _copy_vault(src: str, dst: str) -> None:
    """Copy vault directory contents from src to dst."""
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.mkdir(parents=True, exist_ok=True)
    if src_path.exists():
        for item in src_path.iterdir():
            s = src_path / item.name
            d = dst_path / item.name
            if item.is_dir():
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)


def _extract_links(markdown: str) -> list[str]:
    """Extract [[wikilinks]] from markdown content."""
    return re.findall(r"\[\[([^\]]+)\]\]", markdown)


def _extract_markdown_without_frontmatter(full_content: str) -> str:
    """Remove YAML front-matter from note content."""
    if full_content.startswith("---"):
        end = full_content.find("---", 3)
        if end != -1:
            return full_content[end + 3:].strip()
    return full_content


def _call_reviewer(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_message: str,
) -> dict | None:
    """Call the LLM Reviewer and return parsed JSON, or None on failure."""
    for attempt in range(1, 3):
        temperature = 0.1 if attempt == 1 else 0.0
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                timeout=120,
            )
            raw = response.choices[0].message.content or ""
            result = parse_review_response(raw)
            if result is not None:
                return result
            logger.warning("Reviewer 返回无效 JSON (尝试 %d/2)", attempt)
        except Exception as e:
            logger.warning("Reviewer API 调用失败 (尝试 %d/2): %s", attempt, str(e))
            if attempt < 2:
                time.sleep(2)
    return None


def run_single_pipeline(
    video_path: str,
    vault_dir: str,
    db_path: str,
    llm_config: LLMConfig,
    asr_engine: ASREngine,
    embedding_enabled: bool = False,
) -> Path | None:
    """Run pipeline for one video, return the output .md file path or None."""
    video_name = Path(video_path).name
    video_id = Path(video_path).stem

    db = DatabaseManager(db_path)
    db.create_task(video_id, video_name, str(video_path))

    cpu_count = os.cpu_count() or 4
    cfg = PipelineConfig(
        input_dir=str(Path(video_path).parent),
        vault_dir=vault_dir,
        db_path=db_path,
        model_size="tiny",
        cpu_threads=max(1, cpu_count - 2),
        batch_size=1,
        once=True,
        embedding_enabled=embedding_enabled,
    )

    emb_config = None
    if embedding_enabled:
        try:
            emb_config = EmbeddingConfig()
        except OSError as e:
            logger.warning("Embedding 配置失败: %s", str(e))
            return None

    try:
        process_pipeline(
            video_id=video_id,
            video_name=video_name,
            file_path=str(video_path),
            db=db,
            asr_engine=asr_engine,
            llm_config=llm_config,
            cfg=cfg,
            embedding_config=emb_config,
        )
    except Exception as e:
        logger.error("管道失败 (%s): %s", video_name, str(e))
        return None

    output_stem = Path(video_name).stem
    output_path = Path(vault_dir) / f"{output_stem}.md"
    if output_path.exists():
        return output_path
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent EVAL Runner")
    parser.add_argument("--seed-vault", required=True, help="Seed vault directory (baseline knowledge)")
    parser.add_argument("--video-list", required=True, help="File with video paths, one per line")
    parser.add_argument("--output-dir", default="./eval_results", help="Output directory for results")
    parser.add_argument("--asr-model", default="tiny", help="Whisper model size")
    parser.add_argument("--reviewer-model", default=None, help="LLM model for reviewing (default: same as pipeline LLM)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    reviews_dir = output_dir / "reviews"
    reviews_dir.mkdir(exist_ok=True)

    # Read video list
    with open(args.video_list, encoding="utf-8") as f:
        videos = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not videos:
        logger.error("视频列表为空")
        sys.exit(1)

    # Init shared services
    llm_config = LLMConfig()
    reviewer_model = args.reviewer_model or llm_config.model
    reviewer_client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
    asr_engine = ASREngine(
        model_size=args.asr_model,
        cpu_threads=max(1, (os.cpu_count() or 4) - 2),
    )

    results: list[VideoPairResult] = []
    skipped: list[dict] = []

    # Determine if seed vault is empty (new domain indicator)
    seed_vault_path = Path(args.seed_vault)
    seed_has_notes = seed_vault_path.exists() and list(seed_vault_path.glob("*.md"))

    for i, video_path in enumerate(videos):
        logger.info("=== 视频 %d/%d: %s ===", i + 1, len(videos), video_path)

        if not Path(video_path).exists():
            logger.warning("跳过不存在的文件: %s", video_path)
            skipped.append({"video": video_path, "reason": "file_not_found"})
            continue

        # Create isolated vaults
        work_dir = Path(tempfile.mkdtemp(prefix="eval_"))
        main_vault = work_dir / "main_vault"
        emb_vault = work_dir / "emb_vault"
        main_db = work_dir / "main.db"
        emb_db = work_dir / "emb.db"

        try:
            _copy_vault(args.seed_vault, str(main_vault))
            _copy_vault(args.seed_vault, str(emb_vault))

            # Randomize execution order
            emb_first = random.choice([True, False])

            if emb_first:
                emb_output = run_single_pipeline(
                    video_path, str(emb_vault), str(emb_db),
                    llm_config, asr_engine, embedding_enabled=True,
                )
                main_output = run_single_pipeline(
                    video_path, str(main_vault), str(main_db),
                    llm_config, asr_engine, embedding_enabled=False,
                )
            else:
                main_output = run_single_pipeline(
                    video_path, str(main_vault), str(main_db),
                    llm_config, asr_engine, embedding_enabled=False,
                )
                emb_output = run_single_pipeline(
                    video_path, str(emb_vault), str(emb_db),
                    llm_config, asr_engine, embedding_enabled=True,
                )

            if main_output is None or emb_output is None:
                logger.warning("跳过: 管道输出缺失")
                skipped.append({"video": video_path, "reason": "pipeline_failure"})
                continue

            # Read and extract outputs
            main_full = main_output.read_text(encoding="utf-8", errors="replace")
            emb_full = emb_output.read_text(encoding="utf-8", errors="replace")
            main_md = _extract_markdown_without_frontmatter(main_full)
            emb_md = _extract_markdown_without_frontmatter(emb_full)
            main_links = _extract_links(main_md)
            emb_links = _extract_links(emb_md)

            is_new_domain = not seed_has_notes

            # Randomize A/B labels for blind review
            emb_is_a = random.choice([True, False])
            if emb_is_a:
                note_a_content = emb_md
                note_a_links = emb_links
                note_b_content = main_md
                note_b_links = main_links
            else:
                note_a_content = main_md
                note_a_links = main_links
                note_b_content = emb_md
                note_b_links = emb_links

            user_msg = build_user_message(
                raw_text="(ASR text not separately archived — see note content)",
                related_notes_summary="",
                note_a_content=note_a_content[:5000],
                note_a_links=note_a_links,
                note_a_suggestions=[],
                note_b_content=note_b_content[:5000],
                note_b_links=note_b_links,
                note_b_suggestions=[],
            )

            review = _call_reviewer(
                reviewer_client, reviewer_model,
                build_system_prompt(), user_msg,
            )

            if review is None:
                logger.warning("评审失败: %s", video_path)
                skipped.append({"video": video_path, "reason": "review_failure"})
                continue

            # Save review
            review_path = reviews_dir / f"{Path(video_path).stem}_review.json"
            review_path.write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            pair_result = VideoPairResult(
                video_name=Path(video_path).name,
                is_new_domain=is_new_domain,
                review_a_is_emb=emb_is_a,
                review=review,
            )
            results.append(pair_result)
            logger.info("评审完成: %s", Path(video_path).name)

        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    # Aggregation
    summary = aggregate_results(results)
    conclusion = decide_merge(summary)
    summary["conclusion"] = conclusion
    summary["skipped_count"] = len(skipped)
    summary["skipped"] = skipped
    summary["generated_at"] = datetime.now().isoformat()

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info("=== EVAL 完成 ===")
    logger.info("处理: %d, 跳过: %d, 结论: %s", len(results), len(skipped), conclusion)

    # Print key stats
    print(f"\n{'='*60}")
    print("Agent EVAL 结果")
    print(f"{'='*60}")
    print(f"总视频对: {summary['total_pairs']}")
    print(f"维度 A (术语) 平均分差: {summary['dim_A_avg_diff']:+.2f}")
    print(f"维度 B (双链) 平均分差: {summary['dim_B_avg_diff']:+.2f}")
    print(f"维度 C (更新) 平均分差: {summary['dim_C_avg_diff']:+.2f}")
    print(f"维度 D (综合) emb 胜出: {summary['dim_D_emb_win_pct']:.0%}")
    print(f"  已有领域: {summary['dim_D_emb_win_pct_existing']:.0%}")
    print(f"  新领域:   {summary['dim_D_emb_win_pct_new']:.0%}")
    if summary.get("self_doubt_flags"):
        print(f"低置信维度: {list(summary['self_doubt_flags'].keys())}")
    print(f"\n结论: {conclusion}")


if __name__ == "__main__":
    main()
