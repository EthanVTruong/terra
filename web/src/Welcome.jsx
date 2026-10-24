import React, { useEffect, useRef } from "react";
import { OpenAiLogo } from "@phosphor-icons/react";
import { CHATGPT_USAGE_URL, entryStage } from "./chatgpt-state";
import "./welcome.css";

export default function Welcome({ connection }) {
  const { sourceExpired, returnToTerra, loading, busy, error, accountId, account, accounts, hasPlan,
    catalog, catalogLoading, catalogError, model, authLink, refresh, connect, cancel,
    chooseModel, retryModels, act } = connection;
  const stage = entryStage(connection);
  const heading = useRef(null), primary = useRef(null), previous = useRef(stage);
  useEffect(() => {
    // Keep keyboard focus in the entry flow when an action replaces its controls.
    if (previous.current !== stage) {
      if (stage === "disconnected" && previous.current === "connecting") primary.current?.focus({ preventScroll: true });
      else if (!["checking", "disconnected"].includes(stage)) heading.current?.focus({ preventScroll: true });
    }
    previous.current = stage;
  }, [stage]);
  const disabled = Boolean(busy) || loading;
  const signIn = () => connect({ account: accountId, reconsent: stage === "plan_disabled" });
  const primaryLabel = busy === "connect" ? "Connecting…" : stage === "plan_disabled" ? "Enable ChatGPT plan" : ["reauth_required", "expired", "error"].includes(stage) ? "Reconnect with ChatGPT" : "Sign in with ChatGPT";
  const message = sourceExpired ? "This simulation session expired. Return to Terra to start a fresh connection." : stage === "connecting" ? "Finish signing in in the window that opened."
    : stage === "plan_disabled" ? "You’re signed in. Allow Terra to use your ChatGPT plan to continue."
      : stage === "reauth_required" ? "Your ChatGPT connection needs to be renewed."
        : stage === "expired" ? "The sign-in link expired. Please try again."
          : stage === "error" ? "ChatGPT sign-in didn’t finish. Please try again."
            : stage === "paused" ? "Your ChatGPT plan is at its usage limit. Manage usage, then resume when you’re ready."
              : stage === "cooldown" ? "ChatGPT is temporarily unavailable. You can retry when you’re ready."
                : stage === "model" ? "Choose a model from your ChatGPT plan to continue."
                  : stage === "unavailable" ? "ChatGPT sign-in couldn’t be checked. Try the connection again."
                    : "";

  return <main className="welcome-screen" aria-busy={disabled}>
    <section className="welcome-card" aria-labelledby="welcome-heading">
      <img className="welcome-logo" src={`${import.meta.env.BASE_URL}brand/terra-cube-1080.png`} alt="" />
      <h1 id="welcome-heading" ref={heading} tabIndex={-1}>Welcome to Terra</h1>
      <p className="welcome-subtitle">Use your ChatGPT plan to get started.</p>
      <div className="welcome-connection">
        {message && <p className="welcome-status" role="status">{message}</p>}
        {account && ["model", "paused", "cooldown", "plan_disabled", "reauth_required"].includes(stage) && <p className="welcome-account">{account.label}</p>}
        {accounts.length > 1 && stage !== "connecting" && <label className="welcome-field">Account<select value={accountId || ""} disabled={disabled} onChange={(event) => act("account", "select-account", { account_id: event.target.value })}><option value="" disabled>Choose an account</option>{accounts.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
        {stage === "model" && hasPlan && <div className="welcome-model">
          <label className="welcome-field">ChatGPT model<select aria-label="ChatGPT model" value={model} disabled={disabled || catalogLoading || !catalog?.models.length} onChange={chooseModel}><option value="" disabled>{catalogLoading ? "Loading models…" : "Choose a model"}</option>{catalog?.models.map((item) => <option key={item.slug} value={item.slug}>{item.display_name}</option>)}</select></label>
          {catalogError ? <p className="welcome-status">{catalogError} <button className="welcome-text-button" type="button" onClick={retryModels} disabled={disabled || catalogLoading}>Retry models</button></p> : !catalogLoading && !catalog?.models.length && <p className="welcome-status">No models are available for this account.</p>}
        </div>}
        {error && <p className="welcome-error" role="alert">{error}</p>}
        {sourceExpired ? <button className="welcome-button" type="button" disabled={disabled} onClick={returnToTerra}>{busy === "return_live" ? "Returning…" : "Return to Terra"}</button> : stage === "connecting" ? <>
          {authLink && <a className="welcome-button" href={authLink} target="_blank" rel="noopener noreferrer"><OpenAiLogo size={20} aria-hidden="true" />Open ChatGPT sign-in</a>}
          <button className="welcome-text-button" type="button" disabled={Boolean(busy)} onClick={cancel}>{busy === "cancel" ? "Canceling…" : "Cancel sign-in"}</button>
        </> : ["paused", "cooldown"].includes(stage) ? <>
          {stage === "paused" && <a className="welcome-button" href={CHATGPT_USAGE_URL} target="_blank" rel="noopener noreferrer">Manage ChatGPT usage</a>}
          <button className={stage === "cooldown" ? "welcome-button" : "welcome-text-button"} type="button" disabled={disabled} onClick={() => act("resume", "resume")}>{busy === "resume" ? "Resuming…" : stage === "cooldown" ? "Retry connection" : "Resume"}</button>
        </> : stage === "unavailable" ? <button className="welcome-button" type="button" disabled={disabled} onClick={refresh}>Try connection again</button>
          : stage !== "model" && <button ref={primary} className="welcome-button" type="button" disabled={disabled || stage === "checking"} onClick={signIn}><OpenAiLogo size={20} aria-hidden="true" />{primaryLabel}</button>}
        {stage === "checking" && <span className="welcome-checking" role="status">Checking your connection…</span>}
        {stage === "model" && <button className="welcome-text-button" type="button" disabled={disabled} onClick={() => connect()}>Use another ChatGPT account</button>}
      </div>
    </section>
  </main>;
}
