"""LLM summaries for news articles and quiet-hours digests."""
from typing import Dict, List, Optional

from utils import home_log


async def _news_llm(system_prompt_key: str, prompt: str, num_predict: int, label: str) -> Optional[str]:
    """Run a news prompt on the configured news model (or the default chat model), with model fallback."""
    from models import model_manager
    from utils.llm import providers
    from utils.llm_service import try_models_with_fallback, get_enhanced_prompt

    from .store import get_news_model

    provider, model_name = get_news_model()
    if not model_name:
        info = model_manager.get_user_model_info(0)
        provider, model_name = info["provider"], info["model"]
    messages = [
        {"role": "system", "content": get_enhanced_prompt(system_prompt_key)},
        {"role": "user", "content": prompt},
    ]
    try:
        _, response = await try_models_with_fallback(
            model_name,
            messages,
            provider=provider,
            request_options={"temperature": 0.3, "num_predict": num_predict},
        )
    except Exception as e:
        home_log.log_sync(f"⚠️ News {label} error: {e}")
        return None
    if not response or providers.is_error_reply(response):
        home_log.log_sync(f"⚠️ News {label} failed: {response}")
        return None
    return response.strip()


async def summarize_article(article: Dict, detail_level: str = "normal", topic: str = "") -> Optional[str]:
    """Summarize an article using the configured LLM."""
    is_finnish = topic.lower() == "finland"

    if detail_level == "detailed":
        length_instruction = "Keep it concise but complete: around 140-190 words."
    elif detail_level == "brief":
        length_instruction = "Keep it very concise: around 55-80 words."
    else:
        length_instruction = "Keep it concise and informative: around 90-130 words."

    language_note = ""
    if is_finnish:
        language_note = "If the article is in Finnish, keep the summary in Finnish. Otherwise translate to English."
    else:
        language_note = "The summary MUST be in English. Translate if necessary."

    prompt = f"""Summarize this news article for a busy professional. {length_instruction}

{language_note}

Write in a professional, coherent style.
Be factual and objective. Avoid hype, slang, and speculation.

Your response must follow this EXACT structure:
**HEADLINE:** [one-line headline, under 14 words]
[4-6 short bullet points that include: core facts, why this matters, likely implications, and practical context]
**Follow topics:** [3-6 short topic suggestions, comma-separated]

Article title: {article['title']}
Source: {article.get('source', 'Unknown')}
Content: {article.get('summary', 'No content preview available.')}

Do NOT add any introduction or conclusion outside the structure above."""

    return await _news_llm("news_summary", prompt, 800, "summarization")


async def summarize_quiet_time_batch(articles: List[Dict]) -> Optional[str]:
    """Produce a combined summary of articles queued during quiet time."""
    if not articles:
        return None

    article_list = ""
    for i, a in enumerate(articles[:30], 1):
        article_list += f"\n{i}. [{a.get('topic', '?').upper()}] {a.get('title', 'Untitled')} ({a.get('source', '?')})\n   {a.get('summary', '')[:200]}\n"

    prompt = f"""You received {len(articles)} news articles while notifications were paused. Create a structured briefing.

Group them by topic/category. For each group:
- State the category with an emoji
- List the most important developments (combine similar stories)
- Note anything that needs immediate attention

Articles:
{article_list}

Keep the total summary concise but comprehensive. Use bullet points. End with a "🔴 Requires Attention" section if any story seems urgent."""

    return await _news_llm("news_briefing", prompt, 2000, "quiet-time summary")
