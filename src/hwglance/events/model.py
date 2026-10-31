"""一筆原始事件：Windows 事件記錄（或示範資料）原本的樣子，還沒解讀。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RawEvent:
    log: str                                   # System／Application
    provider: str                              # 來源，例如 nvlddmkm、Microsoft-Windows-Kernel-Power
    event_id: int
    level: int                                 # 1 重大、2 錯誤、3 警告、4（或 0）資訊
    time: float                                # 記下的時間（UTC epoch 秒）
    record: int = 0                            # 記錄編號（同一個記錄裡唯一）
    fields: dict[str, str] = field(default_factory=dict)   # 有名字的資料（Kernel-Power 41 的 BugcheckCode…）
    args: tuple[str, ...] = ()                 # 全部資料照順序（舊式來源沒有名字，只能看位置）
    binary: str | None = None                  # 附加的二進位資料（十六進位字串；6008 的時間在這裡）
    message: str | None = None                 # Windows 照事件定義組好的訊息（沒有定義就沒有，例如 nvlddmkm）

    def arg(self, i: int) -> str:
        return self.args[i] if 0 <= i < len(self.args) else ""
