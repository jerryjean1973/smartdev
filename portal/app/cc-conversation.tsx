'use client';

import {useEffect, useRef, useState, type ClipboardEvent, type FormEvent} from 'react';
import {X} from 'lucide-react';

type Turn = {
  id: string;
  prompt: string;
  reply: string | null;
  status: 'queued' | 'running' | 'completed' | 'failed';
  created_at: string;
  image_count?: number;
};

type Conversation = {
  turns: Turn[];
  memory: {note: string; source: 'owner' | 'codex'; created_at: string}[];
};

export default function CcConversation({taskId, title, onClose}: {
  taskId?: string;
  title: string;
  onClose: () => void;
}) {
  const query = taskId ? '?task_id=' + encodeURIComponent(taskId) : '';
  const [conversation, setConversation] = useState<Conversation>();
  const [prompt, setPrompt] = useState('');
  const [images, setImages] = useState<{name: string; url: string; bytes: number}[]>([]);
  const [readingImages, setReadingImages] = useState(false);
  const [note, setNote] = useState('');
  const [sending, setSending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [error, setError] = useState('');
  const [update, setUpdate] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const feed = useRef<HTMLDivElement>(null);
  const request = useRef<{id: string; prompt: string; images: string[]} | null>(null);

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const response = await fetch('/api/cc-codex' + query, {
          cache: 'no-store', signal: controller.signal,
        });
        if (!response.ok) throw Error();
        const result: Conversation = await response.json();
        if (!controller.signal.aborted) {
          setConversation(result);
          setLoadError('');
        }
      } catch {
        if (!controller.signal.aborted) setLoadError('The host conversation is unavailable.');
      }
      if (!controller.signal.aborted) timer = setTimeout(refresh, 2000);
    }
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [query, update]);

  const lastTurn = conversation?.turns.at(-1);
  const lastTurnId = lastTurn?.id;
  const lastTurnStatus = lastTurn?.status;
  useEffect(() => {
    if (feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [lastTurnId, lastTurnStatus]);

  async function post(endpoint: string, body: object) {
    const response = await fetch('/api/cc-codex' + endpoint + query, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : 'Request failed.');
    setUpdate(key => key + 1);
  }

  async function attachImages(event: ClipboardEvent<HTMLTextAreaElement>) {
    const items = Array.from(event.clipboardData.items)
      .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
      .map(item => item.getAsFile()).filter((file): file is File => file !== null);
    const files = items.length ? items : Array.from(event.clipboardData.files)
      .filter(file => file.type.startsWith('image/'));
    if (!files.length) return;
    event.preventDefault();
    if (readingImages || sending) return;
    if (files.some(file => !/^image\/(png|jpeg|webp)$/.test(file.type))) {
      setError('Paste a PNG, JPEG, or WebP image.');
      return;
    }
    if (files.length + images.length > 4 || files.some(file => file.size > 5_000_000) ||
        files.reduce((total, file) => total + file.size, 0) +
          images.reduce((total, image) => total + image.bytes, 0) > 10_000_000) {
      setError('Attach up to four images, 5 MB each and 10 MB total.');
      return;
    }
    setError('');
    setReadingImages(true);
    try {
      const attachments = await Promise.all(files.map(file => new Promise<{name: string; url: string; bytes: number}>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve({name: file.name || 'Pasted image', url: String(reader.result), bytes: file.size});
        reader.onerror = reject;
        reader.readAsDataURL(file);
      })));
      setImages(existing => [...existing, ...attachments]);
    } catch {
      setError('The pasted image could not be read.');
    } finally {
      setReadingImages(false);
    }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (sending || readingImages || (!prompt.trim() && !images.length)) return;
    const text = prompt.trim() || 'Review the attached image.';
    const payload = images.map(image => image.url.split(',', 2)[1]);
    const prior = request.current;
    const message = prior?.prompt === text && prior.images.length === payload.length &&
      payload.every((image, index) => image === prior.images[index])
      ? prior : {id: crypto.randomUUID(), prompt: text, images: payload};
    request.current = message;
    setSending(true);
    setError('');
    try {
      await post('', message);
      setPrompt(current => current.trim() === prompt.trim() ? '' : current);
      setImages([]);
      request.current = null;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Message could not be sent.');
    } finally {
      setSending(false);
    }
  }

  async function remember(event: FormEvent) {
    event.preventDefault();
    const text = note.trim();
    if (!text || saving) return;
    setSaving(true);
    setError('');
    try {
      await post('/memory', {note: text});
      setNote(current => current.trim() === text ? '' : current);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Memory could not be saved.');
    } finally {
      setSaving(false);
    }
  }

  return <dialog ref={dialog} className="wb-cc-dialog" aria-labelledby="cc-chat-title"
    onCancel={event => {event.preventDefault();onClose();}}>
    <div className="wb-cc-header">
      <div><h2 id="cc-chat-title">{taskId ? 'Ask CC · Task conversation' : 'Ask CC · Main'}</h2>
        {taskId && <p>{title}</p>}</div>
      <button type="button" aria-label="Close CC conversation" onClick={onClose}><X size={18} /></button>
    </div>
    <div className="wb-cc-feed" ref={feed} role="log" aria-label="Conversation">
      {!conversation && !loadError && <p role="status">Connecting to Command Center…</p>}
      {conversation && !conversation.turns.length && <p>Send a message to Codex on the Command Center host.</p>}
      {conversation?.turns.map(turn => <div className="wb-cc-turn" key={turn.id}>
        <div className="wb-cc-message" data-speaker="owner"><strong>You</strong><p>{turn.prompt}</p>
          {!!turn.image_count && <small>{turn.image_count} image{turn.image_count === 1 ? '' : 's'} attached</small>}
        </div>
        <div className="wb-cc-message" data-speaker="codex"><strong>Codex</strong>
          <p>{turn.reply ?? (turn.status === 'queued' ? 'Queued' : 'Working…')}</p>
        </div>
      </div>)}
    </div>
    {(error || loadError) && <p className="wb-cc-error" role="alert">{error || loadError}</p>}
    <details className="wb-cc-memory">
      <summary>Memory bank ({conversation?.memory.length ?? 0})</summary>
      <ul>{conversation?.memory.map(item => <li key={item.note}>
        <strong>{item.source === 'owner' ? 'Owner note' : 'Codex note'}</strong> · {item.note}
      </li>)}</ul>
      <form onSubmit={remember}>
        <label>Memory note <input value={note} onChange={event => setNote(event.target.value)} maxLength={2000} /></label>
        <button type="submit" disabled={saving || !note.trim() || !conversation}>Save memory</button>
      </form>
    </details>
    <form className="wb-cc-compose" onSubmit={send}>
      <label htmlFor="cc-chat-message">Message Codex</label>
      <textarea id="cc-chat-message" value={prompt} onChange={event => setPrompt(event.target.value)}
        rows={3} maxLength={12000} onPaste={event => void attachImages(event)} />
      {!!images.length && <ul className="wb-cc-images" aria-label="Attached images">
        {images.map((image, index) => <li key={index}>
          <img src={image.url} alt={image.name} />
          <button type="button" disabled={sending || readingImages} aria-label={`Remove image ${index + 1}`}
            onClick={() => setImages(existing => existing.filter((_, position) => position !== index))}>Remove</button>
        </li>)}
      </ul>}
      <button type="submit" disabled={sending || readingImages || (!prompt.trim() && !images.length) || !conversation}>{sending ? 'Sending…' : 'Send'}</button>
    </form>
  </dialog>;
}
