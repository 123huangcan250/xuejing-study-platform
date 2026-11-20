"use client";
import { ReviewSession } from "@/components/flashcards/ReviewSession";

export default function ReviewPage() {
  return (
    <div className="p-6">
      <div className="mx-auto mb-5 max-w-2xl">
        <h1 className="text-xl font-bold text-slate-800">今日复习</h1>
        <p className="text-sm text-slate-500">所有课程的到期闪卡 · 空格翻面，1~4 评分。只有复习到期卡才计 XP。</p>
      </div>
      <ReviewSession />
    </div>
  );
}
