import React, { useEffect, useRef, useState } from "react";
import { UserCircle } from "@phosphor-icons/react";
import { Icon } from "./Icon";
import { usePresence } from "./usePresence";
import { CHATGPT_USAGE_URL } from "./chatgpt-state";
import "./chatgpt.css";

// The entry screen and settings use the same controller; this view owns no OAuth session.
export default function ChatGPTConnection({ connection, openRequest = 0, refreshRequest = null, onStatus, showTrigger = true, returnFocusRef }) {
  const { status, loading, busy, error, catalog, catalogError, catalogLoading, authLink, phase,
    hasPlan, accountId, account, accounts, model, canManage, confirmed, refresh, loadStatus,
    connect, cancel, chooseModel, confirmPlan, retryModels, act } = connection;
  const [open, setOpen] = useState(false);
  const mounted = usePresence(open, 180);
  const container = useRef(null), trigger = useRef(null), firstFocus = useRef(null);
  useEffect(() => { if (openRequest > 0) setOpen(true); }, [openRequest]);
  useEffect(() => { onStatus?.(status); }, [onStatus, status]);
  useEffect(() => { if (refreshRequest) loadStatus({ quiet: true }).catch(() => {}); }, [refreshRequest, loadStatus]);
  function close(restoreFocus = true) {
    setOpen(false);
    if (restoreFocus) requestAnimationFrame(() => (trigger.current || returnFocusRef?.current)?.focus());
  }
  useEffect(() => {
    if (!open) return;
    refresh();
    const outside = (event) => { if (!container.current?.contains(event.target)) close(false); };
    const escape = (event) => { if (event.key === "Escape") { event.preventDefault(); close(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open, refresh]);
  useEffect(() => { if (open && mounted) firstFocus.current?.focus(); }, [open, mounted]);
  const connectLabel = "Continue with ChatGPT";

  return <div className={`chatgpt-controls${showTrigger ? "" : " chatgpt-controls-menu"}`} ref={container}>
    {showTrigger && <button ref={trigger} type="button" className="icon-button chatgpt-toggle" aria-label="Message writing" title="Message writing" aria-haspopup="dialog" aria-expanded={open} aria-controls="chatgpt-connection-panel" onClick={() => setOpen((value) => !value)}><UserCircle size={18} weight="regular" aria-hidden="true" /></button>}
    {mounted && <section id="chatgpt-connection-panel" className="query-panel chatgpt-panel" data-open={open} inert={!open ? true : undefined} role="dialog" aria-label="Message writing" aria-busy={Boolean(busy) || loading}>
      <div className="query-heading"><strong>Message writing</strong><button ref={firstFocus} type="button" className="icon-button" aria-label="Close message writing" onClick={() => close()}><Icon name="close" size={15} /></button></div>
      {status?.preview_only && <p className="microcopy">Connection preview. Saved field notes stay unchanged.</p>}

      {phase === "unavailable" ? <p className="chatgpt-state">{loading ? "Checking message writing…" : "Template notes still work."}</p>
        : phase === "connecting" ? <p className="chatgpt-state" role="status">Finish connecting in your browser.</p>
          : phase === "connected" ? <><p className="chatgpt-state">Using ChatGPT plan</p>{account && <p className="chatgpt-account">{account.label}</p>}</>
            : phase === "paused" ? <><p className="chatgpt-state">ChatGPT writing is paused at its usage limit. Template notes still work.</p>{account && <p className="chatgpt-account">{account.label}</p>}</>
              : phase === "plan_disabled" ? <><p className="chatgpt-state">Signed in, plan use not enabled.</p>{account && <p className="chatgpt-account">{account.label}</p>}</>
                : phase === "reauth_required" ? <><p className="chatgpt-state">Reconnect ChatGPT to write new notes. Template notes still work.</p>{account && <p className="chatgpt-account">{account.label}</p>}</>
                  : phase === "expired" ? <p className="chatgpt-state">The connection link expired. Reconnect to try again.</p>
                    : phase === "error" ? <p className="chatgpt-state">ChatGPT couldn’t connect. Template notes still work.</p>
                      : <p className="chatgpt-state">Write grower notes with your ChatGPT plan.</p>}

      {hasPlan && accountId && !confirmed.has(accountId) && <div className="chatgpt-confirmation"><p>You’re using your ChatGPT plan.</p><button type="button" onClick={confirmPlan}>Got it</button></div>}

      {accounts.length > 1 && phase !== "connecting" && <label className="chatgpt-field">Account<select value={accountId || ""} disabled={Boolean(busy) || loading} onChange={(event) => act("account", "select-account", { account_id: event.target.value })}><option value="" disabled>Choose an account</option>{accounts.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}

      {hasPlan && <div className="chatgpt-model"><label className="chatgpt-field">Model<select value={model} disabled={Boolean(busy) || loading || catalogLoading || !catalog?.models.length} onChange={chooseModel}><option value="" disabled>{catalogLoading ? "Loading models…" : "Choose a model"}</option>{catalog?.models.map((item) => <option key={item.slug} value={item.slug}>{item.display_name}</option>)}</select></label>{catalogError ? <p className="chatgpt-model-note">{catalogError} <button type="button" onClick={retryModels} disabled={Boolean(busy) || catalogLoading}>Retry</button></p> : !catalogLoading && !model && <p className="chatgpt-model-note">{catalog?.models.length ? "Choose a model for new notes." : "No models are available for this account."}</p>}</div>}

      {error && <p className="query-error" role="alert">{error}</p>}
      <div className="chatgpt-actions">
        {phase === "unavailable" ? <button type="button" className="query-cancel" disabled={loading || Boolean(busy)} onClick={refresh}>Try again</button>
          : phase === "connecting" ? <>{authLink && <a className="query-submit" href={authLink} target="_blank" rel="noopener noreferrer">Open connection</a>}<button type="button" className="query-cancel" disabled={Boolean(busy)} onClick={cancel}>{busy === "cancel" ? "Canceling…" : "Cancel"}</button></>
            : phase === "paused" ? <><a className="query-submit" href={CHATGPT_USAGE_URL} target="_blank" rel="noopener noreferrer">Manage usage</a><button type="button" className="query-cancel" disabled={Boolean(busy) || loading} onClick={() => act("resume", "resume")}>{busy === "resume" ? "Resuming…" : "Resume"}</button></>
              : phase === "connected" ? <a className="chatgpt-text-button" href={CHATGPT_USAGE_URL} target="_blank" rel="noopener noreferrer">Manage usage</a>
                : <button type="button" className="query-submit" disabled={Boolean(busy) || loading} onClick={() => connect({ account: accountId, reconsent: phase === "plan_disabled" })}>{busy === "connect" ? "Connecting…" : connectLabel}</button>}
      </div>
      {canManage && <div className="chatgpt-account-actions"><button type="button" className="chatgpt-text-button" disabled={Boolean(busy) || loading} onClick={() => connect()}>Add account</button><button type="button" className="chatgpt-text-button" disabled={Boolean(busy) || loading} onClick={() => act("disconnect", "disconnect", { account_id: accountId })}>{busy === "disconnect" ? "Disconnecting…" : "Disconnect"}</button></div>}
    </section>}
  </div>;
}
