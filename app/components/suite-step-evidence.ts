export type RecordedStep = { title: string; kind: string; screenshot?: string; evidence_exploration_id?: string };

// Keep captures attached to their execution, never infer one from a nearby step.
export function stepImageUrl(apiBase: string | undefined, step?: RecordedStep): string | null {
  const filename = step?.screenshot, owner = step?.evidence_exploration_id;
  if (!apiBase || !filename || !owner || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,255}$/.test(owner) || /[\\/?#]/.test(filename) || filename === ".."
    || [...filename + owner].some(character => character.charCodeAt(0) < 32)
    || !/\.(?:png|jpe?g|webp)$/i.test(filename)) return null;
  try {
    const base = new URL(apiBase);
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash) return null;
    return `${apiBase.replace(/\/$/, "")}/explorations/${encodeURIComponent(owner)}/screenshots/${encodeURIComponent(filename)}`;
  } catch { return null; }
}
