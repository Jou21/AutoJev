"use client";

import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type RefObject } from "react";
import { ArrowDown, ArrowUp, Bot, Check, ChevronDown, Copy, LoaderCircle, Search, Sparkles } from "lucide-react";
import "./PlanningChat.css";

export type ChatLlm = { provider_id: string; model_id: string; provider_name?: string; label?: string };
export type PlanningMessage = { role: "user" | "assistant"; content: string; llm?: ChatLlm };
type ModelOption = { id: string; provider_id: string; provider_name: string; label?: string; enabled?: boolean; input_modalities?: string[]; output_modalities?: string[] };
type Props = {
  id?: string;
  composerRef?: RefObject<HTMLTextAreaElement | null>;
  request: <T>(path: string, options?: RequestInit) => Promise<T>;
  defaultModel?: ChatLlm;
  model: ChatLlm | null;
  onModelChange: (model: ChatLlm) => void;
  messages: PlanningMessage[];
  busy: boolean;
  value: string;
  onValueChange: (value: string) => void;
  onSend: (text: string, model: ChatLlm | null) => Promise<void>;
  error: string;
  children?: ReactNode;
  purpose?: "base" | "execution";
};

const identity = (model: { provider_id: string; model_id?: string; id?: string }) => JSON.stringify([model.provider_id, model.model_id || model.id]);
const asSelection = (model: ModelOption): ChatLlm => ({ provider_id: model.provider_id, model_id: model.id, provider_name: model.provider_name, label: model.label || model.id });

function MessageContent({ content }: { content: string }) {
  // Render only inline emphasis/code. Model-provided HTML is always plain text.
  return <p>{content.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g).map((part, index) => part.startsWith("**") && part.endsWith("**") ? <strong key={index}>{part.slice(2, -2)}</strong> : part.startsWith("`") && part.endsWith("`") ? <code key={index}>{part.slice(1, -1)}</code> : part)}</p>;
}

export default function PlanningChat({ id, composerRef, request, defaultModel, model, onModelChange, messages, busy, value, onValueChange, onSend, error, children, purpose = "execution" }: Props) {
  const [models, setModels] = useState<ModelOption[]>([]), [loading, setLoading] = useState(true), [catalogError, setCatalogError] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false), [query, setQuery] = useState(""), [catalogVersion, setCatalogVersion] = useState(0);
  const [copied, setCopied] = useState<number | null>(null), [copyError, setCopyError] = useState(false), [awayFromBottom, setAwayFromBottom] = useState(false);
  const picker = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null), search = useRef<HTMLInputElement>(null);
  const transcript = useRef<HTMLDivElement>(null), localComposer = useRef<HTMLTextAreaElement>(null), following = useRef(true), copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const composer = composerRef || localComposer;
  const currentModel = models.find(option => model && identity(option) === identity(model));
  const effectiveModel = currentModel ? asSelection(currentModel) : null;

  useEffect(() => {
    let alive = true;
    request<ModelOption[]>("/models").then(options => {
      if (alive) {
        setModels(options.filter(option => option.enabled !== false && option.id && option.provider_id
          && (!option.input_modalities?.length || option.input_modalities.includes("text"))
          && (!option.output_modalities?.length || option.output_modalities.includes("text"))));
        setCatalogError(false);
      }
    }).catch(() => { if (alive) setCatalogError(true); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [request, catalogVersion]);

  useEffect(() => {
    if (!model && defaultModel) {
      const initial = models.find(option => identity(option) === identity(defaultModel));
      if (initial) onModelChange(asSelection(initial));
    }
  }, [models, model, defaultModel, onModelChange]);

  useEffect(() => {
    if (!pickerOpen) return;
    search.current?.focus();
    const closeOutside = (event: MouseEvent) => { if (event.target instanceof Node && !picker.current?.contains(event.target)) setPickerOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); setPickerOpen(false); trigger.current?.focus(); } };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", closeOutside); document.removeEventListener("keydown", escape); };
  }, [pickerOpen]);

  useEffect(() => {
    if (following.current) transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: "instant" });
  }, [messages, busy, children, error]);

  useEffect(() => {
    if (composer.current) {
      composer.current.style.height = "auto";
      composer.current.style.height = Math.min(128, composer.current.scrollHeight) + "px";
    }
  }, [value, composer]);
  useEffect(() => () => { if (copyTimer.current) clearTimeout(copyTimer.current); }, []);

  const send = () => {
    if (busy || !value.trim()) return;
    following.current = true; setAwayFromBottom(false);
    void onSend(value, effectiveModel);
  };
  const copy = async (message: PlanningMessage, index: number) => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(index); setCopyError(false);
      if (copyTimer.current) clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(null), 1800);
    } catch { setCopyError(true); }
  };
  const filtered = models.filter(option => `${option.label || option.id} ${option.id} ${option.provider_name}`.toLocaleLowerCase("pt-BR").includes(query.toLocaleLowerCase("pt-BR")));
  const providers = [...new Set(filtered.map(option => option.provider_id))];
  const navigateModels = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const buttons = Array.from(picker.current?.querySelectorAll<HTMLButtonElement>(".planning-chat-model-option") || []);
    const index = buttons.indexOf(event.currentTarget);
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  };

  const modelPicker = <div className="planning-chat-model" ref={picker}>
      <button ref={trigger} className="planning-chat-model-trigger" type="button" title={currentModel ? `${currentModel.label || currentModel.id} · ${currentModel.provider_name}` : "Escolher modelo de IA"} aria-label={`Modelo de IA do chat: ${currentModel?.label || currentModel?.id || "Escolher modelo"}`} aria-expanded={pickerOpen} aria-haspopup="dialog" disabled={busy || loading} onClick={() => { setPickerOpen(!pickerOpen); setQuery(""); }}>
        {loading && <LoaderCircle size={13} className="spin" />}<strong>{loading ? "Carregando modelos…" : currentModel?.label || currentModel?.id || "Escolher modelo"}</strong><ChevronDown size={13} />
      </button>
      {pickerOpen && <div className="planning-chat-model-menu" role="dialog" aria-label="Escolher modelo de IA">
        <label className="planning-chat-model-search"><Search size={15} /><input ref={search} aria-label="Buscar modelos de IA" placeholder="Buscar modelo ou provedor…" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === "Enter") event.preventDefault(); if (event.key === "ArrowDown") { event.preventDefault(); picker.current?.querySelector<HTMLButtonElement>(".planning-chat-model-option")?.focus(); } }} /></label>
        <div className="planning-chat-model-options">
          {providers.map(provider => <section key={provider}><h3>{filtered.find(option => option.provider_id === provider)?.provider_name}</h3>{filtered.filter(option => option.provider_id === provider).map(option => <button type="button" className="planning-chat-model-option" key={identity(option)} aria-pressed={!!currentModel && identity(option) === identity(currentModel)} onKeyDown={navigateModels} onClick={() => { onModelChange(asSelection(option)); setPickerOpen(false); trigger.current?.focus(); }}><span><strong>{option.label || option.id}</strong><small>{option.id}</small></span>{currentModel && identity(option) === identity(currentModel) && <Check size={16} />}</button>)}</section>)}
          {!filtered.length && <p>{catalogError ? "Não foi possível carregar os modelos." : models.length ? "Nenhum modelo corresponde à busca." : "Nenhum modelo de texto ativo. Configure um em Provedores de IA."}</p>}
        </div>
        <footer>A escolha vale só para esta conversa.</footer>
      </div>}
    </div>;

  return <aside id={id} className="panel suite-chat" aria-label="Chat de planejamento com IA">
    <header className="planning-chat-heading"><Sparkles size={15} /><h3>Assistente de testes</h3><span>Só planejamento</span></header>
    <div className="planning-chat-body">
      <div ref={transcript} className="suite-chat-messages" role="log" aria-label="Conversa com o copiloto" aria-live="polite" aria-busy={busy} onScroll={() => {
        const element = transcript.current;
        if (element) { following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 70; setAwayFromBottom(!following.current); }
      }}>
        {!messages.length && !busy && <div className="suite-chat-welcome"><span><Sparkles size={22} /></span><strong>{purpose === "base" ? "Vamos melhorar sua base?" : "O que vamos testar?"}</strong><p>Descreva a tela e o objetivo. Comparo com sua base e proponho os testes que faltam. Você revisa antes de adicionar.</p><div>{["Quero testar o CRUD da tela [nome]. O que já temos e o que falta?", purpose === "base" ? "Proponha testes que faltam nesta base" : "Sugira testes para executar novamente"].map(prompt => <button key={prompt} onClick={() => { onValueChange(prompt); composer.current?.focus(); }}><Sparkles size={13} /><span>{prompt}</span><ArrowUp size={14} /></button>)}</div></div>}
        {messages.map((message, index) => <article className="suite-chat-message" data-role={message.role} key={index}>
          {message.role === "assistant" && <div className="planning-chat-message-byline"><Bot size={15} /><strong>AutoJev</strong><small>{message.llm?.label || message.llm?.model_id || "Sem uso de IA"}</small></div>}
          <MessageContent content={message.content} />
          {message.role === "assistant" && <button className="planning-chat-copy" aria-label={copied === index ? "Resposta copiada" : "Copiar resposta"} title="Copiar resposta" onClick={() => void copy(message, index)}>{copied === index ? <Check size={13} /> : <Copy size={13} />}</button>}
        </article>)}
        {busy && <div className="planning-chat-thinking" role="status"><span className="planning-chat-thinking-dots"><i /><i /><i /></span><span>Organizando seus testes…</span></div>}
        {children}
        {error && <p className="planning-chat-error" role="alert">{error}</p>}
        {copyError && <p className="planning-chat-error" role="status">Não consegui copiar. Você pode selecionar o texto da resposta.</p>}
      </div>
      {awayFromBottom && <button className="planning-chat-latest" aria-label="Ir para mensagens recentes" onClick={() => { following.current = true; setAwayFromBottom(false); transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: "smooth" }); }}><ArrowDown size={16} /></button>}
    </div>
    <div className="planning-chat-bottom">
      {catalogError && <p className="planning-chat-catalog-error">Não consegui carregar os modelos. <button type="button" onClick={() => { setLoading(true); setCatalogVersion(version => version + 1); }}>Tentar novamente</button></p>}
      <form className="suite-chat-compose" onSubmit={event => { event.preventDefault(); if (!pickerOpen) send(); }}>
        <textarea ref={composer} maxLength={4000} rows={2} aria-label="Mensagem para o assistente" aria-describedby="planning-chat-keyboard" placeholder={purpose === "base" ? "Que teste falta na sua base?" : "Quais testes vamos preparar?"} value={value} onChange={event => onValueChange(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} />
        <div className="planning-chat-compose-actions">{modelPicker}<button className="planning-chat-send" type="submit" aria-label="Enviar" title="Enviar mensagem" disabled={busy || !value.trim()}>{busy ? <LoaderCircle className="spin" size={19} /> : <ArrowUp size={20} />}</button></div>
      </form>
      <p id="planning-chat-keyboard">Enter envia · Shift + Enter pula linha</p>
      <p className="planning-chat-safety">Sugestões usam tokens. Você confirma qualquer execução.</p>
    </div>
  </aside>;
}
