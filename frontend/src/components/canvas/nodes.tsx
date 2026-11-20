"use client";
import { memo, useState, type ReactNode } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import clsx from "clsx";
import { BookMarked, CornerDownRight, FileText, GitBranch, Layers, Lightbulb, RefreshCw, Send, Sparkles, StickyNote, Trash2, X } from "lucide-react";
import { Markdown } from "@/components/Markdown";
import { useWorkspace, type FlowNode } from "@/stores/workspace";
import { EXPLAIN_PROMPT } from "@/components/reader/ReaderPanel";

const KIND_LABEL: Record<string, string> = { highlight: "原文提问", followup: "追问", term: "术语分支", merge: "汇总对比" };

function Shell({
  tone,
  icon,
  title,
  right,
  children,
  footer,
}: {
  tone: string;
  icon: ReactNode;
  title: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-surface shadow-md transition-shadow">
      <Handle type="target" position={Position.Left} />
      <div className={clsx("flex items-center gap-1.5 px-3 py-2 text-xs font-medium", tone)}>
        {icon}
        <div className="min-w-0 flex-1 truncate">{title}</div>
        {right}
      </div>
      <div className="px-3.5 py-2.5">{children}</div>
      {footer && <div className="border-t border-slate-100 px-2 py-1.5">{footer}</div>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function FootBtn({ icon, label, onClick, danger }: { icon: ReactNode; label?: string; onClick: () => void; danger?: boolean }) {
  return (
    <button
      className={clsx(
        "nodrag flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] transition",
        danger ? "text-slate-400 hover:bg-rose-50 hover:text-rose-600" : "text-slate-500 hover:bg-indigo-50 hover:text-indigo-700",
      )}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={label}
    >
      {icon}
      {label}
    </button>
  );
}

/** 节点底部“追问”输入框 */
function AskBox({ placeholder, onSubmit, onClose }: { placeholder: string; onSubmit: (q: string) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  return (
    <form
      className="nodrag flex items-center gap-1 px-1 pb-0.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (q.trim()) onSubmit(q.trim());
      }}
    >
      <input
        autoFocus
        className="min-w-0 flex-1 rounded-md border border-slate-200 px-2 py-1 text-xs outline-none focus:border-indigo-400"
        placeholder={placeholder}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
      />
      <button type="submit" className="rounded-md bg-indigo-600 p-1.5 text-white disabled:opacity-40" disabled={!q.trim()}>
        <Send size={12} />
      </button>
      <button type="button" className="rounded-md p-1 text-slate-400 hover:bg-slate-100" onClick={onClose}>
        <X size={12} />
      </button>
    </form>
  );
}

function useCommonActions(id: number) {
  const ws = useWorkspace.getState;
  return {
    followup: (q: string) => ws().ask({ question: q, kind: "followup", parentIds: [id] }),
    remove: () => {
      if (confirm("删除这个节点？（与之相连的连线也会删除）")) void ws().deleteNodes([id]);
    },
  };
}

// ---------------- 原文摘录卡 ----------------
export const ExcerptNode = memo(function ExcerptNode({ data, id }: NodeProps<FlowNode>) {
  const docs = useWorkspace((s) => s.docs);
  const [asking, setAsking] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const a = data.anchor;
  const doc = docs.find((d) => d.id === a?.document_id);
  const { remove } = useCommonActions(Number(id));
  const ws = useWorkspace.getState;
  return (
    <Shell
      tone="bg-amber-50 text-amber-800"
      icon={<FileText size={13} />}
      title={
        <button className="nodrag truncate hover:underline" onClick={() => a && ws().jumpTo(a)} title="跳回原文高亮位置">
          {doc?.filename ?? "原文"} · {doc?.filetype === "pdf" ? `第 ${a?.page} 页` : "摘录"}
        </button>
      }
      footer={
        asking ? (
          <AskBox
            placeholder="针对这段原文提问…"
            onSubmit={(q) => {
              setAsking(false);
              void ws().ask({ question: q, kind: "highlight", parentIds: [Number(id)] });
            }}
            onClose={() => setAsking(false)}
          />
        ) : (
          <div className="flex items-center gap-0.5">
            <FootBtn icon={<GitBranch size={12} />} label="提问" onClick={() => setAsking(true)} />
            <FootBtn icon={<Lightbulb size={12} />} label="通俗解释" onClick={() => void ws().ask({ question: EXPLAIN_PROMPT, kind: "highlight", parentIds: [Number(id)] })} />
            <FootBtn icon={<Layers size={12} />} label="闪卡" onClick={() => ws().openCardDraft({ text: data.content, source: "highlight", source_ref: id })} />
            <div className="flex-1" />
            <FootBtn icon={<Trash2 size={12} />} onClick={remove} danger />
          </div>
        )
      }
    >
      <blockquote
        className={clsx("nodrag cursor-text select-text border-l-2 border-amber-300 pl-2.5 text-[13px] leading-6 text-slate-700 whitespace-pre-wrap", !expanded && "line-clamp-6")}
      >
        {data.content}
      </blockquote>
      {data.content.length > 220 && (
        <button className="nodrag mt-1 text-[11px] text-amber-700 hover:underline" onClick={() => setExpanded(!expanded)}>
          {expanded ? "收起" : "展开全文"}
        </button>
      )}
    </Shell>
  );
});

// ---------------- AI 问答卡 ----------------
export const QANode = memo(function QANode({ data, id }: NodeProps<FlowNode>) {
  const streaming = useWorkspace((s) => !!s.streaming[id]);
  const [asking, setAsking] = useState(false);
  const [term, setTerm] = useState<string | null>(null);
  const { followup, remove } = useCommonActions(Number(id));
  const ws = useWorkspace.getState;
  const nid = Number(id);

  const onCite = (n: number) => {
    const s = data.sources?.[n - 1];
    if (s) ws().jumpTo({ document_id: s.document_id, page: s.page, quote: s.snippet, rects: [] });
  };

  return (
    <Shell
      tone="bg-indigo-50 text-indigo-800"
      icon={<Sparkles size={13} />}
      title={<span>{KIND_LABEL[data.kind] ?? "问答"}</span>}
      right={<span className="rounded bg-surface/70 px-1.5 text-[10px] text-indigo-500">第 {data.depth} 层</span>}
      footer={
        asking ? (
          <AskBox
            placeholder="继续追问…（只会带上这条分支的上下文）"
            onSubmit={(q) => {
              setAsking(false);
              void followup(q);
            }}
            onClose={() => setAsking(false)}
          />
        ) : (
          <div className="flex items-center gap-0.5">
            <FootBtn icon={<CornerDownRight size={12} />} label="追问" onClick={() => setAsking(true)} />
            <FootBtn
              icon={<Layers size={12} />}
              label="闪卡"
              onClick={() => ws().openCardDraft({ text: data.content, context: `问题：${data.question}`, source: "node", source_ref: id })}
            />
            <FootBtn
              icon={<StickyNote size={12} />}
              label="沉淀笔记"
              onClick={() => void ws().createNode({ type: "note", title: data.question.slice(0, 30), content: data.content, parent_ids: [nid] })}
            />
            <div className="flex-1" />
            {!streaming && <FootBtn icon={<RefreshCw size={12} />} onClick={() => void ws().streamAnswer(nid)} label="" />}
            <FootBtn icon={<Trash2 size={12} />} onClick={remove} danger />
          </div>
        )
      }
    >
      <div className="mb-2 text-[13.5px] font-semibold leading-6 text-slate-800">{data.question}</div>
      {term && (
        <div className="nodrag mb-2 flex flex-wrap items-center gap-1 rounded-lg bg-indigo-50/70 px-2 py-1.5 text-xs">
          <span className="font-medium text-indigo-700">「{term}」</span>
          <button
            className="rounded-md bg-surface px-2 py-0.5 text-indigo-700 shadow-sm hover:bg-indigo-100"
            onClick={() => {
              setTerm(null);
              void ws().ask({ question: `「${term}」是什么？结合上文，讲清它在这里的含义和作用。`, kind: "term", parentIds: [nid] });
            }}
          >
            <GitBranch size={11} className="mr-0.5 inline" />
            生长分支
          </button>
          <button
            className="rounded-md bg-surface px-2 py-0.5 text-indigo-700 shadow-sm hover:bg-indigo-100"
            onClick={() => {
              setTerm(null);
              void ws().ask({
                question: `用 3~5 句话简明解释「${term}」（结合上文语境），最后给一个一句话的类比。`,
                kind: "term",
                type: "concept",
                title: term,
                parentIds: [nid],
              });
            }}
          >
            <BookMarked size={11} className="mr-0.5 inline" />
            概念速查
          </button>
          <button className="ml-auto text-slate-400" onClick={() => setTerm(null)}>
            <X size={12} />
          </button>
        </div>
      )}
      <div className={clsx("nodrag nowheel scroll-thin max-h-[440px] cursor-text select-text overflow-y-auto pr-1", streaming && "streaming-caret")}>
        {data.content ? (
          <Markdown text={data.content} onTerm={(t) => setTerm(t)} onCite={onCite} />
        ) : streaming ? (
          <div className="text-xs text-slate-400">思考中…</div>
        ) : (
          <button className="rounded-md bg-indigo-600 px-2.5 py-1 text-xs text-white" onClick={() => void ws().streamAnswer(nid)}>
            生成回答
          </button>
        )}
      </div>
      {!!data.sources?.length && !streaming && (
        <div className="nodrag mt-2 flex flex-wrap gap-1 border-t border-dashed border-slate-100 pt-2">
          {data.sources.map((s, i) => (
            <button
              key={s.chunk_id}
              className="rounded-md bg-slate-50 px-1.5 py-0.5 text-[10.5px] text-slate-500 hover:bg-indigo-50 hover:text-indigo-700"
              title={s.snippet}
              onClick={() => onCite(i + 1)}
            >
              [{i + 1}] {s.filename.length > 14 ? s.filename.slice(0, 14) + "…" : s.filename} p.{s.page}
            </button>
          ))}
        </div>
      )}
    </Shell>
  );
});

// ---------------- 笔记卡 ----------------
export const NoteNode = memo(function NoteNode({ data, id }: NodeProps<FlowNode>) {
  const [editing, setEditing] = useState(!data.content);
  const [draft, setDraft] = useState(data.content);
  const [title, setTitle] = useState(data.title);
  const [asking, setAsking] = useState(false);
  const { followup, remove } = useCommonActions(Number(id));
  const ws = useWorkspace.getState;
  const save = () => {
    ws().patchNode(Number(id), { content: draft, title });
    setEditing(false);
  };
  return (
    <Shell
      tone="bg-emerald-50 text-emerald-800"
      icon={<StickyNote size={13} />}
      title={
        <input
          className="nodrag w-full bg-transparent outline-none placeholder:text-emerald-400"
          value={title}
          placeholder="笔记标题"
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => title !== data.title && ws().patchNode(Number(id), { title })}
        />
      }
      footer={
        asking ? (
          <AskBox placeholder="基于这条笔记提问…" onSubmit={(q) => { setAsking(false); void followup(q); }} onClose={() => setAsking(false)} />
        ) : (
          <div className="flex items-center gap-0.5">
            <FootBtn icon={<CornerDownRight size={12} />} label="提问" onClick={() => setAsking(true)} />
            <FootBtn icon={<Layers size={12} />} label="闪卡" onClick={() => ws().openCardDraft({ text: data.content, source: "node", source_ref: id })} />
            <div className="flex-1" />
            <FootBtn icon={<Trash2 size={12} />} onClick={remove} danger />
          </div>
        )
      }
    >
      {editing ? (
        <div className="nodrag">
          <textarea
            autoFocus
            className="nowheel h-32 w-full resize-y rounded-md border border-slate-200 p-2 text-[13px] outline-none focus:border-emerald-400"
            placeholder="用自己的话写下理解（支持 Markdown 与 $公式$）"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) save();
            }}
          />
          <div className="mt-1 flex justify-end gap-1">
            <button className="rounded-md px-2 py-0.5 text-xs text-slate-500 hover:bg-slate-100" onClick={() => { setDraft(data.content); setEditing(false); }}>
              取消
            </button>
            <button className="rounded-md bg-emerald-600 px-2 py-0.5 text-xs text-white" onClick={save}>
              保存 (Ctrl+Enter)
            </button>
          </div>
        </div>
      ) : (
        <div className="nodrag nowheel scroll-thin max-h-[360px] cursor-text overflow-y-auto" onDoubleClick={() => setEditing(true)} title="双击编辑">
          <Markdown text={data.content || "_（空笔记，双击编辑）_"} />
        </div>
      )}
    </Shell>
  );
});

// ---------------- 概念卡 ----------------
export const ConceptNode = memo(function ConceptNode({ data, id }: NodeProps<FlowNode>) {
  const streaming = useWorkspace((s) => !!s.streaming[id]);
  const { remove } = useCommonActions(Number(id));
  const ws = useWorkspace.getState;
  return (
    <Shell
      tone="bg-violet-50 text-violet-800"
      icon={<BookMarked size={13} />}
      title={<span className="font-semibold">{data.title || "概念"}</span>}
      footer={
        <div className="flex items-center gap-0.5">
          <FootBtn
            icon={<GitBranch size={12} />}
            label="深挖"
            onClick={() => void ws().ask({ question: `深入讲讲「${data.title}」：它的定义、为什么重要、常见误区。`, kind: "term", parentIds: [Number(id)] })}
          />
          <FootBtn
            icon={<Layers size={12} />}
            label="闪卡"
            onClick={() => ws().openCardDraft({ text: `${data.title}：${data.content}`, source: "node", source_ref: id })}
          />
          <div className="flex-1" />
          <FootBtn icon={<Trash2 size={12} />} onClick={remove} danger />
        </div>
      }
    >
      <div className={clsx("nodrag nowheel scroll-thin max-h-[300px] overflow-y-auto", streaming && "streaming-caret")}>
        {data.content ? <Markdown text={data.content} /> : <div className="text-xs text-slate-400">{streaming ? "查询中…" : "（无内容）"}</div>}
      </div>
    </Shell>
  );
});

export const nodeTypes = { excerpt: ExcerptNode, qa: QANode, note: NoteNode, concept: ConceptNode };
