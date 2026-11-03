// 「關閉這個程式」的整套流程（SPEC D31，後端 processes.py）：按了不會馬上動，先問後端會關哪個、能不能好好關，
// 在原地確認；好好關（WM_CLOSE）沒關掉，才問要不要強制結束。顯存排行（details/RankingItem）與暫存檔頁（pages/Temp）共用。
// 用法：const flow = useCloseProcess(proc, onDone)；按鈕放 <CloseButton flow={flow} />，確認框放 <ClosePanel flow={flow} />。
import { Power } from "lucide-react";
import { useState } from "react";
import { api, errorMessage } from "../../lib/api";
import type { ClosePlan, ProcessRef } from "../../types";

type Step =
  | { step: "idle" }
  | { step: "asking" }
  | { step: "confirm"; plan: ClosePlan }
  | { step: "working" }
  | { step: "stuck"; plan: ClosePlan; why: string } // 好好關沒關掉／沒有視窗：問要不要強制結束
  | { step: "message"; text: string; tone: "ok" | "warn" };

export interface CloseFlow {
  state: Step;
  ask: () => void;
  close: (plan: ClosePlan, force: boolean) => void;
  cancel: () => void;
}

const small = "rounded-md border px-2 py-0.5 text-xs cursor-pointer disabled:opacity-50";
export const plainButton = `${small} border-line text-fg-muted hover:text-fg hover:border-line-strong`;
export const dangerButton = `${small} border-hot text-hot hover:bg-hot-soft`;

/** onDone：真的關掉了（好好關或強制結束）之後呼叫，例如重新掃描 */
export function useCloseProcess(proc: ProcessRef | undefined | null, onDone?: () => void): CloseFlow {
  const [state, setState] = useState<Step>({ step: "idle" });

  const ask = async () => {
    if (!proc) return;
    setState({ step: "asking" });
    try {
      const plan = await api.closePlan(proc);
      if (plan.blocked) setState({ step: "message", text: `不能關：${plan.blocked}`, tone: "warn" });
      else setState({ step: "confirm", plan });
    } catch (e) {
      setState({ step: "message", text: errorMessage(e), tone: "warn" });
    }
  };

  const close = async (plan: ClosePlan, force: boolean) => {
    if (!plan.target) return;
    setState({ step: "working" });
    try {
      const r = await api.closeProcess(plan.target, force);
      if (r.result === "closed" || r.result === "killed") {
        setState({ step: "message", text: r.result === "closed" ? `已關閉「${r.name}」` : `已強制結束「${r.name}」`, tone: "ok" });
        onDone?.();
      } else if (r.result === "running") {
        setState({ step: "stuck", plan, why: "還開著：它可能在問你要不要存檔，先去它的視窗看一下。" });
      } else if (r.result === "no_window") {
        setState({ step: "stuck", plan, why: "它沒有視窗，沒辦法請它自己關。" });
      } else {
        setState({ step: "message", text: "強制結束失敗（可能是權限不夠）", tone: "warn" });
      }
    } catch (e) {
      setState({ step: "message", text: errorMessage(e), tone: "warn" });
    }
  };

  return {
    state,
    ask: () => void ask(),
    close: (plan, force) => void close(plan, force),
    cancel: () => {
      setState({ step: "idle" });
    },
  };
}

export function CloseButton({ flow, label, text = "關閉" }: { flow: CloseFlow; label: string; text?: string }) {
  const busy = flow.state.step !== "idle" && flow.state.step !== "message";
  return (
    <button type="button" onClick={flow.ask} disabled={busy} title="關閉這個程式（會先確認）" aria-label={`${text} ${label}`}
      className={`${plainButton} inline-flex items-center gap-1 self-center`}>
      <Power className="size-3" aria-hidden /> {text}
    </button>
  );
}

export function ClosePanel({ flow }: { flow: CloseFlow }) {
  const s = flow.state;
  if (s.step === "asking" || s.step === "working") {
    return <p className="m-0 mt-2 text-xs text-fg-muted">{s.step === "asking" ? "查詢中…" : "關閉中…（最多等 5 秒）"}</p>;
  }
  if (s.step === "confirm" && s.plan.target) {
    const { plan } = s;
    return (
      <div role="alertdialog" aria-label="確認關閉" className="mt-2 rounded-lg border border-line bg-surface px-3 py-2 text-xs space-y-1.5">
        <p className="m-0 text-sm">要關閉「{plan.target!.name}」（PID {plan.target!.pid}）嗎？</p>
        {plan.target!.pid !== plan.pid ? (
          <p className="m-0 text-fg-muted">「{plan.name}」是它底下的子程式（PID {plan.pid}），會關掉整個程式。</p>
        ) : null}
        <p className="m-0 text-fg-muted">
          {plan.gentle
            ? "會先請它自己關：有沒存的檔案，它會問你要不要存。"
            : "它沒有視窗，沒辦法請它自己關，只能強制結束：不會存檔。"}
        </p>
        <div className="flex gap-2 pt-0.5">
          <button type="button" className={plan.gentle ? plainButton : dangerButton}
            onClick={() => flow.close(plan, !plan.gentle)}>
            {plan.gentle ? "關閉" : "強制結束"}
          </button>
          <button type="button" className={plainButton} onClick={flow.cancel}>取消</button>
        </div>
      </div>
    );
  }
  if (s.step === "stuck") {
    return (
      <div role="alertdialog" aria-label="要不要強制結束" className="mt-2 rounded-lg border border-line bg-surface px-3 py-2 text-xs space-y-1.5">
        <p className="m-0 text-fg-muted">{s.why}還是關不掉的話可以強制結束：<span className="text-hot">不會存檔</span>。</p>
        <div className="flex gap-2 pt-0.5">
          <button type="button" className={dangerButton} onClick={() => flow.close(s.plan, true)}>強制結束</button>
          <button type="button" className={plainButton} onClick={flow.cancel}>算了</button>
        </div>
      </div>
    );
  }
  if (s.step === "message") {
    return (
      <p role="status" className={`m-0 mt-2 text-xs ${s.tone === "ok" ? "text-ok" : "text-warm"}`}>
        {s.text}
        <button type="button" className="ml-2 text-fg-subtle hover:text-fg cursor-pointer" onClick={flow.cancel}>知道了</button>
      </p>
    );
  }
  return null;
}
