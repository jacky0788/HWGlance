"""硬體事件記錄：讀 Windows 事件記錄裡跟硬體有關的事件（當機、顯示卡、硬碟、CPU／記憶體、程式當掉），
歸成一件件事給事件頁看。只讀，不清除、不寫入事件記錄。

    model.py      一筆原始事件（RawEvent），Windows 與示範資料共用
    reader.py     讀真的事件記錄（wevtapi.dll，ctypes）
    catalog.py    要讀哪些事件、每一種代表什麼（中文說明、藍屏代碼、建議）
    incidents.py  把同一段時間的一串事件歸成一件事，算摘要
    demo.py       示範資料：照這台電腦 2026-06～10 的真實記錄
    service.py    快取（打開事件頁時才讀，60 秒內重複用）
"""
