import re
from collections import Counter
from typing import Any


CATEGORY_WEIGHTS = {
    "factuality_relevance": 0.35,
    "structure_readability": 0.25,
    "seo_coverage": 0.25,
    "duplication_spam": 0.15,
}

CATEGORY_RUBRIC = {
    "factuality_relevance": "How well content covers title and target keywords with specific language.",
    "structure_readability": "How clear sentence and paragraph structure is for readers.",
    "seo_coverage": "How consistently target keywords appear with healthy distribution.",
    "duplication_spam": "How effectively content avoids repetition and spam patterns.",
}

TITLE_STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "how",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}

SPAM_PHRASES = {
    "click here",
    "buy now",
    "limited time",
    "guaranteed",
    "best ever",
}


def evaluate_generated_content_quality(
    title: str,
    target_keywords: list[str],
    generated_content: str,
) -> dict[str, Any]:
    factuality_relevance_score = score_factuality_relevance(
        title=title,
        target_keywords=target_keywords,
        generated_content=generated_content,
    )
    structure_readability_score = score_structure_readability(generated_content=generated_content)
    seo_coverage_score = score_seo_coverage(
        target_keywords=target_keywords,
        generated_content=generated_content,
    )
    duplication_spam_score = score_duplication_spam(
        target_keywords=target_keywords,
        generated_content=generated_content,
    )

    category_scores = {
        "factuality_relevance": factuality_relevance_score,
        "structure_readability": structure_readability_score,
        "seo_coverage": seo_coverage_score,
        "duplication_spam": duplication_spam_score,
    }

    weighted_aggregate_score = sum(
        category_scores[category_name] * category_weight
        for category_name, category_weight in CATEGORY_WEIGHTS.items()
    )

    return {
        "category_scores": {
            category_name: round(category_score, 4)
            for category_name, category_score in category_scores.items()
        },
        "aggregate_score": round(weighted_aggregate_score, 4),
        "weights": CATEGORY_WEIGHTS,
        "rubric": CATEGORY_RUBRIC,
    }


def score_factuality_relevance(
    title: str,
    target_keywords: list[str],
    generated_content: str,
) -> float:
    normalized_content = _normalize_text(generated_content)
    content_words = _get_words(normalized_content)
    if not content_words:
        return 0.0

    normalized_target_keywords = _normalize_keywords(target_keywords)
    title_terms = [
        word
        for word in _get_words(_normalize_text(title))
        if len(word) > 3 and word not in TITLE_STOP_WORDS
    ]

    keyword_coverage_score = _get_keyword_presence_ratio(
        normalized_target_keywords=normalized_target_keywords,
        normalized_content=normalized_content,
    )

    content_word_set = set(content_words)
    covered_title_terms = sum(1 for title_term in title_terms if title_term in content_word_set)
    title_term_coverage_score = _safe_ratio(covered_title_terms, len(title_terms), fallback=0.5)

    lexical_diversity = _safe_ratio(len(content_word_set), len(content_words))
    lexical_diversity_score = _clamp((lexical_diversity - 0.25) / 0.45)
    length_adequacy_score = _clamp(len(content_words) / 180)

    combined_score = (
        (0.45 * keyword_coverage_score)
        + (0.20 * title_term_coverage_score)
        + (0.15 * lexical_diversity_score)
        + (0.20 * length_adequacy_score)
    )
    return _clamp(combined_score)


def score_structure_readability(generated_content: str) -> float:
    normalized_content = generated_content.strip()
    content_words = _get_words(_normalize_text(normalized_content))
    if not content_words:
        return 0.0

    sentence_count = _count_sentences(normalized_content)
    paragraph_count = _count_paragraphs(normalized_content)
    average_sentence_word_count = _safe_ratio(len(content_words), sentence_count, fallback=0.0)
    contains_punctuation = bool(re.search(r"[.,;:!?]", normalized_content))

    sentence_count_score = _clamp(sentence_count / 6)
    sentence_length_score = _clamp(1 - (abs(average_sentence_word_count - 18) / 18))
    paragraph_score = _clamp(paragraph_count / 3)
    punctuation_score = 1.0 if contains_punctuation else 0.0

    combined_score = (
        (0.35 * sentence_count_score)
        + (0.35 * sentence_length_score)
        + (0.20 * paragraph_score)
        + (0.10 * punctuation_score)
    )
    return _clamp(combined_score)


def score_seo_coverage(target_keywords: list[str], generated_content: str) -> float:
    normalized_content = _normalize_text(generated_content)
    content_words = _get_words(normalized_content)
    if not content_words:
        return 0.0

    normalized_target_keywords = _normalize_keywords(target_keywords)
    keyword_coverage_score = _get_keyword_presence_ratio(
        normalized_target_keywords=normalized_target_keywords,
        normalized_content=normalized_content,
    )

    keyword_occurrence_count = sum(
        normalized_content.count(normalized_target_keyword)
        for normalized_target_keyword in normalized_target_keywords
    )
    keyword_frequency_per_hundred_words = (
        _safe_ratio(keyword_occurrence_count, len(content_words)) * 100
    )
    frequency_score = _clamp(1 - (abs(keyword_frequency_per_hundred_words - 2.5) / 2.5))

    intro_character_limit = max(int(len(normalized_content) * 0.2), 1)
    intro_section = normalized_content[:intro_character_limit]
    has_keyword_in_intro = any(
        normalized_target_keyword in intro_section
        for normalized_target_keyword in normalized_target_keywords
    )
    intro_score = 1.0 if has_keyword_in_intro else 0.0

    length_score = _score_word_count_for_seo(len(content_words))

    combined_score = (
        (0.40 * keyword_coverage_score)
        + (0.25 * frequency_score)
        + (0.20 * intro_score)
        + (0.15 * length_score)
    )
    return _clamp(combined_score)


def score_duplication_spam(target_keywords: list[str], generated_content: str) -> float:
    normalized_content = _normalize_text(generated_content)
    content_words = _get_words(normalized_content)
    if not content_words:
        return 0.0

    sentences = _get_sentences(generated_content)
    unique_sentence_count = len(set(sentences))
    sentence_duplication_ratio = 1 - _safe_ratio(unique_sentence_count, len(sentences), fallback=1.0)

    unique_word_ratio = _safe_ratio(len(set(content_words)), len(content_words))
    repeated_word_penalty = 1 - unique_word_ratio
    most_common_word_count = Counter(content_words).most_common(1)[0][1]
    most_common_word_ratio = _safe_ratio(most_common_word_count, len(content_words))
    repeated_word_dominance_penalty = _clamp((most_common_word_ratio - 0.08) / 0.22)

    normalized_target_keywords = _normalize_keywords(target_keywords)
    repeated_keyword_count = sum(
        1
        for normalized_target_keyword in normalized_target_keywords
        if normalized_content.count(normalized_target_keyword) > 3
    )
    repeated_keyword_penalty = _safe_ratio(
        repeated_keyword_count,
        len(normalized_target_keywords),
        fallback=0.0,
    )

    exclamation_penalty = _clamp(generated_content.count("!") / 10)
    spam_phrase_count = sum(
        normalized_content.count(spam_phrase)
        for spam_phrase in SPAM_PHRASES
    )
    spam_phrase_penalty = _clamp(spam_phrase_count / 6)
    short_content_penalty = _clamp(1 - _score_word_count_for_seo(len(content_words)))

    combined_penalty = (
        (0.25 * sentence_duplication_ratio)
        + (0.20 * repeated_word_penalty)
        + (0.20 * repeated_word_dominance_penalty)
        + (0.15 * repeated_keyword_penalty)
        + (0.10 * short_content_penalty)
        + (0.05 * exclamation_penalty)
        + (0.05 * spam_phrase_penalty)
    )
    return _clamp(1 - combined_penalty)


def _get_keyword_presence_ratio(
    normalized_target_keywords: list[str],
    normalized_content: str,
) -> float:
    covered_keyword_count = sum(
        1
        for normalized_target_keyword in normalized_target_keywords
        if normalized_target_keyword in normalized_content
    )
    return _safe_ratio(covered_keyword_count, len(normalized_target_keywords), fallback=0.0)


def _score_word_count_for_seo(word_count: int) -> float:
    if word_count < 120:
        return _clamp(word_count / 120)
    if 120 <= word_count <= 450:
        return 1.0
    if word_count <= 900:
        return _clamp(1 - ((word_count - 450) / 900))
    return 0.5


def _normalize_keywords(target_keywords: list[str]) -> list[str]:
    normalized_target_keywords = []
    for target_keyword in target_keywords:
        normalized_target_keyword = _normalize_text(target_keyword)
        if normalized_target_keyword:
            normalized_target_keywords.append(normalized_target_keyword)

    return normalized_target_keywords


def _get_sentences(text: str) -> list[str]:
    sentence_candidates = re.split(r"[.!?]+", text)
    sentences = [sentence.strip().lower() for sentence in sentence_candidates if sentence.strip()]
    return sentences or []


def _count_sentences(text: str) -> int:
    return len(_get_sentences(text))


def _count_paragraphs(text: str) -> int:
    paragraphs = [paragraph for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    return len(paragraphs) or 1


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _get_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _safe_ratio(numerator: int | float, denominator: int | float, fallback: float = 0.0) -> float:
    if denominator == 0:
        return fallback
    return numerator / denominator


def _clamp(value: float, lower_bound: float = 0.0, upper_bound: float = 1.0) -> float:
    return max(lower_bound, min(upper_bound, value))
