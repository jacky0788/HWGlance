// 分段按鈕：一排選項選一個（設定的溫度單位與更新頻率、事件頁的時間範圍）。
export function Segmented<T extends string | number>({ value, options, onChange, label }: {
  value: T;
  options: { value: T; text: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg border border-line p-0.5">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}
          className={`rounded-md px-3 py-1 text-sm cursor-pointer ${o.value === value ? "bg-surface-2 text-fg font-semibold" : "text-fg-muted hover:text-fg"}`}>
          {o.text}
        </button>
      ))}
    </div>
  );
}
