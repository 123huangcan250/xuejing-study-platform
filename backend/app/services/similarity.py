"""仿真题防抄袭：新题不能在措辞、场景、数据上像真题。三把尺子，任何一把超线就判“太像”：

- 字符 3 元组 Jaccard：抓“换了几个词但句子骨架一样”；
- 最长公共子序列比（difflib）：抓整体顺序相同的改写；
- 数字重合：抓“场景换了但已知数据原样照搬”。
考点相同的题自然会共用一些专业词汇，所以阈值放在“明显照搬”的位置，而不是“有点像”。
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

NGRAM_MAX = 0.30
SEQ_MAX = 0.55
NUM_MAX = 0.60
MIN_NUMBERS = 3  # 题里至少有这么多个数字，数字重合才有意义


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).lower()
    t = re.sub(r"\$+|\\[a-z]+|[{}]", "", t)  # LaTeX 标记不算内容
    return re.sub(r"[\s\W_]+", "", t)


def _grams(t: str, n: int = 3) -> set[str]:
    return {t[i:i + n] for i in range(len(t) - n + 1)} if len(t) >= n else {t} if t else set()


def _numbers(text: str) -> set[str]:
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"[（(]\s*\d{1,2}\s*[)）]|^\s*\d{1,2}[.、]", " ", t, flags=re.M)  # 题号、小问号不算数据
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in re.findall(r"\d+(?:\.\d+)?", t)}


def compare(a: str, b: str) -> dict:
    na, nb = _norm(a), _norm(b)
    ga, gb = _grams(na), _grams(nb)
    ngram = len(ga & gb) / len(ga | gb) if ga and gb else 0.0
    seq = 0.0
    if na and nb:
        sm = SequenceMatcher(None, na[:700], nb[:700], autojunk=False)
        if sm.real_quick_ratio() > SEQ_MAX * 0.6 and sm.quick_ratio() > SEQ_MAX * 0.6:
            seq = sm.ratio()
    xa, xb = _numbers(a), _numbers(b)
    num = len(xa & xb) / len(xa | xb) if len(xa) >= MIN_NUMBERS and len(xb) >= MIN_NUMBERS else 0.0
    return {"ngram": round(ngram, 3), "seq": round(seq, 3), "numbers": round(num, 3),
            "too_similar": ngram >= NGRAM_MAX or seq >= SEQ_MAX or num >= NUM_MAX,
            "score": round(max(ngram / NGRAM_MAX, seq / SEQ_MAX, num / NUM_MAX) * 0.5, 3)}  # 0.5 = 刚好到线；越低越不像


def check_against(text: str, references: list[tuple[int, str]]) -> dict:
    """和一批真题逐一比较，返回最相似的那道：{"score","too_similar","ref_id","detail"}。"""
    best = {"score": 0.0, "too_similar": False, "ref_id": None, "detail": {"ngram": 0.0, "seq": 0.0, "numbers": 0.0}}
    for rid, ref in references:
        r = compare(text, ref)
        if r["too_similar"] and not best["too_similar"] or r["score"] > best["score"] and r["too_similar"] == best["too_similar"]:
            best = {"score": r["score"], "too_similar": r["too_similar"], "ref_id": rid, "detail": r}
    return best
