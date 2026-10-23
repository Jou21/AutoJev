"use client";

/* eslint-disable @next/next/no-img-element -- Preserved screenshots are served by the configured API. */
import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { ExternalLink, Image, LoaderCircle, Minus, Plus, X } from "lucide-react";

type Props = { title: string; index: number; total: number; src: string | null; onClose: () => void };

export default function StepEvidencePreview({ title, index, total, src, onClose }: Props) {
  const [zoom, setZoom] = useState(100), [state, setState] = useState<"loading" | "ready" | "error">("loading"), [attempt, setAttempt] = useState(0);
  const [panning, setPanning] = useState(false);
  const pan = useRef<{ pointerId: number; x: number; y: number; left: number; top: number } | null>(null);
  const endPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (pan.current?.pointerId !== event.pointerId) return;
    pan.current = null; setPanning(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const startPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    // Touch keeps the browser's native panning, momentum and pinch zoom.
    const stage = event.currentTarget;
    if (!src || state !== "ready" || event.pointerType === "touch" || event.isPrimary === false || event.button !== 0
      || event.target === stage || pan.current || (stage.scrollWidth <= stage.clientWidth && stage.scrollHeight <= stage.clientHeight)) return;
    try { stage.setPointerCapture(event.pointerId); } catch { return; }
    pan.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: stage.scrollLeft, top: stage.scrollTop };
    stage.focus({ preventScroll: true });
    setPanning(true); event.preventDefault();
  };
  const movePan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = pan.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (!(event.buttons & 1)) { endPan(event); return; }
    event.currentTarget.scrollLeft = drag.left - (event.clientX - drag.x);
    event.currentTarget.scrollTop = drag.top - (event.clientY - drag.y);
    event.preventDefault();
  };
  return <section id="suite-step-image" className="suite-step-image" aria-label={`Imagem da etapa ${index + 1}: ${title}`}>
    <header><Image size={17} aria-hidden="true" /><div><strong>{title}</strong><small>Etapa {index + 1} de {total} · imagem da execução de referência</small></div>
      {src && state !== "error" && <div className="suite-image-zoom" role="group" aria-label="Zoom da imagem">
        <button type="button" className="icon-button" aria-label="Diminuir imagem" title="Diminuir imagem" disabled={zoom <= 50} onClick={() => setZoom(value => Math.max(50, value - 25))}><Minus size={16} /></button>
        <button type="button" className="suite-image-fit" title="Ajustar imagem ao espaço disponível" aria-label="Ajustar imagem ao espaço disponível" onClick={() => setZoom(100)}>{zoom}%</button>
        <button type="button" className="icon-button" aria-label="Aumentar imagem" title="Aumentar imagem" disabled={zoom >= 200} onClick={() => setZoom(value => Math.min(200, value + 25))}><Plus size={16} /></button>
      </div>}
      {src && <a className="icon-button" href={src} target="_blank" rel="noopener noreferrer" aria-label="Abrir imagem em outra aba" title="Abrir imagem em outra aba"><ExternalLink size={16} /></a>}
      <button type="button" className="icon-button" aria-label="Fechar imagem da etapa" title="Fechar imagem" onClick={onClose}><X size={17} /></button>
    </header>
    <span id="suite-image-pan-help" className="sr-only">Clique e arraste a imagem para movê-la. Você também pode usar as barras de rolagem ou as setas do teclado. Em telas sensíveis ao toque, deslize sobre a imagem.</span>
    <div className="suite-image-stage" data-pannable={!!src && state === "ready"} data-panning={panning} tabIndex={src && state === "ready" ? 0 : undefined} role="region" aria-label="Visualização da captura, com rolagem quando ampliada" aria-describedby="suite-image-pan-help" title={src && state === "ready" ? "Clique e arraste para mover a imagem" : undefined}
      onPointerDown={startPan} onPointerMove={movePan} onPointerUp={endPan} onPointerCancel={endPan} onLostPointerCapture={endPan}>
      {!src ? <div className="suite-image-placeholder" role="status"><Image size={26} /><strong>Esta etapa não tem imagem registrada.</strong><span>Selecione outra etapa para ver sua captura.</span></div>
        : state === "error" ? <div className="suite-image-placeholder" role="status"><Image size={26} /><strong>Não foi possível carregar esta imagem.</strong><span>A captura pode não estar mais disponível. Nenhuma outra imagem foi usada no lugar.</span><button type="button" className="button secondary" onClick={() => { setAttempt(value => value + 1); setState("loading"); }}>Tentar novamente</button></div>
          : <><div className="suite-image-scale" style={{ width: `${zoom}%` }}><img key={attempt} src={src + (attempt ? `?preview_retry=${attempt}` : "")} alt={`${title} · captura preservada da etapa ${index + 1}`} draggable={false} onLoad={() => setState("ready")} onError={() => setState("error")} /></div>{state === "loading" && <div className="suite-image-loading" role="status"><LoaderCircle size={20} className="spin" />Carregando imagem…</div>}</>}
    </div>
  </section>;
}
