"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { Database, Image, Route } from "lucide-react";
import type { StepKind } from "./suite-capture-plan";

export type StepDragPreview = { kind: StepKind; title: string; size: number; x: number; y: number; caseId: string | null; movingId?: string };

export function stepDragPosition(x: number, y: number, size: number, width: number, height: number) {
  return { left: Math.max(8, Math.min(x + 16, width - size - 8)), top: Math.max(8, Math.min(y - size * .35, height - size - 8)) };
}

// A DOM-only pointer update keeps the full-size card responsive without
// rerendering the entire editor on every mouse movement. It never edits a plan.
export default function StepDragOverlay({ preview, onCancel, hidden = false }: { preview: StepDragPreview; onCancel: () => void; hidden?: boolean }) {
  const card = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const move = (event: DragEvent) => {
      if (!card.current) return;
      const position = stepDragPosition(event.clientX, event.clientY, preview.size, window.innerWidth, window.innerHeight);
      card.current.style.left = position.left + "px";
      card.current.style.top = position.top + "px";
    };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") onCancel(); };
    document.addEventListener("dragover", move, true);
    document.addEventListener("dragend", onCancel, true);
    window.addEventListener("keydown", key);
    window.addEventListener("blur", onCancel);
    return () => { document.removeEventListener("dragover", move, true); document.removeEventListener("dragend", onCancel, true); window.removeEventListener("keydown", key); window.removeEventListener("blur", onCancel); };
  }, [preview.size, onCancel]);
  if (typeof document === "undefined") return null;
  const Icon = preview.kind === "capture" ? Image : preview.kind === "sql" ? Database : Route;
  const position = stepDragPosition(preview.x, preview.y, preview.size, window.innerWidth, window.innerHeight);
  return createPortal(<div ref={card} className={`suite-step-drag-overlay kind-${preview.kind}`} data-step-drag-overlay data-compact={preview.size < 125 ? "true" : undefined} aria-hidden="true" style={{ ...position, width: preview.size, height: preview.size, visibility: hidden ? "hidden" : undefined }}><small><Icon size={16} />PRÉVIA</small><strong>{preview.title}</strong><span>{preview.kind === "capture" ? "Pausa para anexar imagem" : "Só planejamento"}</span></div>, document.body);
}
