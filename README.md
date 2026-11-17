# OpenCRBOT

เครื่องมือทดลอง Clash Royale บน Google Play Games on PC Developer Emulator: ADB capture, replayable sessions และ MVP วิเคราะห์ภาพจากจอพร้อมคำแนะนำเชิงรับแบบอธิบายเหตุผลได้

สถานะตอนนี้: มีโค้ด ADB/capture/replay, YOLO + card-template analysis และ defensive hint สำหรับภาพหนึ่งเฟรมแล้ว ยังไม่ได้ทดลอง runtime กับ emulator จริงหรือคาลิเบรตบน GPGPC และไม่มีการส่งคำสั่งแตะเกม

## เป้าหมาย emulator

- **Developer Emulator:** เป้าหมายหลัก ใช้ ADB serial `localhost:6520` ตาม[เอกสาร Google](https://developer.android.com/games/playgames/pg-emulator); ถ้าไม่ปรากฏใน `adb devices` ให้ลอง `opencrbot connect localhost:6520`
- **Production/retail client ที่เปิด ADB ไว้แล้ว:** เลือก serial ด้วย `--serial` ได้
- **Root หรือ GPGPCPatcher:** ไม่จำเป็นสำหรับคำสั่ง ADB/screenshot พื้นฐาน และโปรเจกต์จะไม่เรียก patcher หรือแก้ image ของ emulator ให้เอง ดู [บันทึกการตั้งค่า emulator](docs/emulator-setup.md)

Developer Emulator กับ consumer Google Play Games client เปิดพร้อมกันไม่ได้ ให้ปิดตัวหนึ่งก่อนเปิดอีกตัว

## ติดตั้ง

ต้องมี Python 3.11 ขึ้นไป และ ADB ที่มากับ Developer Emulator, Android SDK หรืออยู่ใน `PATH`

```powershell
cd D:\test\CRAI\OpenCRBOT
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

โปรเจกต์ค้นหา ADB ที่มากับ Google Play Games Developer Emulator ก่อน จากนั้นจึงดูใน Android SDK และ `PATH` เพื่อเลือก ADB ที่เข้ากับ emulator เป้าหมาย

Google ระบุว่า Developer Emulator ใช้ ADB ได้โดยตรง ส่วน GPGPCPatcher เป็นทางเลือกสำหรับการปรับแต่ง Production/Dev image ระดับสูงและไม่ใช่ dependency ของโปรเจกต์นี้ หากเลือกใช้ patcher ให้ทำตามคู่มือและขั้นตอน restore ของ upstream แยกต่างหาก

## คำสั่ง

```powershell
opencrbot doctor
opencrbot devices
opencrbot connect localhost:6520
opencrbot screenshot --serial localhost:6520 --out runs\latest.png
opencrbot record --serial localhost:6520 --seconds 30 --interval-ms 500
opencrbot verify-session runs\captures\session-<id>
```

### วิเคราะห์ภาพและแสดงคำแนะนำ

ติดตั้งส่วน vision ที่เป็น optional ก่อน โดยใช้ Python 3.11+:

```powershell
python -m pip install -e ".[vision]"
```

ส่ง screenshot ที่บันทึกไว้เข้า analyzer:

```powershell
opencrbot analyze --image runs\latest.png `
  --weights ..\clash-ai\models\yolo\clashai_yolo11s.pt `
  --templates ..\clash-ai\assets\card_templates.npz `
  --calibration configs\clashai-reference.json
```

หรือจับภาพจาก Developer Emulator แล้ววิเคราะห์:

```powershell
opencrbot analyze --serial localhost:6520 `
  --weights ..\clash-ai\models\yolo\clashai_yolo11s.pt `
  --templates ..\clash-ai\assets\card_templates.npz
```

แต่ละรอบเขียน `analysis.json`, ภาพ `overlay.png` และ source screenshot ไว้ในโฟลเดอร์ย่อยของ `runs\analysis` ค่า geometry ตัวอย่างดัดแปลงจาก profile มือถือของ `clash-ai`; ต้องแก้ `configs\clashai-reference.json` ให้ตรงกับ screenshot ของ emulator และกรอกชื่อไพ่ 8 ใบใน `known_deck` ก่อนอ่านผลจริงจัง ตัวตรวจ battle screen, similarity/YOLO และ elixir ยังเป็นเพียง estimate และระบบจะบันทึกเป็น `inferred` เสมอ คำแนะนำ policy มีไว้ให้คนตรวจเท่านั้นและ `executable=false` — analyzer ไม่ส่ง input ไปยังเกม

weights และ templates ไม่ได้ถูกคัดลอกหรือดาวน์โหลดโดย OpenCRBOT; คำสั่งข้างบนชี้ไปยังไฟล์ใน checkout `clash-ai` ที่มีอยู่แล้ว ต้นทางระบุ MIT ใน [LICENSE](../clash-ai/LICENSE); ส่วนที่ดัดแปลงใช้ตาม [third-party notice](THIRD_PARTY_NOTICES.md) ตรวจสอบเงื่อนไขของ model/data asset เองก่อนแจกจ่ายต่อ

ถ้ามี ADB หลายชุด สามารถระบุไฟล์เองได้:

```powershell
opencrbot --adb "C:\Program Files\Google\Play Games Developer Emulator\current\emulator\adb.exe" devices
```

## โครงสร้าง

- `src/opencrbot/adb.py` — ค้นหา ADB, list/connect devices และจับภาพหน้าจอ
- `src/opencrbot/cli.py` — คำสั่ง CLI
- `src/opencrbot/contracts.py` — observation/action contracts พร้อม source, confidence และ freshness
- `src/opencrbot/recording.py` — เก็บ screenshot เป็น session แบบมี manifest/checksum, จำกัดจำนวน frame และตรวจ session ย้อนหลัง
- `src/opencrbot/vision.py` — local YOLO detections, hand-template similarity, heuristic elixir estimate และ overlay; ทุกค่าระบุ provenance และยังเป็น `inferred`
- `src/opencrbot/policy.py` — defensive hint แบบจำกัดและอธิบายได้; ไม่มี ADB/device access
- `configs/clashai-reference.json` — geometry เริ่มต้นจาก profile ของมือถือใน upstream; ไม่ใช่ GPGPC calibration
- `docs/PROJECT_AUDIT.md` — วิเคราะห์ทั้งห้า repo และข้อดี/ข้อจำกัด
- `docs/PROJECT_HANDOFF.md` — สถานะล่าสุด, ผลตรวจ emulator/image, ผลทดสอบ และขั้นตอนทำต่อ
- `THIRD_PARTY_NOTICES.md` — attribution/license notice สำหรับส่วนที่ดัดแปลงจาก `clash-ai`
- `docs/ARCHITECTURE.md` — แบบระบบ OpenCRBOT ที่สังเคราะห์จาก audit
- `docs/ROADMAP.md` — ลำดับการพัฒนาและเงื่อนไขก่อนเปิดใช้แต่ละชั้น

ยังไม่มี GPGPC calibration/labelled evaluation, multi-frame tracking, action driver หรือ post-action verification จึงยังไม่ใช่บอทที่เล่นเกมเอง ตรวจข้อจำกัดและลำดับถัดไปใน [roadmap](docs/ROADMAP.md)
