"""LHM 的英文感測器名稱 → 畫面上的中文。對不到的照原文顯示，所以新硬體的感測器不會被漏掉。

名稱照 LHM v0.9.6 原始碼：Amd17Cpu.cs、IntelCpu.cs、NvidiaGpu.cs、AmdGpu.cs、SuperIOHardware.cs、StorageDevice.cs。
"""
from __future__ import annotations

import re

# (感測器種類, 名稱（整串比對）, 中文)。由上往下找，第一個對到的算數
_RULES: list[tuple[str, str, str]] = [
    ("Temperature", r"Core \(Tctl/Tdie\)", "核心 Tctl/Tdie"),
    ("Temperature", r"Core \(Tctl\)", "核心 Tctl"),
    ("Temperature", r"Core \(Tdie\)", "核心 Tdie"),
    ("Temperature", r"CCD(\d+) \(Tdie\)", r"CCD\1"),
    ("Temperature", r"CCDs Max \(Tdie\)", "CCD 最高"),
    ("Temperature", r"CCDs Average \(Tdie\)", "CCD 平均"),
    ("Temperature", r"CPU Package", "封裝"),
    ("Temperature", r"Core Max", "核心最高"),
    ("Temperature", r"Core Average", "核心平均"),
    ("Temperature", r"CPU Core #(\d+)", r"核心 \1"),
    ("Temperature", r"P-Core #(\d+)", r"效能核 \1"),
    ("Temperature", r"E-Core #(\d+)", r"節能核 \1"),
    # Mac（sensors/mac.py）：SMC 的溫度點不是一個核心一個，是晶片上那一區的量測點
    ("Temperature", r"P-Cores Max", "效能核心最高"),
    ("Temperature", r"E-Cores Max", "節能核心最高"),
    ("Temperature", r"P-Cluster #(\d+)", r"效能核心區 \1"),
    ("Temperature", r"E-Cluster #(\d+)", r"節能核心區 \1"),
    ("Temperature", r"GPU Cluster #(\d+)", r"GPU 量測點 \1"),
    ("Temperature", r"GPU Average", "平均"),
    ("Temperature", r"Battery", "電池"),
    ("Temperature", r"GPU Core", "核心"),
    ("Temperature", r"GPU Hot Spot", "熱點"),
    ("Temperature", r"GPU Memory Junction", "顯存接面"),
    ("Temperature", r"GPU Memory", "顯存"),
    ("Temperature", r"GPU VR VDDC", "核心供電"),
    ("Temperature", r"GPU VR MVDD", "顯存供電"),
    ("Temperature", r"GPU VR SoC", "SoC 供電"),
    ("Temperature", r"GPU Liquid", "水溫"),
    ("Temperature", r"GPU PLX", "橋接晶片"),
    ("Temperature", r"GPU Board", "顯示卡電路板"),
    ("Temperature", r"GPU Power Supply", "顯示卡供電"),
    ("Temperature", r"Motherboard", "主機板"),
    ("Temperature", r"System", "系統"),
    ("Temperature", r"Chipset", "晶片組"),
    ("Temperature", r"CPU", "CPU（主機板量）"),
    ("Temperature", r"CPU Socket", "CPU 插座"),
    ("Temperature", r"VRM( MOS)?", "供電"),
    ("Temperature", r"T_Sensor", "外接探頭"),
    ("Temperature", r"Liquid|Coolant", "水溫"),
    ("Temperature", r"Water In", "入水"),
    ("Temperature", r"Water Out", "出水"),
    ("Temperature", r"Composite Temperature|Temperature", "溫度"),
    ("Temperature", r"Temperature #(\d+)", r"感測器 \1"),
    ("Temperature", r"DIMM #(\d+)", r"DIMM \1"),
    ("Fan", r"CPU Fan", "CPU 風扇"),
    ("Fan", r"CPU Optional Fan", "CPU 副風扇"),
    ("Fan", r"Chassis Fan #(\d+)", r"機殼風扇 \1"),
    ("Fan", r"System Fan #(\d+)", r"系統風扇 \1"),
    ("Fan", r"Chipset Fan", "晶片組風扇"),
    ("Fan", r"AIO Pump|Pump", "水冷幫浦"),
    ("Fan", r"Fans", "風扇"),
    ("Fan", r"GPU Fan (\d+)", r"顯卡風扇 \1"),
    ("Fan", r"GPU Fan|GPU", "顯卡風扇"),
    ("Fan", r"Fan #(\d+)", r"風扇 \1"),
]
_COMPILED = [(kind, re.compile(pattern + r"\Z"), text) for kind, pattern, text in _RULES]


def label(sensor_type: str, name: str) -> str:
    for kind, rx, text in _COMPILED:
        if kind == sensor_type:
            m = rx.match(name)
            if m:
                return m.expand(text)
    return name
