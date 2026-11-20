export interface CourseStats {
  documents: number;
  nodes: number;
  flashcards: number;
  due: number;
}

export interface Course {
  id: number;
  name: string;
  description: string;
  emoji: string;
  color: string;
  created_at: string;
  stats: CourseStats;
}

/** 资料种类：课件 / 试卷 / 参考资料 */
export type DocKind = "slides" | "exam" | "reference";

export interface Doc {
  id: number;
  course_id: number;
  filename: string;
  filetype: "pdf" | "md" | "txt" | "docx" | "pptx";
  kind: DocKind;
  source?: "local" | "cloud";
  source_path?: string;
  page_count: number;
  chunk_count: number;
  status: "processing" | "ready" | "error";
  error: string;
  empty_pages: string;
  created_at: string;
}

/** 归一化矩形（相对页面宽高 0~1） */
export interface NRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Anchor {
  document_id: number;
  page: number;
  quote: string;
  rects: NRect[];
}

export interface Source {
  chunk_id: number;
  document_id: number;
  filename: string;
  page: number;
  filetype: string;
  snippet: string;
}

export type NodeType = "excerpt" | "qa" | "note" | "concept";

export interface CanvasNodeData {
  id: number;
  course_id: number;
  type: NodeType;
  kind: string;
  title: string;
  question: string;
  content: string;
  anchor: Anchor | null;
  sources: Source[];
  x: number;
  y: number;
  depth: number;
  rewarded: boolean;
  created_at: string;
  [key: string]: unknown;
}

export interface CanvasEdgeData {
  id: number;
  course_id: number;
  source: number;
  target: number;
}

export interface Flashcard {
  id: number;
  course_id: number;
  front: string;
  back: string;
  card_type: string;
  source: string;
  source_ref: string;
  due: string;
  state: number;
  reps: number;
  lapses: number;
  created_at: string;
  preview?: Record<string, string>;
}

export interface CardDraft {
  front: string;
  back: string;
  card_type: string;
}

export interface Achievement {
  key: string;
  name: string;
  desc: string;
  icon: string;
  category?: string;
  unlocked?: boolean;
  unlocked_at?: string | null;
}

export interface Reward {
  event: string;
  xp: number;
  coins: number;
  total_xp: number;
  level: number;
  xp_in_level: number;
  xp_to_next: number;
  level_up: boolean;
  today_xp: number;
  daily_goal: number;
  streak_lit_now: boolean;
  streak: number;
  new_achievements: Achievement[];
}

export interface Profile {
  total_xp: number;
  coins: number;
  level: number;
  xp_in_level: number;
  xp_to_next: number;
  today: { xp: number; reviews: number; questions: number; goal: number };
  streak: { current: number; longest: number; today_lit: boolean };
  achievements: Achievement[];
}

export interface HeatDay {
  date: string;
  xp: number;
  reviews: number;
  questions: number;
}

export interface SearchHit {
  chunk_id: number;
  document_id: number;
  filename: string;
  filetype: string;
  course_id: number;
  page: number;
  text: string;
  score: number;
  via: string;
}

export interface TierConfig {
  provider: "openai" | "tsinghua";
  model: string;
  base_url: string;
  api_key: string;
  price_in: number;
  price_out: number;
}

export interface LLMConfig {
  base_url: string;
  api_key: string;
  has_api_key?: boolean;
  currency: string;
  tiers: Record<"fast" | "strong" | "translate" | "vision" | "embedding", TierConfig>;
}

export interface Usage {
  month: string;
  total_tokens: number;
  total_cost: number;
  currency: string;
  items: { tier: string; model: string; prompt_tokens: number; completion_tokens: number; calls: number; cost: number }[];
}

export interface TsinghuaStatus {
  state: "not_configured" | "valid" | "refreshing" | "needs_user" | "credentials_invalid" | "error" | "expired" | "stopped";
  message: string;
  token_valid: boolean;
  obtained_at: number | null;
  expires_at: number | null;
  expires_in_s: number | null;
  next_run_at: number | null;
  consecutive_failures: number;
  last_attempt_at: number | null;
  last_success_at: number | null;
  has_credentials: boolean;
  has_session: boolean;
  password_blocked: boolean;
  running: boolean;
  history: { at: number; outcome: string; message: string }[];
  models: string[];
  legacy_unavailable: string[];
}

/** 文档翻译 */
export type TMode = "orig" | "both" | "trans";

export interface TBlock {
  id: number;
  kind: string;
  text: string;
  translatable: boolean;
  translation: string | null;
}

export interface DocBlockInfo {
  id: number;
  page: number;
  idx: number;
  kind: string;
  text: string;
  table: [number, number, number] | null;
}

export interface TStatus {
  translatable: number;
  translated: number;
  running: boolean;
  state: "idle" | "running" | "done" | "error" | "cancelled";
  error: string;
  model: string;
  chain: string[];
}

/** 知识梳理 */
export interface KRef {
  document_id: number;
  page: number;
}

export interface KNode {
  id: number;
  document_id: number | null;
  level: 1 | 2 | 3;
  title: string;
  summary: string;
  points: string[];
  refs: KRef[];
  importance: number;
  exam_count: number;
  exam_total: number;
  children: KNode[];
}

export interface KStatus {
  state: "idle" | "running" | "done" | "error" | "cancelled";
  total: number;
  done: number;
  current: string;
  error: string;
  failed: { document_id: number; error: string }[];
  skipped: number;
}

export interface KnowledgeData {
  tree: KNode[];
  status: KStatus;
  docs: { slides: number; built: number; exam: number; reference: number };
}

/** 试卷 */
export interface ExamQ {
  id: number;
  document_id: number;
  number: string;
  qtype: string;
  text: string;
  points: number | null;
  answer: string;
  topics: string[];
  ability: string;
  difficulty: number;
  nodes: { id: number; title: string; document_id: number | null; page: number | null }[];
}

export interface ExamDoc {
  document_id: number;
  filename: string;
  status: string;
  analysis: { state: string; stage: string; total: number; done: number; error: string };
  questions: ExamQ[];
}

/** 课件某一页上的考点提示 */
export interface ExamMark {
  question_id: number;
  exam: string;
  number: string;
  qtype: string;
  points: number | null;
  excerpt: string;
  topics: string[];
  node: string;
  ability: string;
}

/** 清华云盘 */
export interface CloudImportStatus {
  state: "idle" | "running" | "done" | "error" | "cancelled";
  total: number;
  done: number;
  current: string;
  imported: number;
  skipped: number;
  errors: { name: string; error: string }[];
  error: string;
}

export interface CloudSource {
  id: number;
  course_id: number;
  url: string;
  name: string;
  share_type: "d" | "f";
  default_kind: DocKind;
  has_password: boolean;
  documents: number;
  last_synced: string | null;
  import: CloudImportStatus;
}

export interface CloudEntry {
  name: string;
  path: string;
  size: number;
  mtime: string;
  state: "new" | "imported" | "updated";
  document_id: number | null;
  kind: DocKind | null;
}
