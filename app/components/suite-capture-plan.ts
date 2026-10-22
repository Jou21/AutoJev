export type CaptureCheckpoint = { id: string; gap: number; order: number };
export type StepKind = "capture" | "sws" | "sql";
export type PlannedStep = CaptureCheckpoint & { kind: StepKind };
export type PlannedIntegration = CaptureCheckpoint & { kind: "sws" | "sql" };
export type StepDropPosition = { gap: number; index: number };
export type StepDropSlot = StepDropPosition & { left: number; width: number };

// Use the whole canvas, not just its small + buttons. The expanded preview
// remains its own target so opening space cannot make the destination flicker.
export function nearestStepDrop(x: number, slots: StepDropSlot[], current?: StepDropPosition | null, preview?: { left: number; width: number } | null): StepDropPosition | null {
  if (!Number.isFinite(x)) return null;
  const valid = slots.filter(slot => Number.isInteger(slot.gap) && slot.gap >= 0 && Number.isInteger(slot.index) && slot.index >= 0 && Number.isFinite(slot.left) && Number.isFinite(slot.width) && slot.width > 0);
  const previous = valid.find(slot => slot.gap === current?.gap && slot.index === current?.index);
  if (previous && preview && x >= preview.left && x <= preview.left + preview.width) return { gap: previous.gap, index: previous.index };
  let nearest = previous, distance = previous ? Math.abs(x - (previous.left + previous.width / 2)) : Infinity;
  for (const slot of valid) {
    const nextDistance = Math.abs(x - (slot.left + slot.width / 2));
    if (nextDistance < distance) { nearest = slot; distance = nextDistance; }
  }
  return nearest ? { gap: nearest.gap, index: nearest.index } : null;
}

export function plannedSteps(captures: CaptureCheckpoint[] = [], integrations: PlannedIntegration[] = []): PlannedStep[] {
  return [...captures.map(point => ({ ...point, kind: "capture" as const })), ...integrations]
    .sort((a, b) => a.gap - b.gap || a.order - b.order);
}

// Slots describe the list BEFORE removing a dragged card. Adjust same-gap moves
// so dropping immediately before/after itself is a no-op, never a duplicate.
export function placePlannedStep(points: PlannedStep[], point: PlannedStep, gap: number, index: number): PlannedStep[] {
  const siblings = points.filter(value => value.gap === gap).sort((a, b) => a.order - b.order);
  const oldIndex = siblings.findIndex(value => value.id === point.id);
  const target = Math.max(0, Math.min(index, siblings.length));
  if (oldIndex >= 0) siblings.splice(oldIndex, 1);
  siblings.splice(target - (oldIndex >= 0 && oldIndex < target ? 1 : 0), 0, { ...point, gap });
  return [...points.filter(value => value.gap !== gap && value.id !== point.id), ...siblings.map((value, order) => ({ ...value, order }))]
    .sort((a, b) => a.gap - b.gap || a.order - b.order);
}

// Insertion slots address positions between pauses in the same original gap.
// Keep checkpoint identities and original gaps intact; only normalize their order.
export function insertCaptureCheckpoint(points: CaptureCheckpoint[], id: string, gap: number, index: number): CaptureCheckpoint[] {
  const siblings = points.filter(point => point.gap === gap).sort((a, b) => a.order - b.order);
  siblings.splice(Math.max(0, Math.min(index, siblings.length)), 0, { id, gap, order: 0 });
  return [...points.filter(point => point.gap !== gap), ...siblings.map((point, order) => ({ ...point, order }))]
    .sort((a, b) => a.gap - b.gap || a.order - b.order);
}
