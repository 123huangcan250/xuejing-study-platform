"""SQLModel 数据模型：课程世界 → 资料/分块 → 画布节点/连线 → 闪卡 → 学习事件。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now()


class Course(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: str = ""
    emoji: str = "📘"
    color: str = "#6366f1"
    exam_date: str = ""  # 考试日期 YYYY-MM-DD，设了才会有考前冲刺计划
    sprint_minutes: int = 90  # 冲刺期间每天能学多少分钟
    created_at: datetime = Field(default_factory=now, sa_type=DateTime)


class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, ondelete="CASCADE")
    filename: str
    filetype: str  # pdf / md / txt
    path: str
    page_count: int = 0
    chunk_count: int = 0
    status: str = "processing"  # processing / ready / error
    error: str = ""
    empty_pages: str = ""  # 无文本层的页码（疑似扫描件），逗号分隔
    kind: str = "slides"  # slides 课件 / exam 试卷 / reference 参考资料
    source: str = "local"  # local 本地上传 / cloud 清华云盘（原文件不落地，按需取回）
    source_id: Optional[int] = None  # CloudSource.id
    source_path: str = ""  # 在云盘分享里的路径
    source_size: int = 0
    source_mtime: str = ""  # 云盘上的修改时间，用来判断“有更新”
    created_at: datetime = Field(default_factory=now, sa_type=DateTime)


class Chunk(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True, ondelete="CASCADE")
    course_id: int = Field(index=True)
    page: int = 1  # PDF 为 1 起始页码；Markdown/TXT 恒为 1
    idx: int = 0
    text: str


class CanvasNode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, ondelete="CASCADE")
    type: str  # excerpt / qa / note / concept
    kind: str = ""  # qa 节点来源: highlight / followup / term / merge
    title: str = ""
    question: str = ""
    content: str = ""  # excerpt: 原文；qa: AI 回答；note/concept: 正文
    anchor_json: str = ""  # {"document_id","page","quote","rects":[...]}
    sources_json: str = ""  # 回答引用的 RAG 分块
    x: float = 0
    y: float = 0
    depth: int = 0
    rewarded: bool = False
    created_at: datetime = Field(default_factory=now, sa_type=DateTime)


class CanvasEdge(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, ondelete="CASCADE")
    source: int = Field(foreign_key="canvasnode.id", ondelete="CASCADE")
    target: int = Field(foreign_key="canvasnode.id", ondelete="CASCADE")


class Flashcard(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, ondelete="CASCADE")
    front: str
    back: str
    card_type: str = "basic"  # basic / cloze / formula
    source: str = "manual"  # manual / highlight / node
    source_ref: str = ""
    fsrs_json: str = ""
    due: datetime = Field(default_factory=now, index=True, sa_type=DateTime)
    state: int = 1  # fsrs State: 1 Learning 2 Review 3 Relearning
    reps: int = 0
    lapses: int = 0
    created_at: datetime = Field(default_factory=now, sa_type=DateTime)


class ReviewLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    flashcard_id: int = Field(foreign_key="flashcard.id", index=True, ondelete="CASCADE")
    course_id: int = Field(index=True)
    rating: int
    reviewed_at: datetime = Field(default_factory=now, sa_type=DateTime)


class LearningEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    course_id: Optional[int] = Field(default=None, index=True)
    payload_json: str = ""
    xp: int = 0
    coins: int = 0
    created_at: datetime = Field(default_factory=now, index=True, sa_type=DateTime)


class DailyStat(SQLModel, table=True):
    date: str = Field(primary_key=True)  # YYYY-MM-DD（本地时间）
    xp: int = 0
    events: int = 0
    reviews: int = 0
    questions: int = 0


class Profile(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    xp_total: int = 0
    coins: int = 0


class AchievementUnlock(SQLModel, table=True):
    key: str = Field(primary_key=True)
    unlocked_at: datetime = Field(default_factory=now, sa_type=DateTime)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""


class TokenUsage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tier: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False
    created_at: datetime = Field(default_factory=now, index=True, sa_type=DateTime)


class DocBlock(SQLModel, table=True):
    """文档里的一个文字块（PDF 的文本块、DOCX/PPTX 的段落、Markdown 的自然段）。翻译、对照阅读、导出都以它为单位。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True, ondelete="CASCADE")
    page: int = 1  # PDF 页码 / PPT 幻灯片序号 / DOCX 与文本的“虚拟页”
    idx: int = 0  # 文档内的全局顺序号（导出译文时据此回到原位置）
    kind: str = "p"  # title / h1..h3 / p / li / cell / note / code
    text: str
    meta_json: str = ""  # PDF: bbox/字号/颜色/页宽高；表格: [表序号, 行, 列]


class OcrPage(SQLModel, table=True):
    """扫描页的视觉模型识别结果（公式为 LaTeX）。单独存放：重新解析资料时不必再花钱识别。text 为空 = 空白页。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True, ondelete="CASCADE")
    page: int = 1
    text: str = ""
    model: str = ""
    created_at: datetime = Field(default_factory=now, sa_type=DateTime)


class Translation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True, ondelete="CASCADE")
    block_id: int = Field(foreign_key="docblock.id", index=True, ondelete="CASCADE")
    target: str = "zh"
    src_hash: str = ""  # 原文变化（重新解析）后据此判断译文是否过期
    text: str = ""
    model: str = ""
    created_at: datetime = Field(default_factory=now, sa_type=DateTime)


class KnowledgeNode(SQLModel, table=True):
    """知识梳理里的一个节点。level 1 = 一份课件（章节），2 = 小节，3 = 知识点。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, ondelete="CASCADE")
    document_id: Optional[int] = Field(default=None, foreign_key="document.id", index=True, ondelete="CASCADE")
    parent_id: Optional[int] = Field(default=None, index=True)
    level: int = 1
    order: int = 0
    title: str
    summary: str = ""
    points_json: str = ""  # 要点列表（JSON 数组）
    refs_json: str = ""  # 出处 [{"document_id":..,"page":..}]
    importance: int = 2  # 1 次要 / 2 一般 / 3 重点
    src_hash: str = ""  # 仅 level 1 使用：课件内容变化后据此判断是否需要重新梳理


class ExamQuestion(SQLModel, table=True):
    """从试卷里拆出来的一道题，以及 AI 识别出的考点、能力层级和对应的课件知识点。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, ondelete="CASCADE")
    document_id: int = Field(foreign_key="document.id", index=True, ondelete="CASCADE")
    number: str = ""
    qtype: str = ""
    text: str
    points: Optional[float] = None
    answer: str = ""
    topics_json: str = ""  # 考点短语列表
    node_ids_json: str = ""  # 对应的 KnowledgeNode.id 列表
    ability: str = ""  # 记忆 / 理解 / 应用 / 分析 / 综合
    difficulty: int = 3
    created_at: datetime = Field(default_factory=now, sa_type=DateTime)


class Question(SQLModel, table=True):
    """题库里的一道练习题（AI 按知识点出的，或按真题考点仿真出的）。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, ondelete="CASCADE")
    node_id: Optional[int] = Field(default=None, index=True)  # 主要对应的 KnowledgeNode（知识点）
    qtype: str = "choice"  # choice 单选 / multi 多选 / judge 判断 / fill 填空 / short 简答 / calc 计算 / proof 证明
    stem: str
    options_json: str = ""  # 选择题的选项（不带 A. 前缀）
    answer: str = ""  # choice/multi: 字母；judge: 对/错；fill: JSON 数组（每空可有多个可接受写法，用 | 分隔）；其他: 参考答案
    key_points_json: str = ""  # 主观题的评分要点
    explanation: str = ""
    points: float = 0
    difficulty: int = 3
    ability: str = ""
    origin: str = "generated"  # generated 按知识点出 / simulated 真题仿真
    based_on: Optional[int] = Field(default=None, index=True)  # 仿真题参照的 ExamQuestion.id（只记录考点来源）
    similarity: Optional[float] = None  # 与所有真题的最大相似度（越低越不像）
    created_at: datetime = Field(default_factory=now, sa_type=DateTime)


class Attempt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True, ondelete="CASCADE")
    course_id: int = Field(index=True)
    node_id: Optional[int] = Field(default=None, index=True)
    answer: str = ""
    score: float = 0  # 0~1
    graded_by: str = "auto"  # auto 自动判分 / llm 模型评分 / self 自评
    feedback: str = ""
    created_at: datetime = Field(default_factory=now, index=True, sa_type=DateTime)


class Paper(SQLModel, table=True):
    """一份仿真模拟卷：结构、考点分布、难度都参照真题，题目是新出的。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, ondelete="CASCADE")
    title: str = ""
    based_on_document: Optional[int] = None
    duration_min: int = 120
    created_at: datetime = Field(default_factory=now, sa_type=DateTime)


class PaperItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="paper.id", index=True, ondelete="CASCADE")
    question_id: int = Field(foreign_key="question.id", ondelete="CASCADE")
    order: int = 0
    section: str = ""
    points: float = 0


class SprintDone(SQLModel, table=True):
    """冲刺计划里已完成的任务（计划每次按最新掌握度重新排，完成状态单独存）。key 形如 learn:12 / review:12 / wrong:2026-12-20。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, ondelete="CASCADE")
    key: str = Field(index=True)
    done_at: datetime = Field(default_factory=now, sa_type=DateTime)


class CloudSource(SQLModel, table=True):
    """一个清华云盘分享链接。提取码不在这里：它只以 DPAPI 密文保存在 data/secure/cloud_<id>.dpapi。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, ondelete="CASCADE")
    url: str  # 规范化后的分享链接（不含提取码）
    host: str
    token: str
    share_type: str  # d 文件夹分享 / f 单文件分享
    name: str = ""
    default_kind: str = "slides"
    has_password: bool = False
    last_synced: Optional[datetime] = Field(default=None, sa_type=DateTime)
    created_at: datetime = Field(default_factory=now, sa_type=DateTime)
