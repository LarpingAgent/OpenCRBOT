# OpenCRBOT — current findings and handoff

อัปเดตล่าสุด: 18 พฤศจิกายน 2026 (Asia/Bangkok)

เอกสารนี้รวมสิ่งที่ตรวจพบใน clone ทั้งห้า, สถานะโค้ด OpenCRBOT, การตรวจ ADB/Developer Emulator บนเครื่อง และการตรวจโฟลเดอร์ `D:\test\CRAI\avd\android_system` ไว้ในที่เดียว รายละเอียด source review รายโปรเจกต์อยู่ใน [PROJECT_AUDIT.md](PROJECT_AUDIT.md)

## สถานะสั้น ๆ

- OpenCRBOT มี CLI สำหรับ ADB, capture/replay, ตรวจ session และวิเคราะห์ screenshot หนึ่งเฟรมด้วย YOLO + card templates
- Policy ให้ได้เพียง defensive hint ที่อธิบายเหตุผลได้; `executable=false` และไม่มี code ส่ง tap เข้าเกม
- ติดตั้ง optional vision dependencies ใน `.venv` แล้ว; CLI help, `pip check` และ import ของ vision ผ่าน
- Google Play Games Developer Emulator มี ADB binary ที่ path มาตรฐานแล้ว แต่ตอนตรวจยังไม่มี emulator เชื่อมต่อ: `adb devices` ว่าง และ `adb connect localhost:6520` ถูกปฏิเสธ `10061`
- Google Play Games ตัวเล่นปกติเคยเปิด Clash Royale อยู่ จึงไม่ได้ปิดหรือเปิด Dev Emulator ทับ session นั้น; Google ระบุว่า consumer client และ Developer Emulator เปิดพร้อมกันไม่ได้
- ยังไม่ได้รัน screenshot จริงจาก Dev Emulator, ยังไม่ได้คาลิเบรต vision กับ GPGPC, และยังไม่ได้ทดสอบ action driver

## `avd\android_system` คืออะไร

โฟลเดอร์ที่ตรวจคือ `D:\test\CRAI\avd\android_system` พร้อมไฟล์ image ข้างเคียงใน `D:\test\CRAI\avd`:

- มีโฟลเดอร์ partition ที่แตกออกมาเป็น `system_a`, `product_a`, `vendor_a` และ `avd_data`
- ใน `D:\test\CRAI\avd` มี `_system_a.img` ขนาดประมาณ 1.15 GB, `metadata.img` และ `userdata.img` ขนาดเชิงตรรกะ 80 GB; `userdata.img` เป็น sparse file
- `system_a/system/build.prop` ระบุ `ro.product.system.model=HPE device`, device `vsoc_kiwi_x86_64`, fingerprint `google/kiwi_x86_64/vsoc_kiwi_x86_64:14/UKW1.261030.001/16786796:user/release-keys`
- ค่าระบบระบุ Android 14 / API 34, build ID `UKW1.261030.001`, build date 31 ตุลาคม 2026 และ `ro.build.version.security_patch=2024-02-05`

**ข้อสรุป:** เนื้อหา build properties ชี้ว่า image นี้สร้างมาสำหรับอุปกรณ์ HPE/`kiwi_x86_64`; อย่างไรก็ตามยังไม่มีหลักฐานเทียบ fingerprint นี้กับ emulator ที่กำลังรันหรือยืนยัน provenance ของไฟล์ทั้งชุด ไฟล์เป็น image/ไฟล์ระบบที่แตกออกมา ไม่ใช่แพ็กเกจอัปเดตทางการที่ OpenCRBOT หรือ Developer Emulator ติดตั้งได้โดยตรง

Android 14/API 34 ต่ำกว่า Android 17/API 37 ที่เอกสาร Android ปัจจุบันระบุ แต่เลข API อย่างเดียวไม่ยืนยันว่า image นี้ใช้แทน image ของ GPGPC Dev ได้ Google Play Games ใช้ image และ release track ของตัวเอง จึงไม่ควรนำ `system_a`, `vendor_a`, `product_a` หรือไฟล์ `.img` ไปทับ Developer Emulator เอง

## วิธีอัปเดต Developer Emulator

ใช้ installer จากหน้า Google Play Games on PC Developer Emulator และเลือก Stable หรือ Beta ตามที่ต้องการ Google ระบุว่ามีได้ทีละ track; หากเปลี่ยน track ให้ถอน track ปัจจุบันทั้งหมดก่อน แล้วติดตั้ง installer ของ track ใหม่ การแทน partition ด้วยไฟล์ใน `avd\android_system` ไม่ใช่วิธีอัปเดตที่เอกสารรองรับ

Developer Emulator ต้องใช้ Hyper-V และมี ADB ที่ `C:\Program Files\Google\Play Games Developer Emulator\current\emulator\adb.exe`; endpoint ปกติคือ `localhost:6520` หากไม่เห็น device ให้ปิด consumer GPGPC ก่อน แล้วเปิด Developer Emulator จาก Start Menu จากนั้นตรวจด้วย:

```powershell
cd D:\test\CRAI\OpenCRBOT
.\.venv\Scripts\opencrbot.exe --adb "C:\Program Files\Google\Play Games Developer Emulator\current\emulator\adb.exe" doctor
.\.venv\Scripts\opencrbot.exe --adb "C:\Program Files\Google\Play Games Developer Emulator\current\emulator\adb.exe" connect localhost:6520
.\.venv\Scripts\opencrbot.exe --adb "C:\Program Files\Google\Play Games Developer Emulator\current\emulator\adb.exe" devices
```

ยังไม่มีการยืนยันสถานะ Hyper-V เพราะการอ่าน feature บน Windows ต้องใช้ elevation; firmware virtualization แสดงว่าเปิดใช้ได้บน CPU Intel Core i5-14400F

## สิ่งที่ตรวจใน repo ต้นทางทั้งห้า

| โปรเจกต์ | ข้อดีที่พบ | ข้อจำกัดสำหรับ OpenCRBOT/GPGPC | แนวทางที่เลือก |
|---|---|---|---|
| `cr-memory-reader` | memory reader, build fingerprint/canaries, event sequence และแยก exact/inferred/unknown | ต้อง root และ offsets ตรง ABI/build; profile ที่คู่มืออ้างไม่มีใน clone; ไม่พบ LICENSE | ไม่คัดลอก offsets/code; ยืมแนว fail-closed, provenance และ freshness |
| `FirstLight_CR` | native offline engine, typed/versioned contracts, replay, IL/PPO และ evaluation | ผูกกับ MuMu/Android 12/root/ARM64 และ Null’s Royale build; ไม่ใช่ live GPGPC adapter | ใช้แนวคิด contracts และ offline evaluation; ไม่ย้าย engine/weights |
| `sacha9214/clash-ai` | pipeline ภาพ→YOLO/tracking→rules→tap, timestamps/logging และตรวจผลหลังเล่นไพ่ | auto-select เน้นมือถือ USB, geometry/deck เฉพาะ; README เตือนเรื่อง ToS | เป็น reference ใกล้สุด; OpenCRBOT รับ weights/templates เป็น path ภายนอกและยังไม่อ้างว่า calibrate แล้ว |
| `vegetableleaf/ClashAI` | RoyaleSim/RoyaleGym, visible/public observation, imitation/PPO และ stop gates | offline simulator, ไม่มี Android I/O; ไม่พบ LICENSE ใน clone | ใช้แนว public-only/unknown; ไม่คัดลอก code หรือ weights |
| `RoyaleFish` | offline training/collector, held-out/ghost evaluation และ league | ต้องใช้ FirstLight V4, engine/patch, CUDA; version/catalog เฉพาะ | ยืมแนว held-out evaluation; ไม่ใช้เป็น live runtime |

ดูข้อวิเคราะห์รายโปรเจกต์, licensing และรายละเอียด GPGPC เพิ่มเติมใน [PROJECT_AUDIT.md](PROJECT_AUDIT.md)

## OpenCRBOT ที่มีอยู่ตอนนี้

- `adb.py`: ค้นหา ADB, แสดง devices, connect และ capture screenshot
- `recording.py`: bounded screenshot sessions พร้อม timestamp/hash และ `verify-session`
- `contracts.py`: schema ของ public observation, evidence/provenance และ gate สำหรับ proposal
- `vision.py`: one-frame local YOLO detections, template similarity, heuristic อ่าน battle UI/elixir และ overlay
- `policy.py`: defensive hint ขนาดเล็ก ไม่ต่อ ADB และไม่ส่ง input
- `configs/clashai-reference.json`: geometry เริ่มต้นที่ดัดแปลงจาก profile มือถือ `clash-ai`; ยังไม่ใช่ GPGPC calibration และ `known_deck` ยังว่าง

ไฟล์ weights และ `card_templates.npz` ไม่ได้ถูก bundle; ส่ง path ของไฟล์ใน checkout `clash-ai` เอง ตัว matcher ควรใส่ไพ่แปดใบของ deck จริงใน `known_deck` ก่อนใช้ ค่า YOLO/template/elixir/battle UI ยังถูกทำเครื่องหมาย `inferred` และคำแนะนำอาจผิดได้

คำสั่งวิเคราะห์เมื่อ ADB พร้อม:

```powershell
.\.venv\Scripts\opencrbot.exe analyze --serial localhost:6520 `
  --weights ..\clash-ai\models\yolo\clashai_yolo11s.pt `
  --templates ..\clash-ai\assets\card_templates.npz `
  --calibration configs\clashai-reference.json
```

ผลที่คาดหวังคือ `analysis.json`, `overlay.png`, `source.png` ใน `runs\analysis`. ไม่มี action mode และไม่ควรใช้ข้อเสนอ policy เป็นคำสั่งเล่นอัตโนมัติ

## การตรวจที่ทำแล้ว

ตรวจจากเครื่องนี้เมื่อ 18 พฤศจิกายน 2026:

- `opencrbot --help` ทำงานและแสดงทุก subcommand
- `pip check` รายงาน `No broken requirements found`
- import `cv2`, `torch`, `ultralytics`, `opencrbot.vision`, `opencrbot.policy` ผ่านหลังซ่อมไฟล์ OpenCV ใน virtualenv
- ADB ที่ติดตั้งตอบ `Android Debug Bridge version 1.0.41`, `Version 33.0.3-8952118`
- `opencrbot doctor` รายงาน ADB พร้อม แต่ `No ADB devices found`
- การ connect ไป `localhost:6520` ถูกปฏิเสธ (`10061`); ยังไม่มี screenshot จริงจาก Developer Emulator
- ระหว่างนี้ไม่มีการรัน test suite, ไม่เรียก GPGPCPatcher, ไม่แก้/แฟลช image และไม่ส่ง touch เข้าเกม

## Licensing และขอบเขต

`cr-memory-reader` และ vegetableleaf `ClashAI` ไม่พบ LICENSE ใน clone; ไม่ copy code, offsets หรือ weights จากสอง repo นั้น `clash-ai` ระบุ MIT; มี attribution ใน [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) สำหรับส่วน feature extraction/geometry ที่ดัดแปลง และ assets ไม่ได้ถูก bundle

หน้า Developer Emulator ของ Google จำกัดการใช้เครื่องมือไว้กับการพัฒนา/ทดสอบเกมที่ผู้ใช้ได้รับอนุญาตให้เข้าถึงเพื่อวัตถุประสงค์ดังกล่าว ส่วน `clash-ai` README ระบุว่าการ automate เกม Clash Royale อาจละเมิดเงื่อนไขของ Supercell; OpenCRBOT รุ่นปัจจุบันจึงเป็น screen-analysis/assistive MVP ไม่มี automated gameplay

## แหล่งข้อมูลหลัก

- [Google: Download Google Play Games on PC Developer Emulator](https://developer.android.com/games/playgames/emulator)
- [Google: Developing with the Developer Emulator / ADB](https://developer.android.com/games/playgames/pg-emulator)
- [Android SDK Platform release notes](https://developer.android.com/tools/releases/platforms)
- [Android API levels](https://developer.android.com/guide/topics/manifest/uses-sdk-element)
- [cr-memory-reader](https://github.com/vegetableleaf/cr-memory-reader)
- [FirstLight_CR](https://github.com/Jaasssoooonnnnn/FirstLight_CR)
- [sacha9214/clash-ai](https://github.com/sacha9214/clash-ai)
- [vegetableleaf/ClashAI](https://github.com/vegetableleaf/ClashAI)
- [RoyaleFish](https://github.com/fabiospalla9-tech/RoyaleFish)
- [GPGPCPatcher](https://github.com/sekedus/GPGPCPatcher)
