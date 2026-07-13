"""
text_filter.py — морфологическая фильтрация и работа с ключевыми словами.

Отвечает за:
- Лемматизацию текстов (pymorphy3/pymorphy2 с fallback)
- _find_matched_keys() — поиск ключей через леммы
- find_key_mentions() — буквальный поиск ключей в тексте (второй уровень)
- _passes_filters() — полная проверка поста на соответствие фильтрам
- _text_similarity() — сравнение текстов через биграммы Дайса
- _text_fingerprint() — fingerprint текста для дедупликации
"""

import re
import logging
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Вспомогательные функции ───────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text)


def _text_similarity(a: str, b: str) -> float:
    """Схожесть двух строк через биграммы (Dice coefficient)."""
    def bigrams(s: str) -> set:
        return {s[i:i+2] for i in range(len(s) - 1)}
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


# ── Класс фильтра ─────────────────────────────────────────────────────────────

class TextFilter:
    """
    Инкапсулирует всю логику фильтрации постов.
    Принимает конфиг и key_tags при инициализации.
    """

    def __init__(self, key_tags: dict, morph=None):
        self.key_tags = key_tags
        self.morph = morph

        self._lemma_cache: dict[str, str] = {}
        self._key_lemmas:   dict[str, frozenset[str]] = {}
        self._minus_lemmas: dict[str, list[frozenset[str]]] = {}

        if key_tags:
            self._precompute_key_lemmas()

    # ── Лемматизация ──────────────────────────────────────────────────────────

    def _lemmatize_word(self, word: str) -> str:
        if word in self._lemma_cache:
            return self._lemma_cache[word]
        if self.morph:
            lemma = self.morph.parse(word)[0].normal_form
        else:
            lemma = word.lower()
        self._lemma_cache[word] = lemma
        return lemma

    def _lemmatize_phrase(self, phrase: str) -> frozenset[str]:
        words = re.findall(r'[а-яёa-z]+', phrase.lower())
        return frozenset(self._lemmatize_word(w) for w in words if w)

    def _lemmatize_text(self, text: str) -> frozenset[str]:
        words = re.findall(r'[а-яёa-z]+', _strip_html(text).lower())
        return frozenset(self._lemmatize_word(w) for w in words if w)

    def _precompute_key_lemmas(self):
        """Предвычисляет леммы ключей и минус-слов при старте."""
        for key, meta in self.key_tags.items():
            phrase = key.replace('_', ' ')
            self._key_lemmas[key] = self._lemmatize_phrase(phrase)
            self._minus_lemmas[key] = [
                self._lemmatize_phrase(mw) for mw in meta.get("minus", [])
            ]
        logger.info(f"Key lemmas precomputed: {len(self._key_lemmas)} keys")

    # ── Поиск ключей ──────────────────────────────────────────────────────────

    def find_matched_keys(self, post: dict) -> list[str]:
        """
        Уровень 1: ищет ключи через леммы.
        Возвращает список совпавших ключей.
        """
        if not self.key_tags:
            return []

        text = _strip_html(post.get("text", "") or "")
        if self.morph:
            text_lemmas = post.get("__lemmas") or self._lemmatize_text(text)
            post["__lemmas"] = text_lemmas
        else:
            text_lower = text.lower()

        matched = []
        for key, key_lemmas in self._key_lemmas.items():
            if not key_lemmas:
                continue
            if self.morph:
                if not key_lemmas.issubset(text_lemmas):
                    continue
                # Проверяем минус-слова
                has_minus = any(
                    ml.issubset(text_lemmas)
                    for ml in self._minus_lemmas.get(key, [])
                    if ml
                )
            else:
                key_str = key.lower().replace('_', ' ')
                if key_str not in text_lower:
                    continue
                meta = self.key_tags[key]
                has_minus = any(mw in text_lower for mw in meta.get("minus", []))

            if not has_minus:
                matched.append(key)

        return matched

    def find_key_mentions(self, text: str) -> list[dict]:
        """
        Уровень 2: буквальный поиск ключей в тексте по предложениям.
        Возвращает список совпадений с контекстом для формирования цитаты.
        """
        if not self.key_tags or not text:
            return []

        sentences = self.extract_sentences(text)
        mentions = []

        for i, sent in enumerate(sentences):
            sent_lower = sent.lower()
            for key, meta in self.key_tags.items():
                key_search = key.lower().replace('_', ' ')
                if key_search not in sent_lower:
                    continue
                if any(mw in sent_lower for mw in meta.get("minus", [])):
                    continue
                mentions.append({
                    "key":      key,
                    "tag":      meta["tag"],
                    "tags":     [meta["tag"]],
                    "sentence": sent,
                    "index":    i,
                    "context":  " ".join(sentences[max(0, i-1):i+2]),
                })

        return mentions

    def extract_sentences(self, text: str) -> list[str]:
        """Разбивает текст на предложения."""
        clean = _strip_html(text)
        parts = re.split(r'(?<=[.!?])\s+|[\n]{2,}', clean)
        return [p.strip() for p in parts if p.strip()]

    # ── Fingerprint текста ────────────────────────────────────────────────────

    def text_fingerprint(self, text: str) -> tuple[int, str]:
        """
        Возвращает (hash, clean_text) для дедупликации.
        Нормализует текст перед хэшированием.
        """
        clean = re.sub(r'\s+', ' ', _strip_html(text or "")).strip().lower()
        return hash(clean), clean

    # ── Посевы (seen_texts) ───────────────────────────────────────────────────

    def find_similar_seen(
        self,
        text: str,
        seen_texts: dict,
        threshold: float,
    ) -> dict | None:
        """
        Ищет похожий текст среди seen_texts через биграммы Дайса.
        Возвращает запись если схожесть >= threshold, иначе None.
        """
        _, clean = self.text_fingerprint(text)
        if not clean:
            return None
        for fp, entry in seen_texts.items():
            prev_text = entry.get("text_preview", "")
            if not prev_text:
                continue
            sim = _text_similarity(clean, prev_text)
            if sim >= threshold:
                return entry
        return None

    # ── Основной фильтр ───────────────────────────────────────────────────────

    def passes_key_filter(
        self,
        post: dict,
        url: str,
        subs: int,
        log_skip_fn,
    ) -> bool:
        """
        Двухуровневая проверка ключей:
        1. Леммы — находит кандидатов
        2. Буквальный поиск — подтверждает что ключ реально есть в тексте

        log_skip_fn(url, reason, subs, keys) — колбэк для логирования скипов.
        """
        if not self.key_tags:
            return True

        matched = self.find_matched_keys(post)
        if not matched:
            # Логируем минус-слова
            raw_text = _strip_html(post.get("text", "") or "")
            if self.morph:
                text_lemmas = post.get("__lemmas") or self._lemmatize_text(raw_text)
                for key, key_lemmas in self._key_lemmas.items():
                    if key_lemmas and key_lemmas.issubset(text_lemmas):
                        for ml in self._minus_lemmas.get(key, []):
                            if ml.issubset(text_lemmas):
                                log_skip_fn(url, f"minus_word({key})", subs=subs, keys=[key])
                                break
            else:
                text_lower = raw_text.lower()
                for key, meta in self.key_tags.items():
                    if key.lower().replace('_', ' ') in text_lower:
                        hit = next((mw for mw in meta.get("minus", []) if mw in text_lower), None)
                        if hit:
                            log_skip_fn(url, f"minus_word({key}:{hit})", subs=subs, keys=[key])

            log_skip_fn(url, "no_keys", subs=subs)
            return False

        # Уровень 2: буквальный поиск
        mentions = self.find_key_mentions(post.get("text", "") or "")
        if not mentions:
            log_skip_fn(url, "no_key_in_text(lemma_only)", subs=subs, keys=matched)
            return False

        return True
