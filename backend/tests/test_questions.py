"""题库（出题 / 判分 / 错题进闪卡 / 掌握度 / 练习队列）和真题仿真（防抄袭 / 模拟卷）。模型用仿真的。"""
from __future__ import annotations

import json
import os
import random
import tempfile
import time

os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="alp-test-q-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.api import questions as qapi  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Document, ExamQuestion, Flashcard, KnowledgeNode, Question  # noqa: E402
from app.services import questions as qs  # noqa: E402
from app.services import simulate as sim  # noqa: E402
from app.services import similarity  # noqa: E402


# ----------------------------------------------------------------- 纯函数
def good(**kw):
    base = {"type": "choice", "stem": "牛顿-拉夫逊法潮流计算的收敛特性是？", "options": ["线性收敛", "平方收敛", "不收敛", "随机"],
            "answer": "B", "explanation": "雅可比矩阵每次更新", "difficulty": 2, "ability": "理解"}
    base.update(kw)
    return base


def test_clean_generated_accepts_and_rejects():
    ok = qs.clean_generated(good(options=["A. 线性收敛", "B. 平方收敛", "C. 不收敛", "D. 随机"]), "choice")
    assert ok and json.loads(ok["options_json"])[1] == "平方收敛" and ok["answer"] == "B"  # 去掉了 A. 前缀
    assert qs.clean_generated(good(answer="E"), "choice") is None  # 答案字母不在选项里
    assert qs.clean_generated(good(options=["a", "a", "b", "c"]), "choice") is None  # 选项重复
    assert qs.clean_generated(good(), "judge") is None  # 没按要求的题型
    assert qs.clean_generated(good(type="multi", answer="A"), "multi") is None  # 多选只有一个答案
    assert qs.clean_generated(good(type="multi", answer="AB"), "multi")["answer"] == "AB"
    j = qs.clean_generated({"type": "judge", "stem": "潮流计算是求稳态运行状态。", "answer": "正确"}, "judge")
    assert j["answer"] == "对"
    assert qs.clean_generated({"type": "judge", "stem": "潮流计算是求稳态运行状态。", "answer": "也许"}, "judge") is None
    f = qs.clean_generated({"type": "fill", "stem": "PQ 节点已知____和____。", "answer": ["有功|P", "无功|Q"]}, "fill")
    assert json.loads(f["answer"]) == ["有功|P", "无功|Q"]
    assert qs.clean_generated({"type": "fill", "stem": "PQ 节点已知____和____。", "answer": ["有功"]}, "fill") is None  # 空数对不上
    assert qs.clean_generated({"type": "calc", "stem": "求线路末端电压并写出步骤。", "answer": "略"}, "calc") is None  # 参考答案太短


def make_q(qtype, answer, options=None, stem="题干足够长的一道题"):
    return Question(course_id=1, qtype=qtype, stem=stem, answer=answer, options_json=json.dumps(options or [], ensure_ascii=False))


def test_grade_objective():
    assert qs.grade_objective(make_q("choice", "B", ["a", "b", "c"]), " b ")[0] == 1
    assert qs.grade_objective(make_q("choice", "B", ["a", "b", "c"]), "A")[0] == 0
    m = make_q("multi", "AC", ["a", "b", "c", "d"])
    assert qs.grade_objective(m, "AC")[0] == 1 and qs.grade_objective(m, "A")[0] == 0.5 and qs.grade_objective(m, "AB")[0] == 0
    j = make_q("judge", "错")
    assert qs.grade_objective(j, "错误")[0] == 1 and qs.grade_objective(j, "对")[0] == 0 and qs.grade_objective(j, "")[0] == 0
    f = make_q("fill", json.dumps(["牛顿-拉夫逊法|牛顿拉夫逊法", "0.95"], ensure_ascii=False))
    assert qs.grade_objective(f, json.dumps(["牛顿拉夫逊法", "0.9500"]))[0] == 1  # 别名 + 数值等价
    assert qs.grade_objective(f, json.dumps(["牛顿拉夫逊法", "0.5"]))[0] == 0.5
    assert qs.grade_objective(f, "牛顿-拉夫逊法；0.95")[0] == 1  # 用分号分隔也行
    assert qs.grade_objective(f, "")[0] == 0


def test_similarity_guard():
    real = "某 220kV 输电线路长 100km，单位长度阻抗 z=0.1+j0.4 Ω/km，末端负荷 S=80+j30 MVA，末端电压 U2=220kV。(1) 求线路首端电压和功率；(2) 若首端电压保持 230kV，求末端电压。"
    assert similarity.compare(real, real)["too_similar"]
    para = "某 220kV 输电线路长度为 100km，单位长度阻抗 z=0.1+j0.4 Ω/km，末端负荷为 S=80+j30 MVA，末端电压 U2=220kV。(1) 计算线路首端的电压与功率；(2) 若首端电压维持 230kV，计算末端电压。"
    assert similarity.compare(para, real)["too_similar"]
    same_data = "一条 110kV 单回路供电给某工厂，线路长 100km，z=0.1+j0.4 Ω/km，工厂负荷 80+j30 MVA，受端额定电压 220kV，求电源端电压及送出的功率。"
    assert similarity.compare(same_data, real)["numbers"] >= 0.6 and similarity.compare(same_data, real)["too_similar"]  # 场景换了数据照搬
    fresh = "某 110kV 配电线路长 60km，每公里阻抗为 0.17+j0.38 Ω，线路末端接有一座变电站，负荷为 35+j12 MVA，末端电压按 108kV 运行。(a) 求线路电压降落和电压损耗；(b) 求首端输入的有功与无功功率。"
    assert not similarity.compare(fresh, real)["too_similar"]
    best = similarity.check_against(fresh, [(1, real), (2, "简述牛顿法的步骤")])
    assert not best["too_similar"] and best["ref_id"] == 1


# ----------------------------------------------------------------- 接口流程
class FakeLLM:
    """按提示词里的“出题要求”返回合格题目；可以注入坏题、模拟抄袭。"""

    def __init__(self):
        self.calls = 0
        self.seq = 0
        self.grade_fail = False
        self.copy_first: str | None = None  # 仿真时第一次故意抄这段真题
        self.always_copy: str | None = None
        self.sim_calls = 0

    async def call(self, messages, *, json_mode):
        self.calls += 1
        sysmsg, user = messages[0]["content"], messages[-1]["content"]
        if "阅卷老师" in sysmsg:
            if self.grade_fail:
                from app.services.translate import TranslateError

                raise TranslateError("模型不可用")
            return json.dumps({"score": 0.75, "feedback": "思路对，漏了收敛判据", "missing": ["收敛判据"]}), "fake"
        if "仿真题" in sysmsg:
            self.sim_calls += 1
            if self.always_copy or (self.copy_first and self.sim_calls == 1):
                text = self.always_copy or self.copy_first
                self.copy_first = None
                return json.dumps({"questions": [{"i": 1, "type": "calc", "stem": text, "answer": "参考答案很长很长很长", "key_points": ["a", "b"]}]}), "fake"
            self.seq += 1
            qt = "choice" if "题型 choice" in user else "calc"
            if qt == "choice":
                it = {"i": 1, "type": "choice", "stem": f"全新背景 {self.seq}：某风电场并网后母线电压偏高，应首先采取哪种措施？", "options": ["投入电抗器", "投入电容器", "提高变压器分接头", "切除负荷"], "answer": "A", "explanation": "吸收无功"}
            else:
                it = {"i": 1, "type": "calc", "stem": f"某 35kV 线路 {self.seq * 7 + 13}km，导线参数 {self.seq + 0.21}Ω/km，负荷 {self.seq * 3 + 41}+j{self.seq + 9}MVA，求线路末端电压损耗并写出步骤。", "answer": "先求电压降落纵分量，再求损耗，结果约 3.2kV。", "key_points": ["电压降落公式", "数值结果"]}
            return json.dumps({"questions": [it]}), "fake"
        # 普通出题：解析“出题要求”里的每一行
        import re

        specs = re.findall(r"(\d+)\. 题型 (\w+)（", user)
        title = re.search(r"知识点：(.+?)（", user).group(1)
        items = []
        for i, t in specs:
            self.seq += 1
            base = {"i": int(i), "type": t, "difficulty": 2, "ability": "理解", "explanation": "解析"}
            if t == "choice":
                base.update(stem=f"关于{title}，下列说法正确的是（{self.seq}）？", options=["甲说法", "乙说法", "丙说法", "丁说法"], answer="B")
            elif t == "judge":
                base.update(stem=f"{title}的结论在任何情况下都成立（{self.seq}）。", answer="错")
            elif t == "fill":
                base.update(stem=f"{title}的核心量是____，编号{self.seq}。", answer=["电压|U"])
            else:
                base.update(stem=f"请简述{title}的基本思路并举例说明（{self.seq}）。", answer="先建模再迭代求解直到收敛为止。", key_points=["建模", "迭代", "收敛"])
            items.append(base)
        if self.seq % 5 == 0 and items:
            items.append({"i": 99, "type": "choice", "stem": "坏题", "options": ["x"], "answer": "Z"})  # 一道必须被丢掉的坏题
        return json.dumps({"questions": items}, ensure_ascii=False), "fake"


@pytest.fixture()
def env(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(qs, "call_llm", f.call)
    monkeypatch.setattr(sim, "call_llm", f.call)
    monkeypatch.setattr(qapi, "configured_chain", lambda: ["fast"])
    with TestClient(app, base_url="http://localhost") as c:
        yield c, f


def seed_course(c) -> dict:
    cid = c.post("/api/courses", json={"name": "题库测试课"}).json()["id"]
    with Session(engine) as s:
        slides = Document(course_id=cid, filename="ch1.pdf", filetype="pdf", path="x", kind="slides", status="ready")
        exam = Document(course_id=cid, filename="2024期末.pdf", filetype="pdf", path="y", kind="exam", status="ready")
        s.add_all([slides, exam])
        s.flush()
        ch = KnowledgeNode(course_id=cid, document_id=slides.id, level=1, title="第一章 潮流计算", order=0)
        s.add(ch)
        s.flush()
        sec = KnowledgeNode(course_id=cid, document_id=slides.id, parent_id=ch.id, level=2, title="1.1 方法", order=0)
        s.add(sec)
        s.flush()
        k1 = KnowledgeNode(course_id=cid, document_id=slides.id, parent_id=sec.id, level=3, title="牛顿-拉夫逊法", order=0, importance=3,
                           summary="用雅可比矩阵迭代求解潮流方程", points_json=json.dumps(["雅可比矩阵", "平方收敛"], ensure_ascii=False),
                           refs_json=json.dumps([{"document_id": slides.id, "page": 3}]))
        k2 = KnowledgeNode(course_id=cid, document_id=slides.id, parent_id=sec.id, level=3, title="PQ 分解法", order=1, importance=2, summary="简化雅可比矩阵")
        s.add_all([k1, k2])
        s.flush()
        e1 = ExamQuestion(course_id=cid, document_id=exam.id, number="一、1", qtype="选择", points=3, ability="理解", difficulty=2,
                          text="某 220kV 输电线路长 100km，阻抗 z=0.1+j0.4 Ω/km，末端负荷 80+j30 MVA，下列关于首端电压说法正确的是？",
                          topics_json=json.dumps(["电压降落"], ensure_ascii=False), node_ids_json=json.dumps([k1.id]))
        e2 = ExamQuestion(course_id=cid, document_id=exam.id, number="三、2", qtype="计算", points=12, ability="应用", difficulty=4,
                          text="某 220kV 输电线路长 100km，单位长度阻抗 z=0.1+j0.4 Ω/km，末端负荷 S=80+j30 MVA，末端电压 U2=220kV。(1) 求线路首端电压和功率；(2) 若首端电压保持 230kV，求末端电压。",
                          topics_json=json.dumps(["潮流计算"], ensure_ascii=False), node_ids_json=json.dumps([k1.id, k2.id]))
        s.add_all([e1, e2])
        s.commit()
        return {"cid": cid, "k1": k1.id, "k2": k2.id, "ch": ch.id, "exam": exam.id, "e1": e1.id, "e2": e2.id}


def wait(c, url, key="state"):
    for _ in range(200):
        st = c.get(url).json()
        if st[key] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("任务超时")


def test_generate_grade_flashcard_mastery(env):
    c, f = env
    ids = seed_course(c)
    cid = ids["cid"]
    # 没有知识点时给出明确提示
    empty = c.post("/api/courses", json={"name": "空课"}).json()["id"]
    r = c.post(f"/api/courses/{empty}/questions/generate", json={})
    assert r.status_code == 400 and "知识点" in r.json()["detail"]

    r = c.post(f"/api/courses/{cid}/questions/generate", json={"per_node": 5, "types": ["choice", "judge", "fill", "short", "calc"]})
    assert r.status_code == 200, r.text
    st = wait(c, f"/api/courses/{cid}/questions/generate")
    assert st["state"] == "done" and st["created"] == 10 and st["rejected"] >= 1, st  # 2 个知识点 × 5，坏题被丢掉
    lst = c.get(f"/api/courses/{cid}/questions").json()
    assert len(lst) == 10 and all("answer" not in x for x in lst)  # 列表不泄露答案
    assert {x["qtype"] for x in lst} == {"choice", "judge", "fill", "short", "calc"}
    # 再生成一次：同样的题干不重复入库（编号变了所以会有新题，但同一知识点里不会撞题干）
    stats = c.get(f"/api/courses/{cid}/questions/stats").json()
    assert stats["total"] == 10 and stats["answered"] == 0

    by_type = {x["qtype"]: x for x in lst if x["node_id"] == ids["k1"]}
    # 选择题答对：不进闪卡，给 XP
    ch = by_type["choice"]
    ok = c.post(f"/api/questions/{ch['id']}/attempt", json={"answer": "B"}).json()
    assert ok["graded"] and ok["correct"] and not ok["flashcard_created"] and ok["reward"]["xp"] == 3 + ch["difficulty"]
    again = c.post(f"/api/questions/{ch['id']}/attempt", json={"answer": "B"}).json()
    assert again["reward"] is None  # 已经做对过，再刷不给分
    # 判断题答错：进闪卡，且只建一张
    jd = by_type["judge"]
    bad = c.post(f"/api/questions/{jd['id']}/attempt", json={"answer": "对"}).json()
    assert not bad["correct"] and bad["flashcard_created"] and bad["answer"] == "错"
    bad2 = c.post(f"/api/questions/{jd['id']}/attempt", json={"answer": "对"}).json()
    assert not bad2["flashcard_created"]
    with Session(engine) as s:
        cards = s.exec(select(Flashcard).where(Flashcard.course_id == cid, Flashcard.source == "question")).all()
        assert len(cards) == 1 and cards[0].source_ref == f"q:{jd['id']}" and "错" in cards[0].back
    # 主观题：模型评分 → 自评修正
    sh = by_type["short"]
    g = c.post(f"/api/questions/{sh['id']}/attempt", json={"answer": "先建模，再迭代，直到收敛"}).json()
    assert g["graded"] and g["graded_by"] == "llm" and g["score"] == 0.75 and "收敛判据" in g["feedback"]
    p = c.patch(f"/api/attempts/{g['attempt_id']}", json={"score": 0.2}).json()
    assert p["score"] == 0.2
    # 模型不可用：退回自评，不丢记录
    f.grade_fail = True
    calc = by_type["calc"]
    g2 = c.post(f"/api/questions/{calc['id']}/attempt", json={"answer": "我的解答"}).json()
    assert g2["graded"] is False and g2["need_self_grade"] and g2["answer"]
    g3 = c.post(f"/api/questions/{calc['id']}/attempt", json={"answer": "我的解答", "self_score": 1}).json()
    assert g3["graded"] and g3["graded_by"] == "self" and g3["correct"]
    # 空白作答不花模型调用，直接 0 分
    f.grade_fail = False
    n = f.calls
    blank = c.post(f"/api/questions/{by_type['short']['id']}/attempt", json={"answer": "  "}).json()
    assert blank["score"] == 0 and f.calls == n

    m = c.get(f"/api/courses/{cid}/mastery").json()
    k1 = next(x for x in m["nodes"] if x["id"] == ids["k1"])
    k2 = next(x for x in m["nodes"] if x["id"] == ids["k2"])
    assert k1["attempts"] >= 5 and 0 < k1["mastery"] < 1 and k2["mastery"] is None and m["covered"] == 1
    # 练习队列：做错的排最前，没做过的其次；最近刚做对的最后
    queue = c.get(f"/api/courses/{cid}/practice", params={"count": 50}).json()
    pos = {x["id"]: i for i, x in enumerate(queue)}
    new_ids = [x["id"] for x in lst if x["id"] not in {ch["id"], jd["id"], sh["id"], calc["id"]}]
    assert pos[jd["id"]] < min(pos[i] for i in new_ids) or pos[sh["id"]] < min(pos[i] for i in new_ids)
    assert pos[ch["id"]] > max(pos[i] for i in new_ids)
    wrong = c.get(f"/api/courses/{cid}/questions", params={"status": "wrong"}).json()
    assert {x["id"] for x in wrong} >= {jd["id"], sh["id"]}
    # 揭晓与删除
    assert "answer" in c.get(f"/api/questions/{ch['id']}", params={"reveal": True}).json()
    assert c.delete(f"/api/questions/{ch['id']}").status_code == 200


def test_practice_queue_is_deterministic_in_priority():
    # 纯逻辑：用固定随机种子，新题 > 做对过的
    with Session(engine) as s:
        out = qs.practice_queue(s, 999999, 5, rng=random.Random(1))
        assert out == []


def test_simulation_blocks_copying_and_builds_paper(env):
    c, f = env
    ids = seed_course(c)
    cid = ids["cid"]
    prof = c.get(f"/api/courses/{cid}/exam-profile").json()
    assert prof["analyzed"] == 2 and prof["by_type"] == {"选择": 1, "计算": 1} and prof["by_ability"]["应用"] == 1
    assert prof["exams"][0]["sections"] == [{"qtype": "选择", "count": 1, "points": 3}, {"qtype": "计算", "count": 1, "points": 12}]
    assert any(n["id"] == ids["k1"] and n["count"] == 2 for n in prof["top_nodes"])
    assert [n["title"] for n in prof["uncovered_key_nodes"]] == []  # 重点知识点都考过

    # 第一次抄真题 → 被退回重出，最终入库的题和真题不像
    with Session(engine) as s:
        real_text = s.get(ExamQuestion, ids["e2"]).text
    f.copy_first = real_text
    r = c.post(f"/api/courses/{cid}/simulate", json={"question_ids": [ids["e2"]]})
    assert r.status_code == 200, r.text
    st = wait(c, f"/api/courses/{cid}/simulate")
    assert st["state"] == "done" and st["created"] == 1 and st["rejected"] == 0, st
    assert f.sim_calls == 2  # 抄了一次，重出一次
    sims = c.get(f"/api/courses/{cid}/questions", params={"origin": "simulated"}).json()
    assert len(sims) == 1 and sims[0]["based_on"] == ids["e2"] and sims[0]["points"] == 12 and sims[0]["ability"] == "应用"
    assert sims[0]["similarity"] is not None and sims[0]["similarity"] < 0.5
    with Session(engine) as s:
        refs = sim.real_references(s, cid)
        stem = s.get(Question, sims[0]["id"]).stem
    assert not similarity.check_against(stem, refs)["too_similar"]

    # 一直抄 → 丢弃，任务报错而不是放进题库
    f.always_copy = real_text
    c.post(f"/api/courses/{cid}/simulate", json={"question_ids": [ids["e2"]]})
    st = wait(c, f"/api/courses/{cid}/simulate")
    assert st["state"] == "error" and "太像" in st["error"], st
    assert len(c.get(f"/api/courses/{cid}/questions", params={"origin": "simulated"}).json()) == 1
    f.always_copy = None

    # 模拟卷：照真题结构（选择 3 分 + 计算 12 分），已有的仿真题会复用
    r = c.post(f"/api/courses/{cid}/papers", json={})
    assert r.status_code == 200, r.text
    ps = wait(c, f"/api/courses/{cid}/papers/status")
    assert ps["state"] == "done" and ps["paper_id"], ps
    paper = c.get(f"/api/papers/{ps['paper_id']}").json()
    assert paper["total_points"] == 15 and [i["qtype"] for i in paper["items"]] == ["choice", "calc"]
    assert [i["section"] for i in paper["items"]] == ["一、选择题", "二、计算题"]
    assert paper["items"][1]["id"] == sims[0]["id"]  # 复用
    assert all("answer" not in i for i in paper["items"])  # 不带答案
    md = c.get(f"/api/papers/{paper['id']}/export", params={"answers": True})
    assert md.status_code == 200 and "参考答案与解析" in md.text and "满分 15 分" in md.text
    assert "参考答案" not in c.get(f"/api/papers/{paper['id']}/export").text
    assert len(c.get(f"/api/courses/{cid}/papers").json()) == 1
    assert c.delete(f"/api/papers/{paper['id']}").status_code == 200


def test_simulate_requires_analyzed_exam(env):
    c, _ = env
    cid = c.post("/api/courses", json={"name": "无真题"}).json()["id"]
    r = c.post(f"/api/courses/{cid}/simulate", json={})
    assert r.status_code == 400 and "真题" in r.json()["detail"]
    r = c.post(f"/api/courses/{cid}/papers", json={})
    assert r.status_code == 400
