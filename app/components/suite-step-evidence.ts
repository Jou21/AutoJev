export type ActionPreview = {
  version: string; coordinate_space: string; action: string; target: string;
  image: { width: number; height: number };
  box: { x: number; y: number; width: number; height: number };
  selection?: { requested_label?: string };
};
export type RecordedStep = { title: string; kind: string; screenshot?: string; evidence_exploration_id?: string; action_preview?: ActionPreview; verification_html?: string };

export function targetMarker(preview: ActionPreview | undefined, width: number, height: number) {
  if (!preview || preview.version !== "action-target-v1" || preview.coordinate_space !== "screenshot-css-pixels") return null;
  const image = preview.image, box = preview.box;
  if (!image || !box || ![width, height, image.width, image.height, box.x, box.y, box.width, box.height].every(Number.isFinite)
    || width <= 0 || height <= 0 || image.width <= 0 || image.height <= 0 || box.x < 0 || box.y < 0 || box.width <= 0 || box.height <= 0
    || box.x + box.width > image.width || box.y + box.height > image.height) return null;
  const sx = width / image.width, sy = height / image.height;
  if (Math.abs(sx - sy) > 0.005 * Math.max(sx, sy)) return null;
  const labels: Record<string, string> = { click: "Clique aqui", select: "Selecionar aqui", fill: "Preencher aqui", press: "Tecla neste controle", hover: "Passar o cursor aqui" };
  if (!Object.hasOwn(labels, preview.action)) return null;
  return { style: { left: `${box.x / image.width * 100}%`, top: `${box.y / image.height * 100}%`, width: `${box.width / image.width * 100}%`, height: `${box.height / image.height * 100}%` },
    right: box.x + box.width / 2 > image.width / 2, above: box.y + box.height / 2 > image.height / 2,
    label: preview.action === "select" && preview.selection?.requested_label ? `Selecionar: ${preview.selection.requested_label}` : labels[preview.action] };
}

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
