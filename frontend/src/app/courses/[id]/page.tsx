"use client";
import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import clsx from "clsx";
import { ArrowLeft, BookOpen, ClipboardCheck, Columns2, CalendarClock, Layers, Library, ListTree, Network, PenLine, Search } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Course } from "@/lib/types";
import { useWorkspace } from "@/stores/workspace";
import { ReaderPanel } from "@/components/reader/ReaderPanel";
import { Canvas } from "@/components/canvas/Canvas";
import { CardDraftDialog } from "@/components/flashcards/CardDraftDialog";
import { FlashcardsPanel } from "@/components/flashcards/FlashcardsPanel";
import { LibraryPanel } from "@/components/library/LibraryPanel";
import { SearchPanel } from "@/components/library/SearchPanel";
import { KnowledgePanel } from "@/components/knowledge/KnowledgePanel";
import { ExamPanel } from "@/components/exam/ExamPanel";
import { QuizPanel, type QuizFocus } from "@/components/quiz/QuizPanel";
import { SprintPanel } from "@/components/sprint/SprintPanel";

type Tab = "explore" | "library" | "knowledge" | "exams" | "quiz" | "sprint" | "cards" | "search";

const TABS: { key: Tab; label: string; icon: React.ElementType }[] = [
  { key: "explore", label: "阅读 · 探索画布", icon: Network },
  { key: "library", label: "资料库", icon: Library },
  { key: "knowledge", label: "知识梳理", icon: ListTree },
  { key: "exams", label: "试卷考点", icon: ClipboardCheck },
  { key: "quiz", label: "题库练习", icon: PenLine },
  { key: "sprint", label: "考前冲刺", icon: CalendarClock },
  { key: "cards", label: "闪卡复习", icon: Layers },
  { key: "search", label: "检索", icon: Search },
];

export default function CoursePage() {
  const { id } = useParams<{ id: string }>();
  const courseId = Number(id);
  const [course, setCourse] = useState<Course | null>(null);
  const [tab, setTab] = useState<Tab>("explore");
  const [split, setSplit] = useState(44);
  const [focus, setFocus] = useState<QuizFocus | undefined>();
  const { load, docs, readerVisible, canvasVisible, setPanels, jumpTo } = useWorkspace();
  const containerRef = useRef<HTMLDivElement>(null);

  const reloadCourse = () => api.get<Course>(`/api/courses/${courseId}`).then(setCourse).catch((e) => toast.error(e.message));
  useEffect(() => {
    void reloadCourse();
    void load(courseId).then(() => {
      // 支持从跨课程检索跳转过来：?doc=1&page=3
      const sp = new URLSearchParams(window.location.search);
      const doc = Number(sp.get("doc"));
      if (doc) jumpTo({ document_id: doc, page: Number(sp.get("page")) || 1, quote: "", rects: [] });
    }).catch((e) => toast.error(e.message));
    try {
      const s = Number(localStorage.getItem("alp-split"));
      if (s > 15 && s < 85) setSplit(s);
    } catch {
      /* ignore */
    }
  }, [courseId, load, jumpTo]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (tab === "explore" && docs.length === 0 && course && course.stats.documents === 0) setTab("library");
  }, [docs.length, course, tab]);

  const startDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    const box = containerRef.current?.getBoundingClientRect();
    if (!box) return;
    const move = (ev: MouseEvent) => setSplit(Math.min(80, Math.max(20, ((ev.clientX - box.left) / box.width) * 100)));
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      setSplit((s) => {
        try {
          localStorage.setItem("alp-split", String(Math.round(s)));
        } catch {
          /* ignore */
        }
        return s;
      });
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-slate-200 bg-surface px-4">
        <Link href="/" className="rounded-md p-1 text-slate-400 hover:bg-slate-100" title="返回课程世界">
          <ArrowLeft size={18} />
        </Link>
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-xl">{course?.emoji}</span>
          <span className="truncate font-semibold text-slate-800">{course?.name ?? "…"}</span>
          <ExamChip date={course?.exam_date} onClick={() => setTab("sprint")} />
        </div>
        <div className="ml-4 flex items-center gap-1">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              title={label}
              aria-label={label}
              className={clsx(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition",
                tab === key ? "bg-ink text-ink-fg" : "text-slate-500 hover:bg-slate-100",
              )}
            >
              <Icon size={15} />
              <span className="hidden lg:inline">{label}</span>
            </button>
          ))}
        </div>
        {tab === "explore" && (
          <div className="ml-auto flex items-center gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
            <ViewBtn active={readerVisible && !canvasVisible} onClick={() => setPanels({ readerVisible: true, canvasVisible: false })} icon={<BookOpen size={14} />} label="全屏阅读" />
            <ViewBtn active={readerVisible && canvasVisible} onClick={() => setPanels({ readerVisible: true, canvasVisible: true })} icon={<Columns2 size={14} />} label="分屏" />
            <ViewBtn active={!readerVisible && canvasVisible} onClick={() => setPanels({ readerVisible: false, canvasVisible: true })} icon={<Network size={14} />} label="全屏画布" />
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1">
        {tab === "explore" && (
          <div ref={containerRef} className="flex h-full">
            {readerVisible && (
              <div className="h-full min-w-0 border-r border-slate-200" style={{ width: canvasVisible ? `${split}%` : "100%" }}>
                <ReaderPanel onGoLibrary={() => setTab("library")} />
              </div>
            )}
            {readerVisible && canvasVisible && (
              <div className="w-1.5 shrink-0 cursor-col-resize bg-slate-100 transition hover:bg-indigo-200" onMouseDown={startDrag} title="拖动调整宽度" />
            )}
            {canvasVisible && (
              <div className="h-full min-w-0 flex-1 bg-page">
                <Canvas />
              </div>
            )}
          </div>
        )}
        {tab === "library" && (
          <div className="h-full overflow-auto">
            <LibraryPanel courseId={courseId} onOpen={() => setTab("explore")} />
          </div>
        )}
        {tab === "knowledge" && (
          <div className="h-full">
            <KnowledgePanel courseId={courseId} courseName={course?.name ?? "课程"} onOpenReader={() => setTab("explore")} onGoLibrary={() => setTab("library")} />
          </div>
        )}
        {tab === "exams" && (
          <div className="h-full overflow-auto">
            <ExamPanel courseId={courseId} onOpenReader={() => setTab("explore")} onGoLibrary={() => setTab("library")} onGoKnowledge={() => setTab("knowledge")} />
          </div>
        )}
        {tab === "quiz" && (
          <div className="h-full overflow-auto">
            <QuizPanel courseId={courseId} onGoKnowledge={() => setTab("knowledge")} onGoExams={() => setTab("exams")} focus={focus} />
          </div>
        )}
        {tab === "sprint" && (
          <div className="h-full overflow-auto">
            <SprintPanel
              courseId={courseId}
              course={course}
              onCourseChanged={() => void reloadCourse()}
              onPractice={(nodeId, mode) => {
                setFocus({ nodeId, mode, nonce: Date.now() });
                setTab("quiz");
              }}
              onGoCards={() => setTab("cards")}
              onGoSim={() => {
                setFocus({ nodeId: null, mode: "sim", nonce: Date.now() });
                setTab("quiz");
              }}
              onGoKnowledge={() => setTab("knowledge")}
            />
          </div>
        )}
        {tab === "cards" && (
          <div className="h-full overflow-auto">
            <FlashcardsPanel courseId={courseId} />
          </div>
        )}
        {tab === "search" && (
          <div className="h-full overflow-auto">
            <SearchPanel courseId={courseId} onOpen={() => setTab("explore")} />
          </div>
        )}
      </div>
      <CardDraftDialog />
    </div>
  );
}

function ViewBtn({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button onClick={onClick} title={label} className={clsx("flex items-center gap-1 rounded-md px-2 py-1", active ? "bg-surface text-slate-800 shadow-sm" : "text-slate-500")}>
      {icon}
      <span className="hidden xl:inline">{label}</span>
    </button>
  );
}

function ExamChip({ date, onClick }: { date?: string; onClick: () => void }) {
  if (!date) return null;
  const days = Math.ceil((new Date(date + "T00:00:00").getTime() - new Date(new Date().toDateString()).getTime()) / 86400000);
  if (days < 0) return null;
  return (
    <button onClick={onClick} title={`考试日期 ${date}，点开看冲刺计划`} className={clsx("shrink-0 rounded-full px-2 py-0.5 text-xs font-medium", days <= 7 ? "bg-rose-50 text-rose-600" : "bg-indigo-50 text-indigo-600")}>
      {days === 0 ? "今天考试" : `距考试 ${days} 天`}
    </button>
  );
}
