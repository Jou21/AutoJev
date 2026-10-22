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
