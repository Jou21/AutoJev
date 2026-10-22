export type CaptureCheckpoint = { id: string; gap: number; order: number };

// Insertion slots address positions between pauses in the same original gap.
// Keep checkpoint identities and original gaps intact; only normalize their order.
export function insertCaptureCheckpoint(points: CaptureCheckpoint[], id: string, gap: number, index: number): CaptureCheckpoint[] {
  const siblings = points.filter(point => point.gap === gap).sort((a, b) => a.order - b.order);
  siblings.splice(Math.max(0, Math.min(index, siblings.length)), 0, { id, gap, order: 0 });
  return [...points.filter(point => point.gap !== gap), ...siblings.map((point, order) => ({ ...point, order }))]
    .sort((a, b) => a.gap - b.gap || a.order - b.order);
}
