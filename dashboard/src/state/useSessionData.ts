import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamUrl } from "../api/client";
import type { BrokerEvent, Question, SessionView } from "../api/types";

const RETRY_MS = 1500;
const REFRESH_MS = 350;

export interface SessionData {
  events: BrokerEvent[];
  session: SessionView | null;
  questions: Question[];
  live: boolean;
  error: string | null;
  refresh: () => void;
}

export function useSessionData(id: string, token: string | null, access: boolean): SessionData {
  const [events, setEvents] = useState<BrokerEvent[]>([]);
  const [session, setSession] = useState<SessionView | null>(null);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [live, setLive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refreshTimer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    if (!access) return;
    try {
      const [view, asked] = await Promise.all([api.session(id, token), api.questions(id, token)]);
      setSession(view);
      setQuestions(asked);
      setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  }, [id, token, access]);

  const refresh = useCallback(() => {
    window.clearTimeout(refreshTimer.current);
    refreshTimer.current = window.setTimeout(load, REFRESH_MS);
  }, [load]);

  useEffect(() => {
    setEvents([]);
    setSession(null);
    setQuestions([]);
    setLive(false);
    if (!access) return;
    let closed = false;
    let source: EventSource | null = null;
    let retry: number | undefined;
    let frame: number | undefined;
    let last = 0;
    let pending: BrokerEvent[] = [];

    const flush = () => {
      frame = undefined;
      const batch = pending;
      pending = [];
      setEvents((current) => [...current, ...batch]);
      refresh();
    };

    const connect = () => {
      if (closed) return;
      source = new EventSource(streamUrl(id, token, last));
      source.onopen = () => setLive(true);
      source.onmessage = (message) => {
        const event = JSON.parse(message.data) as BrokerEvent;
        if (event.seq <= last) return;
        last = event.seq;
        pending.push(event);
        frame ??= window.requestAnimationFrame(flush);
      };
      source.onerror = () => {
        setLive(false);
        source?.close();
        retry = window.setTimeout(connect, RETRY_MS);
      };
    };

    api
      .events(id, token)
      .then((history) => {
        if (closed) return;
        last = history.at(-1)?.seq ?? 0;
        setEvents(history);
        connect();
      })
      .catch((failure: unknown) => {
        if (closed) return;
        setError(failure instanceof Error ? failure.message : String(failure));
        retry = window.setTimeout(connect, RETRY_MS);
      });
    load();

    return () => {
      closed = true;
      source?.close();
      window.clearTimeout(retry);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [id, token, access, load, refresh]);

  useEffect(() => () => window.clearTimeout(refreshTimer.current), []);

  return { events, session, questions, live, error, refresh };
}
