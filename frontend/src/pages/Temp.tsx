// 暫存檔頁（#temp，SPEC D33，後端 tempfiles.py）：掃你的暫存資料夾與 Windows 暫存資料夾裡放超過 1 天的檔，
// 可以刪的一次刪掉；被鎖住的列出是哪個程式，可以關掉那個程式（features/processes 的同一套關閉流程）或排到下次開機刪除。
// 按「掃描」才開始（不自動掃）；每個會動到東西的按鈕都先在原地確認。
import { FolderLock, RefreshCw, ShieldAlert, Trash2 } from "lucide-react";
import { useState } from "react";
import { CloseButton, ClosePanel, dangerButton, plainButton, useCloseProcess } from "../features/processes/CloseProcess";
import { api, errorMessage } from "../lib/api";
import { bytes, clockTime } from "../lib/format";
import type { TempCleanResult, TempGroup, TempScan } from "../types";

const count = new Intl.NumberFormat("zh-TW");

function LockedGroup({ group, scanId, admin, onChanged }: {
  group: TempGroup;
  scanId: string;
  admin: boolean;
  onChanged: () => void;
}) {
  const flow = useCloseProcess(group.process, onChanged); // 關掉之後重新掃描，那些檔就能刪了
  const [boot, setBoot] = useState<"idle" | "confirm" | "working">("idle");
  const [message, setMessage] = useState<{ text: string; tone: "ok" | "warn" } | null>(null);

  const schedule = async () => {
    setBoot("working");
    try {
      const r = await api.tempBootDelete(scanId, group.key);
      setMessage({ text: `已排 ${r.scheduled} 個檔，下次開機時刪除${r.skipped ? `（${r.skipped} 個在 Windows 暫存資料夾，不能排）` : ""}`, tone: "ok" });
    } catch (e) {
      setMessage({ text: errorMessage(e), tone: "warn" });
    }
    setBoot("idle");
  };

  return (
    <li className="py-3 border-t border-line first:border-t-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="min-w-0 text-sm font-medium truncate" title={group.name}>{group.name}</span>
        <span className="text-xs text-fg-subtle num">{count.format(group.files)} 個檔・{bytes(group.bytes)}</span>
        <span className="ml-auto flex flex-wrap gap-2">
          {group.process ? <CloseButton flow={flow} label={group.name} text="關掉這個程式" /> : null}
          {group.bootDelete && boot !== "confirm" ? (
            <button type="button" className={plainButton} disabled={!admin || boot === "working"}
              title={admin ? "Windows 下次開機時刪除（不用關程式）" : "要用系統管理員執行 HWGlance"}
              onClick={() => {
                setBoot("confirm");
                setMessage(null);
              }}>
              下次開機刪除
            </button>
          ) : null}
        </span>
      </div>
      <p className="m-0 mt-1 text-xs text-fg-subtle break-all">
        {group.samples.join("、")}{group.files > group.samples.length ? ` 等 ${count.format(group.files)} 個` : ""}
      </p>
      {group.protected ? <p className="m-0 mt-1 text-xs text-fg-subtle">不能關：{group.protected}</p> : null}
      {!admin && group.bootDelete ? <p className="m-0 mt-1 text-xs text-fg-subtle">下次開機刪除要用系統管理員執行 HWGlance。</p> : null}
      <ClosePanel flow={flow} />
      {boot === "confirm" ? (
        <div role="alertdialog" aria-label="確認下次開機刪除" className="mt-2 rounded-lg border border-line bg-surface px-3 py-2 text-xs space-y-1.5">
          <p className="m-0 text-fg-muted">
            這 {count.format(group.files)} 個檔會在下次開機時刪除（不經過資源回收筒）；只排你自己的暫存資料夾裡的檔。
          </p>
          <div className="flex gap-2 pt-0.5">
            <button type="button" className={dangerButton} onClick={() => void schedule()}>排到下次開機刪除</button>
            <button type="button" className={plainButton} onClick={() => setBoot("idle")}>取消</button>
          </div>
        </div>
      ) : null}
      {message ? <p role="status" className={`m-0 mt-2 text-xs ${message.tone === "ok" ? "text-ok" : "text-warm"}`}>{message.text}</p> : null}
    </li>
  );
}

export function Temp() {
  const [scan, setScan] = useState<TempScan | null>(null);
  const [busy, setBusy] = useState<"scan" | "clean" | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [cleaned, setCleaned] = useState<TempCleanResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const doScan = async () => {
    setBusy("scan");
    setError(null);
    setConfirm(false);
    try {
      setScan(await api.tempScan());
    } catch (e) {
      setError(errorMessage(e));
    }
    setBusy(null);
  };

  const clean = async () => {
    if (!scan) return;
    setBusy("clean");
    setConfirm(false);
    setError(null);
    try {
      setCleaned(await api.tempClean(scan.id));
      setScan(await api.tempScan()); // 刪完重新掃描，看剩下什麼
    } catch (e) {
      setError(errorMessage(e));
    }
    setBusy(null);
  };

  return (
    <main className="flex-1 px-5 py-4 space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="min-w-0 flex-[1_1_18rem]">
          <h2 className="m-0 text-sm font-semibold text-fg-muted">暫存檔</h2>
          <p className="m-0 text-xs text-fg-subtle">
            掃你的暫存資料夾與 Windows 暫存資料夾，只處理放超過 1 天的檔。刪不掉的會列出是哪個程式鎖住，可以關掉那個程式，或排到下次開機刪除。
          </p>
        </div>
        <button type="button" onClick={() => void doScan()} disabled={busy != null}
          className={`${plainButton} inline-flex items-center gap-1.5 px-3 py-1`}>
          <RefreshCw className={`size-3.5 ${busy === "scan" ? "animate-spin" : ""}`} aria-hidden />
          {busy === "scan" ? "掃描中…" : scan ? "重新掃描" : "掃描"}
        </button>
      </div>

      {error ? <p role="alert" className="m-0 text-sm text-warm">{error}</p> : null}
      {cleaned ? (
        <p role="status" className="m-0 text-sm text-ok">
          刪掉 {count.format(cleaned.deleted)} 個檔（{bytes(cleaned.bytes)}）{cleaned.dirs ? `、${count.format(cleaned.dirs)} 個空資料夾` : ""}
          {cleaned.failed ? `；${count.format(cleaned.failed)} 個刪不掉（剛好被開了或沒權限）` : ""}
        </p>
      ) : null}

      {scan == null ? (
        <p className="m-0 py-10 text-sm text-fg-muted text-center">{busy === "scan" ? "掃描中…（檔案多的話要十幾秒）" : "按「掃描」開始，不會自動刪任何東西。"}</p>
      ) : (
        <>
          <section className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(16rem,1fr))]">
            {scan.roots.map((r) => (
              <div key={r.path} className="rounded-2xl border border-line bg-surface p-4 min-w-0">
                <h3 className="m-0 text-sm font-semibold">{r.label}</h3>
                <p className="m-0 mt-0.5 text-xs text-fg-subtle break-all">{r.path}</p>
                {r.readable ? (
                  <p className="m-0 mt-2 text-sm num">
                    {count.format(r.files)} 個檔・{bytes(r.bytes)}
                    <span className="block text-xs text-fg-muted">放超過 1 天：{count.format(r.old)} 個・{bytes(r.oldBytes)}</span>
                  </p>
                ) : (
                  <p className="m-0 mt-2 text-xs text-warm">打不開（沒權限或不存在）</p>
                )}
              </div>
            ))}
          </section>

          <section className="rounded-2xl border border-line bg-surface p-4">
            <h3 className="m-0 flex items-center gap-2 text-sm font-semibold">
              <Trash2 className="size-4 text-fg-muted" aria-hidden /> 可以刪的
              <span className="ml-auto text-xs font-normal text-fg-subtle num">
                {count.format(scan.deletable.files)} 個・{bytes(scan.deletable.bytes)}
              </span>
            </h3>
            {scan.deletable.files === 0 ? (
              <p className="m-0 mt-2 text-xs text-fg-subtle">沒有放超過 1 天、又沒被鎖住的檔。</p>
            ) : confirm ? (
              <div role="alertdialog" aria-label="確認刪除" className="mt-2 rounded-lg border border-line bg-surface-2 px-3 py-2 text-xs space-y-1.5">
                <p className="m-0 text-fg-muted">
                  會永久刪除這 {count.format(scan.deletable.files)} 個放超過 1 天的暫存檔（不經過資源回收筒），刪完把空了的舊資料夾也刪掉。
                </p>
                <div className="flex gap-2 pt-0.5">
                  <button type="button" className={dangerButton} onClick={() => void clean()}>刪除</button>
                  <button type="button" className={plainButton} onClick={() => setConfirm(false)}>取消</button>
                </div>
              </div>
            ) : (
              <button type="button" className={`${plainButton} mt-2`} disabled={busy != null}
                onClick={() => {
                  setConfirm(true);
                  setCleaned(null);
                }}>
                {busy === "clean" ? "刪除中…" : `刪除這 ${count.format(scan.deletable.files)} 個`}
              </button>
            )}
          </section>

          <section className="rounded-2xl border border-line bg-surface p-4">
            <h3 className="m-0 flex items-center gap-2 text-sm font-semibold">
              <FolderLock className="size-4 text-warm" aria-hidden /> 被鎖住的
              <span className="ml-auto text-xs font-normal text-fg-subtle num">
                {count.format(scan.locked.reduce((n, g) => n + g.files, 0))} 個
              </span>
            </h3>
            {scan.locked.length === 0 ? (
              <p className="m-0 mt-2 text-xs text-fg-subtle">沒有被鎖住的舊檔。</p>
            ) : (
              <>
                <p className="m-0 mt-1 text-xs text-fg-subtle">
                  程式還開著這些檔。關掉那個程式後重新掃描就能刪；不想關的話可以排到下次開機刪除。
                </p>
                <ul className="m-0 mt-1 p-0 list-none">
                  {scan.locked.map((g) => (
                    <LockedGroup key={g.key} group={g} scanId={scan.id} admin={scan.admin} onChanged={() => void doScan()} />
                  ))}
                </ul>
              </>
            )}
          </section>

          <p className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-subtle">
            {scan.denied.files ? (
              <span className="inline-flex items-center gap-1">
                <ShieldAlert className="size-3.5" aria-hidden />
                {count.format(scan.denied.files)} 個沒權限（{bytes(scan.denied.bytes)}，多半是系統或別的帳號的），略過
              </span>
            ) : null}
            {scan.truncated ? <span className="text-warm">檔案太多，只掃了一部分</span> : null}
            <span className="ml-auto num">掃描於 {clockTime(scan.time)}・{scan.took} 秒</span>
          </p>
        </>
      )}
    </main>
  );
}
