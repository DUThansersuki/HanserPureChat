"use client";

import { BrainIcon } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

type Memory = { id: string; content: string; type: string; source_kind: string; created_at: string; predicate?: string; object_value?: string };

export function MemoryManager() {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<string>();
  const [content, setContent] = useState("");
  const [structuredValue, setStructuredValue] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const { data, error: loadError, mutate } = useSWR<Memory[]>(open ? "/api/memories" : null, async (url: string) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error("无法读取记忆，请稍后重试");
    return response.json();
  });
  async function change(memoryId: string, method: "PATCH" | "DELETE") {
    setBusy(true);
    setError(undefined);
    try {
      const response = await fetch(`/api/memories/${encodeURIComponent(memoryId)}`, {
        method, headers: { "Content-Type": "application/json" },
        body: method === "PATCH" ? JSON.stringify({ content, ...(structuredValue !== undefined ? { object_value: structuredValue } : {}) }) : undefined,
      });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.cause ?? "记忆修改失败");
      }
      setEditing(undefined);
      await mutate();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "记忆修改失败");
    } finally { setBusy(false); }
  }
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button aria-label="查看和管理记忆" size="sm" variant="ghost"><BrainIcon className="size-4" /><span>记忆</span></Button>
      </SheetTrigger>
      <SheetContent className="w-[min(460px,calc(100vw-20px))]! overflow-y-auto p-5">
        <SheetHeader className="p-0 pb-5"><SheetTitle>记忆</SheetTitle><SheetDescription>查看、纠正或忘记已保存的信息。忘记后停止使用该长期记忆，原对话仍保留。</SheetDescription></SheetHeader>
        {loadError || error ? <p role="alert" className="text-sm text-destructive">{error ?? loadError?.message}</p> : null}
        {!data && !loadError ? <p role="status">正在读取…</p> : null}
        {data?.length === 0 ? <p className="text-sm text-muted-foreground">还没有保存的记忆。</p> : null}
        <ul className="space-y-3">
          {data?.map((memory) => <li key={memory.id} className="space-y-2 rounded-lg border p-3 text-sm">
            {editing === memory.id ? <>
              {memory.predicate === "answer_length" || memory.predicate === "expression_permission" ? <label className="block">修正偏好<select aria-label="修正记忆偏好" className="ml-2 rounded border bg-background p-2" value={structuredValue} onChange={(event) => setStructuredValue(event.currentTarget.value)}>
                {(memory.predicate === "answer_length" ? [["用户偏好简短回答", "简短回答"], ["用户偏好详细回答", "详细回答"]] : [["allow", "允许"], ["deny", "不允许"], ["unknown", "未指定"]]).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select></label> : memory.predicate === "preferred_address" ? <label className="block">修正称呼<input aria-label="修正记忆称呼" className="mt-2 w-full rounded border bg-background p-2" value={structuredValue} onChange={(event) => setStructuredValue(event.currentTarget.value)} /></label> : <label className="block">修正内容<textarea aria-label="修正记忆内容" className="mt-2 min-h-20 w-full rounded border bg-background p-2" value={content} onChange={(event) => setContent(event.currentTarget.value)} /></label>}
              <Button disabled={busy || !(structuredValue ?? content).trim()} onClick={() => void change(memory.id, "PATCH")} size="sm">保存</Button>
              <Button disabled={busy} onClick={() => setEditing(undefined)} size="sm" variant="ghost">取消</Button>
            </> : <>
              <p className="whitespace-pre-wrap break-words">{memory.content}</p>
              <p className="text-xs text-muted-foreground">{memory.source_kind === "human_edit" ? "你手动修正的记忆" : "来自你的对话"}</p>
              <Button disabled={busy} onClick={() => { setEditing(memory.id); setContent(memory.content); setStructuredValue(["answer_length", "expression_permission", "preferred_address"].includes(memory.predicate ?? "") ? memory.object_value ?? "" : undefined); }} size="sm" variant="secondary">纠正</Button>
              <Button disabled={busy} onClick={() => void change(memory.id, "DELETE")} size="sm" variant="ghost">忘记</Button>
            </>}
          </li>)}
        </ul>
      </SheetContent>
    </Sheet>
  );
}
