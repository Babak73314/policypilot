import { useCallback, useEffect, useRef, useState, type SubmitEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import type { SimulatorItem } from "../lib/api-types";
import { cx } from "../lib/cx";
import { isRecord } from "../lib/guards";
import { isApiError, request } from "../lib/http";
import { readJson, writeJson } from "../lib/storage";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useFormatters } from "../lib/useFormatters";
import { useVisibilityPolling } from "../lib/useVisibilityPolling";

/** whatsapp.md: the simulator polls once a second while visible and stops after 15 minutes idle. */
const POLL_MS = 1000;
const USER_KEY = "policypilot.simulator.user";

interface SimUser {
  userId: string;
  name: string;
}

type Line = { kind: "in"; id: string; text: string; createdAt: string } | { kind: "out"; item: SimulatorItem };

function isSimUser(value: unknown): value is SimUser {
  return isRecord(value) && typeof value.userId === "string" && typeof value.name === "string";
}

function newUser(): SimUser {
  return { userId: `sim-${crypto.randomUUID().slice(0, 8)}`, name: "Alex" };
}

export function SimulatorPage() {
  const { t } = useTranslation();
  const fmt = useFormatters();
  useDocumentTitle(t("simulator.pageTitle"));
  const [user, setUser] = useState<SimUser>(() => readJson("session", USER_KEY, isSimUser) ?? newUser());
  const [lines, setLines] = useState<Line[]>([]);
  const [text, setText] = useState("");
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const afterRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const polling = useVisibilityPolling({ enabled: !unavailable, intervalMs: POLL_MS });

  useEffect(() => {
    writeJson("session", USER_KEY, user);
  }, [user]);

  const poll = useCallback(async () => {
    try {
      const page = await request<{ items: SimulatorItem[]; nextAfter: number }>("/dev/whatsapp/outbox", {
        query: { userId: user.userId, after: afterRef.current },
      });
      if (page.items.length > 0) {
        afterRef.current = page.nextAfter;
        setLines((current) => [...current, ...page.items.map((item): Line => ({ kind: "out", item }))]);
      }
    } catch (err) {
      if (isApiError(err, 404)) setUnavailable(true);
    }
  }, [user.userId]);

  useEffect(() => {
    if (!polling.active) return;
    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, [polling.active, poll]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lines.length]);

  const simulate = async (body: { text?: string; buttonId?: string }, shown: string) => {
    setError(null);
    setLines((current) => [
      ...current,
      { kind: "in", id: crypto.randomUUID(), text: shown, createdAt: new Date().toISOString() },
    ]);
    try {
      await request("/dev/whatsapp/simulate", {
        method: "POST",
        body: { userId: user.userId, name: user.name, ...body },
      });
    } catch (err) {
      if (isApiError(err, 404)) setUnavailable(true);
      else setError(t("simulator.sendFailed"));
    }
  };

  const onSubmit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = text.trim();
    if (!value) return;
    setText("");
    void simulate({ text: value }, value);
  };

  const advanceClock = async () => {
    await request("/dev/whatsapp/advance-clock", { method: "POST", body: { userId: user.userId, hours: 25 } });
    setLines((current) => [
      ...current,
      { kind: "in", id: crypto.randomUUID(), text: t("simulator.clockAdvanced"), createdAt: new Date().toISOString() },
    ]);
  };

  const reset = () => {
    afterRef.current = 0;
    setLines([]);
    setUser(newUser());
  };

  if (unavailable) {
    return (
      <main className="mx-auto max-w-lg p-8 text-center">
        <h1 className="text-xl font-semibold">{t("simulator.unavailableTitle")}</h1>
        <p className="mt-2 text-slate-600">{t("simulator.unavailableBody")}</p>
        <Link to="/" className="btn-primary mt-4">
          {t("errors.backHome")}
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto grid max-w-5xl gap-8 p-6 md:grid-cols-[360px_1fr]">
      <section
        className="flex h-[680px] flex-col overflow-hidden rounded-[2rem] border-8 border-slate-800 bg-[#efeae2] shadow-xl"
        aria-label={t("simulator.phone")}
      >
        <div className="bg-[#075e54] px-4 py-3 text-white">
          <p className="font-semibold">{t("simulator.contactName")}</p>
          <p className="text-xs opacity-80">{t("simulator.businessAccount")}</p>
        </div>
        <ol className="flex-1 space-y-2 overflow-y-auto p-3" aria-live="polite">
          {lines.map((line) =>
            line.kind === "in" ? (
              <li key={line.id} className="ml-auto max-w-[80%] rounded-lg bg-[#d9fdd3] px-3 py-2 text-sm shadow-sm">
                {line.text}
                <span className="mt-1 block text-right text-[10px] text-slate-500">{fmt.time(line.createdAt)}</span>
              </li>
            ) : (
              <OutgoingBubble
                key={line.item.id}
                item={line.item}
                onButton={(id, title) => void simulate({ buttonId: id }, title)}
              />
            ),
          )}
          <div ref={bottomRef} />
        </ol>
        <form className="flex gap-2 bg-[#f0f2f5] p-2" onSubmit={onSubmit}>
          <label htmlFor="sim-input" className="sr-only">
            {t("simulator.inputLabel")}
          </label>
          <input
            id="sim-input"
            className="input rounded-full"
            value={text}
            placeholder={t("simulator.placeholder")}
            onChange={(event) => {
              setText(event.target.value);
            }}
          />
          <button type="submit" className="btn rounded-full bg-[#00a884] text-white" disabled={!text.trim()}>
            {t("simulator.send")}
          </button>
        </form>
      </section>

      <section className="space-y-4">
        <div>
          <h1 className="text-2xl font-semibold text-brand-900">{t("simulator.heading")}</h1>
          <p className="mt-2 text-slate-600">{t("simulator.intro")}</p>
        </div>
        <dl className="card grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 p-4 text-sm">
          <dt className="text-slate-500">{t("simulator.userId")}</dt>
          <dd className="font-mono">{user.userId}</dd>
          <dt className="text-slate-500">{t("simulator.displayName")}</dt>
          <dd>
            <input
              className="input py-1"
              value={user.name}
              maxLength={60}
              aria-label={t("simulator.displayName")}
              onChange={(event) => {
                setUser({ ...user, name: event.target.value });
              }}
            />
          </dd>
        </dl>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-secondary" onClick={() => void advanceClock()}>
            {t("simulator.advanceClock")}
          </button>
          <button type="button" className="btn-secondary" onClick={reset}>
            {t("simulator.newUser")}
          </button>
        </div>
        {polling.idle && (
          <p className="text-sm text-slate-600">
            {t("simulator.pollingPaused")}{" "}
            <button type="button" className="text-brand-700 underline" onClick={polling.resume}>
              {t("simulator.resume")}
            </button>
          </p>
        )}
        {error && (
          <p className="text-sm text-rose-700" role="alert">
            {error}
          </p>
        )}
        <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">
          <li>{t("simulator.tipSigned")}</li>
          <li>{t("simulator.tipWindow")}</li>
          <li>{t("simulator.tipStop")}</li>
        </ul>
      </section>
    </main>
  );
}

function OutgoingBubble({ item, onButton }: { item: SimulatorItem; onButton: (id: string, title: string) => void }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const payload = item.payload;
  return (
    <li className="max-w-[85%] rounded-lg bg-white px-3 py-2 text-sm whitespace-pre-line shadow-sm">
      {payload.type === "template" && (
        <span className="mb-1 block text-[11px] font-medium text-[#075e54]">
          {t("simulator.templateLabel", { name: payload.name })}
        </span>
      )}
      {payload.type === "text" && payload.text}
      {payload.type === "interactive" && payload.body}
      {payload.type === "template" && (payload.body ?? payload.params.join("\n"))}
      <span className="mt-1 block text-right text-[10px] text-slate-500">{fmt.time(item.createdAt)}</span>
      {payload.type === "interactive" && (
        <div className="mt-2 flex flex-col gap-1 border-t border-slate-100 pt-2">
          {payload.buttons.map((button) => (
            <button
              key={button.id}
              type="button"
              className={cx("rounded py-1 text-center text-[#00a884] hover:bg-slate-50")}
              onClick={() => {
                onButton(button.id, button.title);
              }}
            >
              {button.title}
            </button>
          ))}
        </div>
      )}
    </li>
  );
}
