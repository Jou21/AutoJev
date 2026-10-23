type RailSize = { scrollLeft: number; scrollWidth: number; clientWidth: number };
type WheelInput = { deltaX: number; deltaY: number; deltaMode: number; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean };

// Keep native trackpad/Shift-wheel navigation and browser zoom. Translate only
// a dominant vertical wheel, and only as far as this rail can actually move.
export function horizontalRailDelta(event: WheelInput, rail: RailSize): number {
  if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return 0;
  const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rail.clientWidth : 1;
  const next = Math.max(0, Math.min(rail.scrollWidth - rail.clientWidth, rail.scrollLeft + event.deltaY * scale));
  return next - rail.scrollLeft;
}

export function bindHorizontalRail(rail: HTMLElement | null): () => void {
  if (!rail?.addEventListener) return () => {};
  const wheel = (event: WheelEvent) => {
    const delta = horizontalRailDelta(event, rail);
    if (!delta) return;
    event.preventDefault();
    rail.scrollLeft += delta;
  };
  const key = (event: KeyboardEvent) => {
    if (event.target !== rail || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    const page = Math.max(100, rail.clientWidth * .8);
    const next = event.key === "Home" ? 0 : event.key === "End" ? rail.scrollWidth
      : event.key === "ArrowRight" ? rail.scrollLeft + page : event.key === "ArrowLeft" ? rail.scrollLeft - page : null;
    if (next === null) return;
    event.preventDefault();
    rail.scrollLeft = Math.max(0, Math.min(rail.scrollWidth - rail.clientWidth, next));
  };
  rail.addEventListener("wheel", wheel, { passive: false });
  rail.addEventListener("keydown", key);
  return () => { rail.removeEventListener("wheel", wheel); rail.removeEventListener("keydown", key); };
}

// Drag the stage background or a read-only source card to browse the rail.
// Real draggable steps and insertion controls retain their own interactions.
export function bindHorizontalRailPan(rail: HTMLElement | null): () => void {
  if (!rail?.addEventListener) return () => {};
  const surface = rail.closest<HTMLElement>(".suite-step-canvas");
  if (!surface) return () => {};
  let pan: { id: number; x: number; y: number; left: number; active: boolean } | null = null;
  let suppressClick = false;
  const finish = (blockClick = false) => {
    const previous = pan;
    pan = null;
    if (previous?.active && blockClick) suppressClick = true;
    delete surface.dataset.panning;
    if (previous && surface.hasPointerCapture(previous.id)) surface.releasePointerCapture(previous.id);
  };
  const down = (event: PointerEvent) => {
    suppressClick = false;
    if (event.pointerType === "touch" || event.isPrimary === false || event.button !== 0 || event.ctrlKey || event.metaKey || event.altKey
      || rail.scrollWidth <= rail.clientWidth) return;
    const target = event.target as Element | null;
    if (!target?.closest || target.closest('a, input, textarea, select, [contenteditable]:not([contenteditable="false"]), [draggable="true"], .suite-step-image')
      || (target.closest("button") && !target.closest("button[data-original-step]"))) return;
    // Do not steal native scrollbar gestures along the rail's bottom edge.
    const bounds = rail.getBoundingClientRect();
    if (target === rail && event.clientY >= bounds.top + rail.clientHeight) return;
    finish();
    pan = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rail.scrollLeft, active: false };
  };
  const move = (event: PointerEvent) => {
    if (!pan || pan.id !== event.pointerId) return;
    if (!(event.buttons & 1)) { finish(true); return; }
    const dx = event.clientX - pan.x, dy = event.clientY - pan.y;
    if (!pan.active) {
      // A regular click (including a slightly shaky hand) still opens a card.
      if (Math.abs(dx) < 5 || Math.abs(dx) <= Math.abs(dy)) return;
      try { surface.setPointerCapture(pan.id); } catch { finish(); return; }
      pan.active = true;
      surface.dataset.panning = "true";
      rail.focus({ preventScroll: true });
    }
    rail.scrollLeft = Math.max(0, Math.min(rail.scrollWidth - rail.clientWidth, pan.left - dx));
    event.preventDefault();
  };
  const up = (event: PointerEvent) => { if (pan?.id === event.pointerId) finish(true); };
  const cancel = (event: PointerEvent) => { if (pan?.id === event.pointerId) finish(); };
  const click = (event: MouseEvent) => {
    if (!suppressClick || event.detail === 0) return;
    suppressClick = false; event.preventDefault(); event.stopPropagation();
  };
  surface.dataset.railPan = "true";
  surface.addEventListener("pointerdown", down);
  surface.addEventListener("pointermove", move, { passive: false });
  surface.addEventListener("pointerup", up);
  surface.addEventListener("pointercancel", cancel);
  surface.addEventListener("lostpointercapture", cancel);
  surface.addEventListener("click", click, true);
  return () => {
    finish(); delete surface.dataset.railPan;
    surface.removeEventListener("pointerdown", down);
    surface.removeEventListener("pointermove", move);
    surface.removeEventListener("pointerup", up);
    surface.removeEventListener("pointercancel", cancel);
    surface.removeEventListener("lostpointercapture", cancel);
    surface.removeEventListener("click", click, true);
  };
}

export function moveRailItem<T>(items: T[], index: number, direction: -1 | 1): T[] {
  const next = index + direction;
  if (index < 0 || index >= items.length || next < 0 || next >= items.length) return items;
  const result = [...items];
  [result[index], result[next]] = [result[next], result[index]];
  return result;
}

// Slots are measured before removing the dragged item, including the slot after
// the final card. Keep the objects themselves intact (recipes, pauses, sources).
export function placeRailItem<T>(items: T[], from: number, slot: number): T[] {
  if (!Number.isInteger(from) || !Number.isInteger(slot) || from < 0 || from >= items.length || slot < 0 || slot > items.length) return items;
  const target = slot - (from < slot ? 1 : 0);
  if (target === from) return items;
  const result = [...items];
  const [item] = result.splice(from, 1);
  result.splice(target, 0, item);
  return result;
}
