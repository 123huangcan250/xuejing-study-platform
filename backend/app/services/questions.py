"""题库：按知识点出题、作答判分、错题进闪卡、知识点掌握度、练习队列。

- 出题：以知识梳理里的「知识点」为单位，把知识点概要 + 课件原文摘录交给模型，按指定题型 / 难度出题；
  模型输出经过严格校验（选项数、答案字母、空数等），不合格的丢弃，不会把坏题放进题库。
- 判分：选择 / 判断 / 填空本地自动判分（秒出结果、不花 token）；简答 / 计算 / 证明由模型按评分要点打分，
  模型不可用时退回「对照参考答案自评」。
- 错题：第一次做错的题自动进闪卡（FSRS 之后会按遗忘曲线再来问你）。
- 掌握度：每个知识点按最近作答加权（近的权重大），再用先验收缩，做得少时不会因为一两题就判成 0 或 100%。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from ..db import engine
from ..models import Attempt, Document, Flashcard, KnowledgeNode, Question
from . import fsrs_service
from .blockstore import ensure_blocks
from .knowledge import page_texts, parse_json, to_int
from .translate import TranslateError, call_llm

log = logging.getLogger("app.questions")

QTYPES = ("choice", "multi", "judge", "fill", "short", "calc", "proof")
QTYPE_NAME = {"choice": "单选题", "multi": "多选题", "judge": "判断题", "fill": "填空题", "short": "简答题", "calc": "计算题", "proof": "证明题"}
OBJECTIVE = {"choice", "multi", "judge", "fill"}
ABILITIES = ("记忆", "理解", "应用", "分析", "综合")
DEFAULT_TYPES = ("choice", "judge", "fill", "short", "calc")
LETTERS = "ABCDEF"
CONCURRENCY = 3
MAX_NODES = 24
EXCERPT_CHARS = 1500
WRONG_BELOW = 0.6  # 得分低于它算“做错”

FORMAT_RULES = """输出 JSON：{"questions":[{"i":序号,"type":"choice|multi|judge|fill|short|calc|proof","stem":"题干","options":["选项文字",...],"answer":"...","explanation":"解析","key_points":["评分要点"],"difficulty":1到5,"ability":"记忆|理解|应用|分析|综合"}]}
格式规则：
1. choice（单选）：恰好 4 个选项，选项文字不要带 A. B. 前缀，answer 是单个字母；干扰项要似是而非，不能一眼排除；
2. multi（多选）：4~5 个选项，answer 是所有正确选项的字母，如 "AC"；
3. judge（判断）：stem 是一个陈述句，answer 只能是 "对" 或 "错"；错的要错在关键概念上；
4. fill（填空）：stem 里用 ____ 表示空，answer 是 JSON 数组，每个元素对应一个空，同一个空允许的多种写法用 | 分隔，如 ["牛顿-拉夫逊法|牛顿拉夫逊法"]；
5. short / calc / proof：answer 是完整参考答案（计算题要有步骤和数值结果），key_points 是 2~5 条评分要点；
6. 公式用 LaTeX（行内 $...$）；计算题的数据要自洽、结果合理；
7. explanation 用一两句话说明为什么，并指出常见错误。"""

GEN_SYSTEM = """你是理工科课程的出题老师，要根据给定知识点的课件内容出练习题，帮学生复习。
严格按「出题要求」里每个序号指定的题型和难度出题；题目只考给定内容和本学科常识，不要出课件里没有的东西。
""" + FORMAT_RULES

JUDGE_TRUE = {"对", "正确", "√", "✓", "t", "true", "是", "yes", "y"}
JUDGE_FALSE = {"错", "错误", "×", "✗", "f", "false", "否", "no", "n"}


# ----------------------------------------------------------------- 文本工具
def norm_text(s: str) -> str:
    t = unicodedata.normalize("NFKC", str(s)).lower()
    return re.sub(r"[\s，。,.;；:：、'\"“”‘’()（）\[\]【】]", "", t)


def stem_key(stem: str) -> str:
    return norm_text(stem)[:60]


def qtype_of_exam(qtype: str, text: str = "") -> str:
    """真题里的题型（选择/填空/…）→ 题库题型。"""
    if qtype == "选择":
        return "multi" if re.search(r"多选|不定项", text[:80]) else "choice"
    return {"填空": "fill", "判断": "judge", "简答": "short", "计算": "calc", "证明": "proof", "分析": "short", "综合": "short"}.get(qtype, "short")


# ----------------------------------------------------------------- 出题结果校验
def clean_generated(it: dict, want_type: str | None = None) -> dict | None:
    """把模型给的一道题校验、规整成 Question 字段；不合格返回 None。"""
    if not isinstance(it, dict):
        return None
    qtype = str(it.get("type") or want_type or "").strip().lower()
    if qtype not in QTYPES:
        return None
    if want_type and qtype != want_type:
        return None  # 模型没按要求的题型出
    stem = str(it.get("stem") or "").strip()
    if len(stem) < 6:
        return None
    ans = it.get("answer")
    options: list[str] = []
    key_points = [str(x).strip() for x in (it.get("key_points") or []) if str(x).strip()][:6] if isinstance(it.get("key_points"), list) else []
    if qtype in ("choice", "multi"):
        opts = it.get("options")
        if not isinstance(opts, list):
            return None
        options = [re.sub(r"^\s*[A-Fa-f][.、:：)）]\s*", "", str(o)).strip() for o in opts]
        if not 3 <= len(options) <= 6 or any(not o for o in options) or len({norm_text(o) for o in options}) != len(options):
            return None
        letters = sorted({c for c in str(ans).upper() if c in LETTERS[: len(options)]})
        if not letters or (qtype == "choice" and len(letters) != 1) or (qtype == "multi" and len(letters) < 2):
            return None
        answer = "".join(letters)
    elif qtype == "judge":
        a = norm_text(ans if not isinstance(ans, bool) else ("对" if ans else "错"))
        answer = "对" if a in JUDGE_TRUE else "错" if a in JUDGE_FALSE else ""
        if not answer:
            return None
    elif qtype == "fill":
        arr = ans if isinstance(ans, list) else [ans] if ans not in (None, "") else []
        arr = [str(x).strip() for x in arr if str(x).strip()]
        blanks = len(re.findall(r"_{2,}|＿{2,}|（\s*）|\(\s*\)", stem))
        if not arr or blanks == 0 or blanks != len(arr):
            return None  # 题干里的空数和答案数对不上
        answer = json.dumps(arr, ensure_ascii=False)
    else:
        answer = str(ans or "").strip()
        if len(answer) < 4:
            return None
    diff = to_int(it.get("difficulty"))
    return {"qtype": qtype, "stem": stem, "options_json": json.dumps(options, ensure_ascii=False) if options else "",
            "answer": answer, "key_points_json": json.dumps(key_points, ensure_ascii=False) if key_points else "",
            "explanation": str(it.get("explanation") or "").strip(), "difficulty": diff if diff in (1, 2, 3, 4, 5) else 3,
            "ability": it.get("ability") if it.get("ability") in ABILITIES else ""}


# ----------------------------------------------------------------- 知识点与上下文
def leaves_for(s: Session, course_id: int, node_ids: list[int] | None) -> list[KnowledgeNode]:
    """把任意层级的节点展开成「知识点」（level 3）。没指定就是全部知识点。"""
    nodes = list(s.exec(select(KnowledgeNode).where(KnowledgeNode.course_id == course_id)).all())
    kids: dict[int | None, list[KnowledgeNode]] = {}
    for n in nodes:
        kids.setdefault(n.parent_id, []).append(n)
    by_id = {n.id: n for n in nodes}
    if not node_ids:
        return [n for n in nodes if n.level == 3]
    out, seen = [], set()

    def walk(n: KnowledgeNode):
        if n.level == 3:
            if n.id not in seen:
                seen.add(n.id)
                out.append(n)
            return
        for c in sorted(kids.get(n.id, []), key=lambda x: x.order):
            walk(c)

    for i in node_ids:
        if i in by_id:
            walk(by_id[i])
    return out


def node_path(s: Session, n: KnowledgeNode, cache: dict[int, KnowledgeNode] | None = None) -> str:
    parts, cur = [n.title], n
    for _ in range(3):
        parent = (cache or {}).get(cur.parent_id) if cache is not None else (s.get(KnowledgeNode, cur.parent_id) if cur.parent_id else None)
        if not parent:
            break
        parts.append(parent.title)
        cur = parent
    return " > ".join(reversed(parts))


def source_excerpt(s: Session, n: KnowledgeNode, cache: dict[int, dict[int, str]]) -> str:
    """知识点出处页的课件原文（前 EXCERPT_CHARS 字）。"""
    refs = json.loads(n.refs_json or "[]")[:3]
    out: list[str] = []
    for r in refs:
        did = r.get("document_id") or n.document_id
        if did not in cache:
            doc = s.get(Document, did)
            try:
                cache[did] = page_texts(ensure_blocks(s, doc)) if doc else {}
                s.commit()
            except Exception as e:  # noqa: BLE001  原文取不到（云盘断网、文件被删）时照样能凭知识点概要出题
                s.rollback()
                log.warning("读取课件原文失败，只用知识点概要出题：%s", e)
                cache[did] = {}
        t = cache[did].get(int(r.get("page", 0)))
        if t:
            out.append(f"[第{r['page']}页] {t}")
    return "\n".join(out)[:EXCERPT_CHARS]


def spec_list(count: int, types: list[str], difficulty: int | None) -> list[tuple[str, int]]:
    cycle = [2, 3, 3, 4]
    return [(types[i % len(types)], difficulty or cycle[i % len(cycle)]) for i in range(count)]


def build_gen_messages(title: str, path: str, summary: str, points: list[str], excerpt: str, specs: list[tuple[str, int]]) -> list[dict]:
    req = "\n".join(f"{i}. 题型 {t}（{QTYPE_NAME[t]}），难度 {d}" for i, (t, d) in enumerate(specs, 1))
    body = [f"知识点：{title}（{path}）"]
    if summary:
        body.append(f"概要：{summary}")
    if points:
        body.append("要点：\n" + "\n".join(f"- {p}" for p in points))
    if excerpt:
        body.append(f"课件原文摘录：\n{excerpt}")
    body.append(f"出题要求（共 {len(specs)} 题）：\n{req}")
    return [{"role": "system", "content": GEN_SYSTEM}, {"role": "user", "content": "\n\n".join(body)}]


# ----------------------------------------------------------------- 出题后台任务
@dataclass
class QJob:
    course_id: int
    state: str = "running"  # running / done / error / cancelled
    total: int = 0  # 要处理的知识点数
    done: int = 0
    created: int = 0
    rejected: int = 0
    error: str = ""
    current: str = ""
    task: asyncio.Task | None = None


JOBS: dict[int, QJob] = {}


def start_generation(course_id: int, node_ids: list[int] | None, per_node: int, types: list[str], difficulty: int | None) -> QJob:
    old = JOBS.get(course_id)
    if old and old.state == "running":
        return old
    with Session(engine) as s:
        leaves = leaves_for(s, course_id, node_ids)
        if not leaves:
            raise TranslateError("这门课还没有知识点：先在「知识梳理」里对课件做梳理。")
        # 太多时优先重点和考过的
        from .knowledge import exam_counts

        ec = exam_counts(s, course_id)
        leaves.sort(key=lambda n: (-(n.importance or 2), -ec.get(n.id, 0), n.id))
        ids = [n.id for n in leaves[:MAX_NODES]]
    types = [t for t in types if t in QTYPES] or list(DEFAULT_TYPES)
    job = QJob(course_id=course_id, total=len(ids))
    JOBS[course_id] = job
    job.task = asyncio.create_task(_run(job, ids, max(1, min(per_node, 8)), types, difficulty), name=f"questions-{course_id}")
    return job


def cancel_generation(course_id: int) -> bool:
    j = JOBS.get(course_id)
    if j and j.state == "running" and j.task:
        j.task.cancel()
        return True
    return False


async def _run(job: QJob, node_ids: list[int], per_node: int, types: list[str], difficulty: int | None) -> None:
    try:
        sem = asyncio.Semaphore(CONCURRENCY)
        cache: dict[int, dict[int, str]] = {}
        failures: list[str] = []

        async def one(nid: int):
            async with sem:
                with Session(engine) as s:
                    n = s.get(KnowledgeNode, nid)
                    if not n:
                        return
                    path = node_path(s, n)
                    excerpt = source_excerpt(s, n, cache)
                    title, summary, points = n.title, n.summary, json.loads(n.points_json or "[]")
                specs = spec_list(per_node, types, difficulty)
                job.current = title
                try:
                    raw, _ = await call_llm(build_gen_messages(title, path, summary, points, excerpt, specs), json_mode=True)
                except TranslateError as e:
                    failures.append(str(e))
                    return
                data = parse_json(raw) or {}
                items = data.get("questions", []) if isinstance(data, dict) else []
                made = rejected = 0
                with Session(engine) as s:
                    have = {stem_key(q) for q in s.exec(select(Question.stem).where(Question.course_id == job.course_id)).all()}
                    for pos, it in enumerate(items):
                        idx = to_int(it.get("i")) if isinstance(it, dict) else None
                        want = specs[idx - 1][0] if idx and 1 <= idx <= len(specs) else (specs[pos][0] if pos < len(specs) else None)
                        c = clean_generated(it, want)
                        if not c or stem_key(c["stem"]) in have:
                            rejected += 1
                            continue
                        have.add(stem_key(c["stem"]))
                        s.add(Question(course_id=job.course_id, node_id=nid, origin="generated", **c))
                        made += 1
                    s.commit()
                job.created += made
                job.rejected += rejected

        results = await asyncio.gather(*(one(i) for i in node_ids), return_exceptions=True)
        for r in results:
            if isinstance(r, asyncio.CancelledError):
                raise r
            if isinstance(r, Exception):
                failures.append(str(r))
            job.done += 1
        if failures and job.created == 0:
            raise TranslateError(failures[0])
        job.state = "done"
        if failures:
            job.error = f"有 {len(failures)} 个知识点出题失败：{failures[0][:100]}"
    except asyncio.CancelledError:
        job.state = "cancelled"
        raise
    except Exception as e:  # noqa: BLE001
        job.state, job.error = "error", str(e)[:300]
        log.warning("出题失败：%s", e)


def gen_status(course_id: int) -> dict:
    j = JOBS.get(course_id)
    if not j:
        return {"state": "idle", "total": 0, "done": 0, "created": 0, "rejected": 0, "error": "", "current": ""}
    return {"state": j.state, "total": j.total, "done": j.done, "created": j.created, "rejected": j.rejected, "error": j.error, "current": j.current}


async def shutdown() -> None:
    from .translate import cancel_tasks

    await cancel_tasks(j.task for j in list(JOBS.values()))


# ----------------------------------------------------------------- 判分
def _parse_blanks(answer: str, n: int) -> list[str]:
    a = (answer or "").strip()
    try:
        v = json.loads(a)
        if isinstance(v, list):
            return [str(x) for x in v] + [""] * max(0, n - len(v))
    except ValueError:
        pass
    parts = [p for p in re.split(r"[;；\n]+", a)] if a else []
    return parts + [""] * max(0, n - len(parts))


def _num(s: str) -> float | None:
    try:
        return float(re.sub(r"[,\s]", "", unicodedata.normalize("NFKC", s)).rstrip("%"))
    except ValueError:
        return None


def _same_blank(given: str, accepted: str) -> bool:
    g = norm_text(given)
    if not g:
        return False
    for alt in accepted.split("|"):
        a = norm_text(alt)
        if not a:
            continue
        if g == a:
            return True
        x, y = _num(given), _num(alt)
        if x is not None and y is not None and abs(x - y) <= 1e-3 * max(1.0, abs(y)):
            return True
    return False


def grade_objective(q: Question, answer: str) -> tuple[float, str]:
    """选择 / 判断 / 填空的本地判分，返回 (得分 0~1, 说明)。"""
    if q.qtype == "choice":
        got = re.sub(r"[^A-Fa-f]", "", answer or "").upper()[:1]
        return (1.0, "") if got == q.answer else (0.0, f"正确答案：{q.answer}")
    if q.qtype == "multi":
        got = set(re.sub(r"[^A-Fa-f]", "", answer or "").upper())
        right = set(q.answer)
        if got == right:
            return 1.0, ""
        if got and got < right:
            return 0.5, f"漏选了，正确答案：{q.answer}"
        return 0.0, f"正确答案：{q.answer}"
    if q.qtype == "judge":
        a = norm_text(answer or "")
        got = "对" if a in JUDGE_TRUE else "错" if a in JUDGE_FALSE else ""
        return (1.0, "") if got == q.answer else (0.0, f"正确答案：{q.answer}")
    if q.qtype == "fill":
        accepted = json.loads(q.answer or "[]")
        given = _parse_blanks(answer, len(accepted))
        ok = sum(_same_blank(g, a) for g, a in zip(given, accepted))
        score = ok / len(accepted) if accepted else 0.0
        return score, "" if score == 1 else "参考答案：" + "；".join(a.split("|")[0] for a in accepted)
    raise ValueError("主观题不能本地判分")


GRADE_SYSTEM = """你是严谨又友善的阅卷老师。给定题目、参考答案、评分要点和学生的作答，按要点打分。
输出 JSON：{"score":0到1之间的小数,"feedback":"一两句话：对在哪、缺什么、错在哪","missing":["漏掉的要点"]}
规则：按评分要点逐条核对；思路对但计算失误可给一半左右；答非所问或空白给 0；不要因为文字风格扣分。"""


async def grade_subjective(q: Question, answer: str) -> tuple[float, str]:
    if not norm_text(answer):
        return 0.0, "没有作答。"
    kp = json.loads(q.key_points_json or "[]")
    user = f"【题目】\n{q.stem}\n\n【参考答案】\n{q.answer}\n\n【评分要点】\n" + ("\n".join(f"- {k}" for k in kp) or "（无，按参考答案判断）") + f"\n\n【学生作答】\n{answer[:4000]}"
    raw, _ = await call_llm([{"role": "system", "content": GRADE_SYSTEM}, {"role": "user", "content": user}], json_mode=True)
    data = parse_json(raw) or {}
    try:
        score = max(0.0, min(1.0, float(data.get("score"))))
    except (TypeError, ValueError):
        raise TranslateError("模型没有给出有效的分数") from None
    fb = str(data.get("feedback") or "").strip()
    missing = [str(m) for m in (data.get("missing") or []) if str(m).strip()][:4]
    if missing:
        fb += "　漏掉：" + "；".join(missing)
    return round(score, 2), fb


# ----------------------------------------------------------------- 作答记录、错题、掌握度
def question_out(q: Question, reveal: bool = False) -> dict:
    d = {"id": q.id, "course_id": q.course_id, "node_id": q.node_id, "qtype": q.qtype, "stem": q.stem,
         "options": json.loads(q.options_json) if q.options_json else [], "points": q.points, "difficulty": q.difficulty,
         "ability": q.ability, "origin": q.origin, "based_on": q.based_on, "similarity": q.similarity,
         "created_at": q.created_at.isoformat()}
    if q.qtype == "fill":
        d["blanks"] = len(json.loads(q.answer or "[]"))
    if reveal:
        d.update(reference(q))
    return d


def reference(q: Question) -> dict:
    """作答后给学生看的参考答案。"""
    ans = q.answer
    if q.qtype == "fill":
        ans = "；".join(a.split("|")[0] for a in json.loads(q.answer or "[]"))
    elif q.qtype in ("choice", "multi"):
        opts = json.loads(q.options_json or "[]")
        ans = "；".join(f"{c}. {opts[LETTERS.index(c)]}" for c in q.answer if LETTERS.index(c) < len(opts))
    return {"answer": ans, "correct_answer": q.answer if q.qtype in ("choice", "multi", "judge") else "", "explanation": q.explanation,
            "key_points": json.loads(q.key_points_json or "[]")}


def ensure_flashcard(s: Session, q: Question) -> int | None:
    """错题进闪卡（每道题只建一张）。返回新卡 id；已经有就返回 None。"""
    ref = f"q:{q.id}"
    if s.exec(select(Flashcard.id).where(Flashcard.course_id == q.course_id, Flashcard.source_ref == ref)).first():
        return None
    front = q.stem
    if q.options_json:
        opts = json.loads(q.options_json)
        front += "\n\n" + "\n".join(f"{LETTERS[i]}. {o}" for i, o in enumerate(opts))
    r = reference(q)
    back = f"**{r['answer']}**" + (f"\n\n{r['explanation']}" if r["explanation"] else "")
    fj, due = fsrs_service.new_card_json()
    c = Flashcard(course_id=q.course_id, front=front, back=back, card_type="basic", source="question", source_ref=ref, fsrs_json=fj, due=due)
    s.add(c)
    s.flush()
    return c.id


def mastery_by_node(s: Session, course_id: int) -> dict[int, dict]:
    """知识点 → {mastery 0~1, attempts, last}。按最近作答加权，再向 0.5 收缩（先验权重 1：全对 1 次约 75%，4 次约 88%）。"""
    rows = s.exec(select(Attempt).where(Attempt.course_id == course_id, Attempt.node_id.is_not(None))  # type: ignore[union-attr]
                  .order_by(Attempt.created_at.desc())).all()
    acc: dict[int, list[Attempt]] = {}
    for a in rows:
        acc.setdefault(a.node_id, []).append(a)
    out = {}
    for nid, lst in acc.items():
        ws = [0.85 ** k for k in range(len(lst))]
        m = (sum(w * a.score for w, a in zip(ws, lst)) + 0.5) / (sum(ws) + 1.0)
        out[nid] = {"mastery": round(m, 3), "attempts": len(lst), "last": lst[0].created_at.isoformat()}
    return out


def practice_queue(s: Session, course_id: int, count: int, node_ids: list[int] | None = None, origin: str | None = None,
                   rng: random.Random | None = None) -> list[Question]:
    """先做错过的、没做过的、薄弱知识点的题，最近刚做对的往后放。"""
    rng = rng or random.Random()
    stmt = select(Question).where(Question.course_id == course_id)
    if origin:
        stmt = stmt.where(Question.origin == origin)
    qs = list(s.exec(stmt).all())
    if node_ids:
        allowed = {n.id for n in leaves_for(s, course_id, node_ids)}
        qs = [q for q in qs if q.node_id in allowed]
    last: dict[int, Attempt] = {}
    for a in s.exec(select(Attempt).where(Attempt.course_id == course_id).order_by(Attempt.created_at)).all():
        last[a.question_id] = a
    mast = mastery_by_node(s, course_id)
    now = datetime.now()

    def prio(q: Question) -> float:
        weak = 1 - mast.get(q.node_id, {}).get("mastery", 0.5)
        a = last.get(q.id)
        if a is None:
            return 2.0 + weak
        days = (now - a.created_at).total_seconds() / 86400
        if a.score < WRONG_BELOW:
            return 3.0 + min(days / 10, 0.5) + weak * 0.3
        return 0.5 * weak + min(days / 14, 1.0) * 0.5

    return sorted(qs, key=lambda q: -(prio(q) + rng.uniform(-0.3, 0.3)))[: max(1, min(count, 50))]


def question_stats(s: Session, course_id: int) -> dict:
    total = s.exec(select(func.count()).select_from(Question).where(Question.course_id == course_id)).one()
    simulated = s.exec(select(func.count()).select_from(Question).where(Question.course_id == course_id, Question.origin == "simulated")).one()
    atts = s.exec(select(Attempt).where(Attempt.course_id == course_id)).all()
    last: dict[int, Attempt] = {}
    for a in sorted(atts, key=lambda x: x.created_at):
        last[a.question_id] = a
    wrong = sum(1 for a in last.values() if a.score < WRONG_BELOW)
    acc = round(sum(a.score for a in atts) / len(atts), 3) if atts else None
    return {"total": total, "simulated": simulated, "answered": len(last), "wrong": wrong, "attempts": len(atts), "accuracy": acc}
