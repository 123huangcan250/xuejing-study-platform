"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { ClipboardList, Download, FileText, Loader2, PenLine, Sparkles, Swords, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { API_BASE } from "@/lib/api";
import type { ExamProfile, JobStatus, PaperBrief, PaperDetail, Quiz, QuizStats } from "@/lib/types";
import { Button, Card, Modal, ProgressBar } from "@/components/ui";
import { QuestionCard, QTYPE_NAME } from "./QuestionCard";
import { BossBattle } from "./BossBattle";

type View = "practice" | "bank" | "sim" | "boss";

interface KNode {
  id: number;
  level: number;
  title: string;
  children: KNode[];
}

/** 轮询一个后台任务的状态，结束时回调一次。 */
function useJob(url: string, onFinish?: (s: JobStatus) => void) {
  const [st, setSt] = useState<JobStatus>({ state: "idle", total: 0, done: 0, error: "" });
  const prev = useRef("idle");
  const finish = useRef(onFinish);
  finish.current = onFinish;
  const load = useCallback(async () => {
    try {
      const s = await api.get<JobStatus>(url);
      setSt(s);
      if (prev.current === "running" && s.state !== "running") finish.current?.(s);
      prev.current = s.state;
    } catch {
      /* ignore */
    }
  }, [url]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (st.state !== "running") return;
    const t = setInterval(() => void load(), 1500);
    return () => clearInterval(t);
  }, [st.state, load]);
  return { st, reload: load, setSt };
}

function JobBar({ st, label, onCancel }: { st: JobStatus; label: string; onCancel: () => void }) {
  if (st.state === "running")
    return (
      <div className="flex items-center gap-3 rounded-xl border border-indigo-200 bg-indigo-50/60 p-3 text-sm">
        <Loader2 size={15} className="animate-spin text-indigo-500" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-indigo-700">
            {label} {st.done}/{st.total}
            {st.current ? ` · ${st.current}` : ""}
          </div>
          <ProgressBar value={st.done} max={Math.max(1, st.total)} className="mt-1.5" />
        </div>
        <button className="text-xs text-slate-400 underline" onClick={onCancel}>
          停止
        </button>
      </div>
    );
  if (st.error) return <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-3 text-sm text-amber-800">{st.error}</div>;
  return null;
}

export function QuizPanel({ courseId, onGoKnowledge, onGoExams }: { courseId: number; onGoKnowledge: () => void; onGoExams: () => void }) {
  const [view, setView] = useState<View>("practice");
  const [stats, setStats] = useState<QuizStats | null>(null);
  const [tree, setTree] = useState<KNode[]>([]);

  const loadStats = useCallback(async () => {
    try {
      setStats(await api.get<QuizStats>(`/api/courses/${courseId}/questions/stats`));
    } catch {
      /* ignore */
    }
  }, [courseId]);
  useEffect(() => {
    void loadStats();
    api.get<{ tree: KNode[] }>(`/api/courses/${courseId}/knowledge`).then((k) => setTree(k.tree)).catch(() => {});
  }, [courseId, loadStats]);

  const views: { id: View; label: string; icon: React.ElementType }[] = [
    { id: "practice", label: "练习", icon: PenLine },
    { id: "bank", label: "题库", icon: ClipboardList },
    { id: "sim", label: "真题仿真", icon: FileText },
    { id: "boss", label: "Boss 战", icon: Swords },
  ];

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-lg bg-slate-100 p-0.5 text-sm">
          {views.map(({ id, label, icon: Icon }) => (
            <button key={id} onClick={() => setView(id)} className={clsx("flex items-center gap-1.5 rounded-md px-3 py-1.5 transition", view === id ? "bg-surface font-medium text-indigo-700 shadow-sm" : "text-slate-500 hover:text-slate-700")}>
              <Icon size={14} /> {label}
            </button>
          ))}
        </div>
        {stats && (
          <div className="ml-auto flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
            <span>题库 {stats.total} 题</span>
            <span>已做 {stats.answered}</span>
            <span className={stats.wrong ? "text-rose-500" : ""}>待攻克 {stats.wrong}</span>
            {stats.accuracy != null && <span>正确率 {Math.round(stats.accuracy * 100)}%</span>}
          </div>
        )}
      </div>

      {view === "practice" && <PracticeView courseId={courseId} tree={tree} stats={stats} onAnswered={loadStats} onGoBank={() => setView("bank")} />}
      {view === "bank" && <BankView courseId={courseId} tree={tree} onChanged={loadStats} onGoKnowledge={onGoKnowledge} />}
      {view === "sim" && <SimView courseId={courseId} onChanged={loadStats} onGoExams={onGoExams} />}
      {view === "boss" && <BossBattle courseId={courseId} tree={tree} onAnswered={loadStats} onGoBank={() => setView("bank")} />}
    </div>
  );
}

// ----------------------------------------------------------------- 练习
function ScopeSelect({ tree, value, onChange }: { tree: KNode[]; value: number | null; onChange: (v: number | null) => void }) {
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} className="h-8 max-w-56 rounded-lg border border-slate-200 bg-surface px-2 text-sm text-slate-700">
      <option value="">全部知识点</option>
      {tree.map((n) => (
        <option key={n.id} value={n.id}>
          {n.title}
        </option>
      ))}
    </select>
  );
}

function PracticeView({ courseId, tree, stats, onAnswered, onGoBank }: { courseId: number; tree: KNode[]; stats: QuizStats | null; onAnswered: () => void; onGoBank: () => void }) {
  const [scope, setScope] = useState<number | null>(null);
  const [mode, setMode] = useState<"mix" | "wrong" | "sim">("mix");
  const [queue, setQueue] = useState<Quiz[] | null>(null);
  const [i, setI] = useState(0);
  const [score, setScore] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);

  const start = async () => {
    setLoading(true);
    try {
      const qs = mode === "wrong" ? await api.get<Quiz[]>(`/api/courses/${courseId}/questions?status=wrong${scope ? `&node_id=${scope}` : ""}`) : await api.get<Quiz[]>(`/api/courses/${courseId}/practice?count=10${scope ? `&node_id=${scope}` : ""}${mode === "sim" ? "&origin=simulated" : ""}`);
      if (!qs.length) toast.info(mode === "wrong" ? "没有待攻克的错题 🎉" : "题库里还没有可做的题，先去「题库」生成");
      setQueue(qs.slice(0, 10));
      setI(0);
      setScore([]);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  if (!queue || queue.length === 0)
    return (
      <Card className="space-y-4 p-5">
        <div className="text-sm text-slate-600">每次 10 题：先做错过的和没做过的，薄弱知识点优先。做错的题会自动进闪卡。</div>
        <div className="flex flex-wrap items-center gap-2">
          <ScopeSelect tree={tree} value={scope} onChange={setScope} />
          <div className="inline-flex rounded-lg bg-slate-100 p-0.5 text-xs">
            {([["mix", "智能混合"], ["wrong", `只做错题${stats?.wrong ? `（${stats.wrong}）` : ""}`], ["sim", "只做仿真题"]] as const).map(([k, l]) => (
              <button key={k} onClick={() => setMode(k)} className={clsx("rounded-md px-2.5 py-1", mode === k ? "bg-surface font-medium text-indigo-700 shadow-sm" : "text-slate-500")}>
                {l}
              </button>
            ))}
          </div>
          <Button onClick={() => void start()} disabled={loading}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : <PenLine size={14} />} 开始练习
          </Button>
        </div>
        {stats?.total === 0 && (
          <div className="text-sm text-slate-500">
            还没有题。<button className="text-indigo-600 underline" onClick={onGoBank}>去题库生成</button>
          </div>
        )}
      </Card>
    );

  if (i >= queue.length) {
    const right = score.filter((s) => s >= 0.6).length;
    return (
      <Card className="space-y-3 p-6 text-center">
        <div className="text-3xl">{right === queue.length ? "🏆" : right >= queue.length / 2 ? "👏" : "💪"}</div>
        <div className="text-lg font-semibold text-slate-800">
          本轮 {right}/{queue.length} 题答对
        </div>
        <div className="text-sm text-slate-500">做错的题已进闪卡，到「闪卡复习」里按遗忘曲线再练。</div>
        <div className="flex justify-center gap-2">
          <Button onClick={() => void start()}>再来一轮</Button>
          <Button variant="outline" onClick={() => setQueue(null)}>
            换个范围
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 text-xs text-slate-500">
        <ProgressBar value={i} max={queue.length} className="flex-1" />
        <span className="tabular-nums">
          {i + 1}/{queue.length}
        </span>
        <button className="underline" onClick={() => setQueue(null)}>
          结束
        </button>
      </div>
      <QuestionCard
        q={queue[i]}
        onResult={(r) => {
          setScore((s) => [...s, r.score ?? 0]);
          onAnswered();
        }}
        onNext={() => setI((n) => n + 1)}
        nextLabel={i + 1 >= queue.length ? "看结果" : "下一题"}
      />
    </div>
  );
}

// ----------------------------------------------------------------- 题库
const ALL_TYPES = Object.keys(QTYPE_NAME);

function BankView({ courseId, tree, onChanged, onGoKnowledge }: { courseId: number; tree: KNode[]; onChanged: () => void; onGoKnowledge: () => void }) {
  const [list, setList] = useState<Quiz[] | null>(null);
  const [status, setStatus] = useState("all");
  const [origin, setOrigin] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [dlg, setDlg] = useState(false);
  const { st, setSt } = useJob(`/api/courses/${courseId}/questions/generate`, (s) => {
    void load();
    onChanged();
    if (s.state === "done") toast.success(`出了 ${s.created} 道题${s.rejected ? `（${s.rejected} 道不合格已丢弃）` : ""}`);
  });

  const load = useCallback(async () => {
    try {
      setList(await api.get<Quiz[]>(`/api/courses/${courseId}/questions?status=${status}${origin ? `&origin=${origin}` : ""}`));
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, [courseId, status, origin]);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => setDlg(true)} disabled={st.state === "running"}>
          <Sparkles size={14} /> AI 出题
        </Button>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-8 rounded-lg border border-slate-200 bg-surface px-2 text-sm">
          <option value="all">全部</option>
          <option value="new">没做过</option>
          <option value="wrong">做错的</option>
          <option value="done">已做对</option>
        </select>
        <select value={origin} onChange={(e) => setOrigin(e.target.value)} className="h-8 rounded-lg border border-slate-200 bg-surface px-2 text-sm">
          <option value="">全部来源</option>
          <option value="generated">按知识点出的</option>
          <option value="simulated">真题仿真</option>
        </select>
      </div>
      <JobBar st={st} label="出题中" onCancel={async () => setSt(await api.post<JobStatus>(`/api/courses/${courseId}/questions/generate/cancel`))} />
      {list === null && <div className="p-8 text-center text-sm text-slate-400">加载中…</div>}
      {list?.length === 0 && (
        <Card className="p-8 text-center text-sm text-slate-500">
          这里还没有题。点「AI 出题」，会按知识梳理里的知识点出题；还没做知识梳理的话先
          <button className="mx-1 text-indigo-600 underline" onClick={onGoKnowledge}>
            去梳理
          </button>
          。
        </Card>
      )}
      <div className="space-y-2">
        {list?.map((q) => (
          <Card key={q.id} className="overflow-hidden">
            <div className="flex cursor-pointer items-start gap-3 p-3" onClick={() => setOpen(open === q.id ? null : q.id)}>
              <span className={clsx("mt-1 h-2.5 w-2.5 shrink-0 rounded-full", q.last_score == null ? "bg-slate-300" : q.last_score >= 0.6 ? "bg-emerald-500" : "bg-rose-500")} title={q.last_score == null ? "没做过" : q.last_score >= 0.6 ? "上次做对" : "上次做错"} />
              <div className="min-w-0 flex-1">
                <div className="line-clamp-2 text-sm text-slate-800">{q.stem.replace(/\$/g, "")}</div>
                <div className="mt-1 flex flex-wrap gap-1.5 text-[11px] text-slate-400">
                  <span>{QTYPE_NAME[q.qtype]}</span>
                  <span>难度 {q.difficulty}</span>
                  {q.origin === "simulated" && <span className="text-violet-600">真题仿真</span>}
                </div>
              </div>
              <button
                className="rounded p-1 text-slate-300 hover:bg-rose-50 hover:text-rose-500"
                title="删除这道题"
                onClick={async (e) => {
                  e.stopPropagation();
                  await api.del(`/api/questions/${q.id}`);
                  void load();
                  onChanged();
                }}
              >
                <Trash2 size={14} />
              </button>
            </div>
            {open === q.id && (
              <div className="border-t border-slate-100 p-4">
                <QuestionCard q={q} compact onResult={() => { onChanged(); void load(); }} />
              </div>
            )}
          </Card>
        ))}
      </div>
      <GenerateDialog open={dlg} onClose={() => setDlg(false)} courseId={courseId} tree={tree} onStarted={(s) => { setSt(s); setDlg(false); }} />
    </div>
  );
}

function GenerateDialog({ open, onClose, courseId, tree, onStarted }: { open: boolean; onClose: () => void; courseId: number; tree: KNode[]; onStarted: (s: JobStatus) => void }) {
  const [scope, setScope] = useState<number | null>(null);
  const [per, setPer] = useState(3);
  const [types, setTypes] = useState<string[]>(["choice", "judge", "fill", "short", "calc"]);
  const [diff, setDiff] = useState(0);
  const [busy, setBusy] = useState(false);
  const leaves = useMemo(() => {
    const count = (n: KNode): number => (n.level === 3 ? 1 : n.children.reduce((a, c) => a + count(c), 0));
    return scope ? count(tree.find((t) => t.id === scope) ?? { id: 0, level: 3, title: "", children: [] }) : tree.reduce((a, t) => a + count(t), 0);
  }, [tree, scope]);
  const nodes = Math.min(24, leaves);
  return (
    <Modal open={open} onClose={onClose} title="AI 出题">
      <div className="space-y-4 p-5 text-sm">
        <div>
          <div className="mb-1 text-xs font-medium text-slate-500">范围</div>
          <ScopeSelect tree={tree} value={scope} onChange={setScope} />
          <div className="mt-1 text-xs text-slate-400">共 {leaves} 个知识点{leaves > 24 ? "，一次最多处理 24 个（重点和考过的优先）" : ""}</div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-500">每个知识点出几题：{per}</div>
          <input type="range" min={1} max={6} value={per} onChange={(e) => setPer(Number(e.target.value))} className="w-full" />
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-500">题型（按顺序轮流）</div>
          <div className="flex flex-wrap gap-1.5">
            {ALL_TYPES.map((t) => (
              <button key={t} onClick={() => setTypes((a) => (a.includes(t) ? a.filter((x) => x !== t) : [...a, t]))} className={clsx("rounded-full border px-2.5 py-1 text-xs", types.includes(t) ? "border-indigo-400 bg-indigo-50 text-indigo-700" : "border-slate-200 text-slate-500")}>
                {QTYPE_NAME[t]}
              </button>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-500">难度</div>
          <select value={diff} onChange={(e) => setDiff(Number(e.target.value))} className="h-8 rounded-lg border border-slate-200 bg-surface px-2">
            <option value={0}>混合（2~4 星）</option>
            {[1, 2, 3, 4, 5].map((d) => (
              <option key={d} value={d}>{"★".repeat(d)}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center justify-between border-t border-slate-100 pt-3">
          <span className="text-xs text-slate-400">预计 {nodes} 次模型调用，约 {nodes * per} 道题</span>
          <Button
            disabled={busy || !types.length || leaves === 0}
            onClick={async () => {
              setBusy(true);
              try {
                onStarted(await api.post<JobStatus>(`/api/courses/${courseId}/questions/generate`, { node_ids: scope ? [scope] : null, per_node: per, types, difficulty: diff || null }));
              } catch (e) {
                toast.error((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} 开始出题
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ----------------------------------------------------------------- 真题仿真
function Bars({ title, data, order }: { title: string; data: Record<string, number>; order?: string[] }) {
  const entries = (order ? order.filter((k) => data[k]).map((k) => [k, data[k]] as [string, number]) : Object.entries(data)).filter(([k]) => k !== "未知");
  const max = Math.max(1, ...entries.map(([, n]) => n));
  return (
    <div className="min-w-0 flex-1 basis-40">
      <div className="mb-1.5 text-xs font-semibold text-slate-500">{title}</div>
      <div className="space-y-1">
        {entries.map(([k, n]) => (
          <div key={k} className="flex items-center gap-2 text-xs">
            <span className="w-8 shrink-0 text-slate-600">{k}</span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
              <div className="h-full rounded-full bg-violet-500" style={{ width: `${(n / max) * 100}%` }} />
            </div>
            <span className="w-5 text-right tabular-nums text-slate-500">{n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SimView({ courseId, onChanged, onGoExams }: { courseId: number; onChanged: () => void; onGoExams: () => void }) {
  const [profile, setProfile] = useState<ExamProfile | null>(null);
  const [papers, setPapers] = useState<PaperBrief[]>([]);
  const [paperId, setPaperId] = useState<number | null>(null);
  const loadAll = useCallback(async () => {
    try {
      const [p, ps] = await Promise.all([api.get<ExamProfile>(`/api/courses/${courseId}/exam-profile`), api.get<PaperBrief[]>(`/api/courses/${courseId}/papers`)]);
      setProfile(p);
      setPapers(ps);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, [courseId]);
  useEffect(() => {
    void loadAll();
  }, [loadAll]);
  const sim = useJob(`/api/courses/${courseId}/simulate`, (s) => {
    onChanged();
    if (s.state === "done") toast.success(`仿真出了 ${s.created} 道题，可在「题库 → 真题仿真」里做`);
  });
  const paper = useJob(`/api/courses/${courseId}/papers/status`, (s) => {
    void loadAll();
    if (s.state === "done" && s.paper_id) {
      onChanged();
      setPaperId(s.paper_id);
    }
  });

  const run = async (url: string, body: object, set: (s: JobStatus) => void) => {
    try {
      set(await api.post<JobStatus>(url, body));
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  if (paperId) return <PaperView id={paperId} onBack={() => { setPaperId(null); void loadAll(); }} onAnswered={onChanged} />;
  if (!profile) return <div className="p-8 text-center text-sm text-slate-400">加载中…</div>;
  if (profile.analyzed === 0)
    return (
      <Card className="space-y-2 p-8 text-center text-sm text-slate-500">
        <div>真题仿真要先「学」真题：上传往年试卷并在「试卷考点」里分析后，这里会给出考情画像，并能照着真题的考点、能力和结构出新题。</div>
        <Button variant="soft" onClick={onGoExams}>去试卷考点</Button>
      </Card>
    );
  const busy = sim.st.state === "running" || paper.st.state === "running";

  return (
    <div className="space-y-4">
      <Card className="space-y-4 p-4">
        <div className="flex items-center justify-between">
          <div className="font-semibold text-slate-800">考情画像 · {profile.analyzed} 道真题</div>
          {profile.avg_points != null && <span className="text-xs text-slate-400">平均 {profile.avg_points} 分/题</span>}
        </div>
        <div className="flex flex-wrap gap-5">
          <Bars title="题型" data={profile.by_type} />
          <Bars title="能力层级" data={profile.by_ability} order={["记忆", "理解", "应用", "分析", "综合"]} />
          <Bars title="难度" data={Object.fromEntries(Object.entries(profile.by_difficulty).map(([k, v]) => [`${k}星`, v]))} />
        </div>
        {profile.top_nodes.length > 0 && (
          <div>
            <div className="mb-1.5 text-xs font-semibold text-slate-500">高频考点</div>
            <div className="flex flex-wrap gap-1.5">
              {profile.top_nodes.map((n) => (
                <span key={n.id} className="rounded-full bg-violet-50 px-2.5 py-1 text-xs text-violet-700">
                  {n.title.replace(/（.*）$/, "")} ×{n.count}
                </span>
              ))}
            </div>
          </div>
        )}
        {profile.uncovered_key_nodes.length > 0 && (
          <div>
            <div className="mb-1.5 text-xs font-semibold text-slate-500">重点但真题里还没考过（押题方向）</div>
            <div className="flex flex-wrap gap-1.5">
              {profile.uncovered_key_nodes.map((n) => (
                <span key={n.id} className="rounded-full bg-amber-50 px-2.5 py-1 text-xs text-amber-800">
                  {n.title.replace(/（.*）$/, "")}
                </span>
              ))}
            </div>
          </div>
        )}
      </Card>

      <Card className="space-y-3 p-4">
        <div className="font-semibold text-slate-800">照真题出新题</div>
        <div className="text-xs leading-5 text-slate-500">
          只学真题的考点、能力层级、难度、题型和分值；题目的背景、数据、措辞全部重新出，并和这门课所有真题逐一比对，太像的自动退回重出（相似度会显示在题上）。
        </div>
        {profile.exams.map((e) => (
          <div key={e.document_id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 p-2.5 text-sm">
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium text-slate-700">{e.name}</div>
              <div className="text-xs text-slate-400">
                {e.questions} 题{e.total_points ? ` · ${e.total_points} 分` : ""} · {e.sections.map((s) => `${s.qtype}${s.count}`).join(" / ")}
              </div>
            </div>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(`/api/courses/${courseId}/simulate`, { exam_document_id: e.document_id }, sim.setSt)}>
              <Sparkles size={13} /> 仿真每题各出 1 道
            </Button>
            <Button size="sm" disabled={busy} onClick={() => void run(`/api/courses/${courseId}/papers`, { exam_document_id: e.document_id }, paper.setSt)}>
              <FileText size={13} /> 生成模拟卷
            </Button>
          </div>
        ))}
        <JobBar st={sim.st} label="仿真出题" onCancel={async () => sim.setSt(await api.post<JobStatus>(`/api/courses/${courseId}/simulate/cancel`))} />
        <JobBar st={paper.st} label="组卷中" onCancel={async () => paper.setSt(await api.post<JobStatus>(`/api/courses/${courseId}/papers/cancel`).catch(() => paper.st))} />
      </Card>

      {papers.length > 0 && (
        <Card className="space-y-2 p-4">
          <div className="font-semibold text-slate-800">我的模拟卷</div>
          {papers.map((p) => (
            <div key={p.id} className="flex items-center gap-2 rounded-lg border border-slate-200 p-2.5 text-sm">
              <button className="min-w-0 flex-1 truncate text-left font-medium text-indigo-700 hover:underline" onClick={() => setPaperId(p.id)}>
                {p.title}
              </button>
              <span className="text-xs text-slate-400">{p.created_at.slice(0, 10)}</span>
              <button
                className="rounded p-1 text-slate-300 hover:bg-rose-50 hover:text-rose-500"
                onClick={async () => {
                  if (!confirm(`删除「${p.title}」？卷里的题会保留在题库里。`)) return;
                  await api.del(`/api/papers/${p.id}`);
                  void loadAll();
                }}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}

function PaperView({ id, onBack, onAnswered }: { id: number; onBack: () => void; onAnswered: () => void }) {
  const [paper, setPaper] = useState<PaperDetail | null>(null);
  useEffect(() => {
    api.get<PaperDetail>(`/api/papers/${id}`).then(setPaper).catch((e) => toast.error(e.message));
  }, [id]);
  if (!paper) return <div className="p-8 text-center text-sm text-slate-400">加载中…</div>;
  let sec = "";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onBack}>
          ← 返回
        </Button>
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold text-slate-800">{paper.title}</div>
          <div className="text-xs text-slate-400">
            满分 {paper.total_points} 分 · 建议 {paper.duration_min} 分钟 · 题目为 AI 依真题考点原创
          </div>
        </div>
        <a className="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 px-2.5 text-xs text-slate-600 hover:bg-slate-50" href={`${API_BASE}/api/papers/${id}/export`}>
          <Download size={12} /> 试卷
        </a>
        <a className="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 px-2.5 text-xs text-slate-600 hover:bg-slate-50" href={`${API_BASE}/api/papers/${id}/export?answers=true`}>
          <Download size={12} /> 含答案
        </a>
      </div>
      {paper.items.map((it) => {
        const head = it.section !== sec ? (sec = it.section) : null;
        return (
          <div key={it.id} className="space-y-2">
            {head && <div className="pt-2 text-sm font-semibold text-slate-600">{head}</div>}
            <QuestionCard q={it} onResult={onAnswered} hint={<span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">第 {it.order} 题</span>} />
          </div>
        );
      })}
    </div>
  );
}
