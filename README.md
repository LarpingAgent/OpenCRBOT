# OpenCRBOT

เครื่องมือทดลอง Clash Royale บน Google Play Games on PC Developer Emulator: ADB capture, replayable sessions และ MVP วิเคราะห์ภาพจากจอพร้อมคำแนะนำเชิงรับแบบอธิบายเหตุผลได้

สถานะตอนนี้: บันทึกและตรวจ session จากหน้าต่าง Google Play Games on PC ได้ และมีผล HUD/card reader จาก labelled holdout; ค่าจากภาพยังเป็น inferred. ClashAI FV3 generalist ฝึกผ่าน Colab แล้ว; corrected RoyaleGym evaluation ชนะ seeded-random 3/3 เมื่อส่ง `my_next` จาก engine. เพิ่ม Next-card icon reader แบบ optional และมี same-match pilot ที่รู้ค่า 164/176 เฟรมที่ policy ใช้ได้ แต่ยังไม่มีผล held-out จากแมตช์ใหม่หรือ label วัดคุณภาพคำแนะนำ. มี Windows action path แบบต้องยืนยันทีละ play แล้ว แต่ยังไม่ผ่านการทดสอบในแมตช์จริง. ดูสถานะและเกณฑ์ CP1–CP7 ใน [checkpoint status](docs/CHECKPOINTS.md)

โหมด Training Camp แบบยืนยัน action ทีละรายการมีให้ทดลองบน Windows ด้วย `analyze --window ... --watch-seconds ... --weights ... --templates ... --clock-templates ... --regulation-clock --next-templates ... --calibration ... --clashai-root ... --clashai-checkpoint ... --clashai-deck ... --clashai-costs ... --track --confirm-actions`. โหมดนี้ต้องรันใน interactive terminal (TTY); จะหยุดตั้งแต่เริ่มถ้า stdin ใช้ยืนยันไม่ได้. ก่อนแต่ละ play จะแสดง preview บันทึก `confirmation_requested` ลง `actions-*.jsonl` แล้วรอวลี `PLAY TRAINING CAMP`; ต้องอ่าน Next card และ regulation clock ได้สด และไม่มีวลีนี้ก็ไม่มีคลิก. ผล confirmation/dispatch/verification จะถูกเขียนต่อใน action log; live log ยังเก็บ tracker policy และ re-association events. ถ้า click verification ไม่ชัด runner จะหยุด. โหมดนี้ต้องใช้ GPGPC calibration ที่ตรงกับขนาดหน้าต่าง และยังรอการทดสอบครบหนึ่งแมตช์เพื่อปิด CP7.

มีคำสั่ง PowerShell ที่ใส่ path ของ checkpoint/model/assets ปัจจุบันให้แล้ว; ระบุชื่อหน้าต่างเกมที่ไม่ซ้ำหรือ HWND:

```powershell
$gameWindow = Read-Host "Window title or HWND"
.\tools\run_cp7_training_camp.ps1 -Window $gameWindow
```

คำแนะนำ Next-card ใช้ template จาก CP3 capture เดิม; ถ้า reader ไม่มั่นใจจะไม่ส่ง action. ตรวจ preview ทุกครั้ง และพิมพ์คำยืนยันใน console เองก่อน action หนึ่งครั้ง.

## เป้าหมาย emulator

### Google Play Games ตัวปกติบน Windows

เปิด Clash Royale จาก launcher ของ Google แล้วระบุชื่อหน้าต่างเกม:

```powershell
python -m pip install -e ".[windows,vision]"
$gameWindow = "Clash Royale"
Start-Sleep -Seconds 3; opencrbot screenshot --window $gameWindow --out runs\latest.png
```

สลับไปยังเกมระหว่างรอ 3 วินาทีและอย่าให้มีหน้าต่างอื่นบังเกม จากนั้นใช้ `analyze --image runs/latest.png` ตามตัวอย่างด้านล่าง หรือใช้ `analyze --window $gameWindow` เพื่อจับภาพและวิเคราะห์ในคำสั่งเดียว ภาพอาจมีขอบหน้าต่าง จึงต้องปรับ calibration ให้ตรงกับภาพจริงก่อนใช้ผลในแมตช์

บันทึกต่อเนื่องจากหน้าต่าง Windows และตรวจไฟล์ที่บันทึกได้ด้วย:

```powershell
Start-Sleep -Seconds 3; opencrbot record --window $gameWindow --seconds 300 --interval-ms 500 --out runs\training-match
opencrbot verify-session runs\training-match
opencrbot replay runs\training-match
```

คำสั่ง `replay` ใช้ OpenCV จาก `.[vision]` และกด Esc เพื่อหยุดได้ `verify-session` รายงานช่วงยาวที่สุดที่ภาพเหมือนกันทุกไบต์เพื่อช่วยตรวจภาพค้าง

### การเชื่อมต่อผ่าน ADB

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

แต่ละรอบเขียน `analysis.json`, ภาพ `overlay.png` และ source screenshot ไว้ในโฟลเดอร์ย่อยของ `runs\analysis` ค่า geometry ตัวอย่างดัดแปลงจาก profile มือถือของ `clash-ai`; ต้องแก้ `configs\clashai-reference.json` ให้ตรงกับ screenshot ของ emulator และกรอกชื่อไพ่ 8 ใบใน `known_deck` ก่อนอ่านผลจริงจัง ตัวตรวจ battle screen, similarity/YOLO และ elixir ยังเป็นเพียง estimate และระบบจะบันทึกเป็น `inferred` เสมอ การวิเคราะห์ภาพครั้งเดียวและ live watch ที่ไม่ได้เปิด `--confirm-actions` จะไม่ส่ง input เข้าเกม

ใช้ `--session runs\training-match` แทน `--image` เพื่อวิเคราะห์ทุกเฟรมตามลำดับและเขียน JSONL พร้อมเลขเฟรมและเวลาจับภาพ สำหรับหน้าต่าง Windows ขนาดใกล้ 659×1077 ใช้ `--calibration configs\gpgpc-windows-training.json` เป็นจุดเริ่มต้นแล้วตรวจตำแหน่ง HUD กับภาพจริงของคุณ

เพิ่ม `--frames 12 55 140` เพื่อเลือกเฟรมสำหรับเทียบกับป้ายกำกับด้วย `evaluate-hud <analysis.jsonl> <labels.json>` หรือใช้ `--track` กับ session เต็มหรือ live watch เพื่อบันทึก `track_id` โดยติดตั้ง `.[vision,tracking]` ก่อน ใช้ `--clock-templates` กับไฟล์ตัวอย่างตัวเลข HUD และเพิ่ม `--regulation-clock` เมื่อทราบว่าภาพอยู่ในช่วงเวลาปกติทั้งหมด

เพิ่ม `--rubble-template runs/training-match-cp1/princess-tower-rubble.png` เพื่ออ่านสถานะป้อม Princess ที่พังจากภาพซากป้อมในโปรไฟล์ GPGPC ที่มี `board_bounds` หากทั้งตัวตรวจป้อมและตัวเทียบซากไม่มีหลักฐานเพียงพอ สถานะป้อมจะเป็น `unknown`

สำหรับแมตช์ทดสอบในเครื่อง ชุดไพ่ครบแปดใบและค่ากรอบภาพอยู่ใน `runs/training-match-cp1/gpgpc-eight-card-templates.npz` และ `runs/training-match-cp1/gpgpc-eight-card-calibration.json` ไฟล์เหล่านี้สร้างจากภาพที่บันทึกในเครื่องและต้องใช้กับเด็คและขนาดหน้าต่างที่ตรงกัน

weights และ templates ไม่ได้ถูกคัดลอกหรือดาวน์โหลดโดย OpenCRBOT; คำสั่งข้างบนชี้ไปยังไฟล์ใน checkout `clash-ai` ที่มีอยู่แล้ว ต้นทางระบุ MIT ใน [LICENSE](../clash-ai/LICENSE); ส่วนที่ดัดแปลงใช้ตาม [third-party notice](THIRD_PARTY_NOTICES.md) ตรวจสอบเงื่อนไขของ model/data asset เองก่อนแจกจ่ายต่อ

ถ้ามี ADB หลายชุด สามารถระบุไฟล์เองได้:

```powershell
opencrbot --adb "C:\Program Files\Google\Play Games Developer Emulator\current\emulator\adb.exe" devices
```

## นำเข้าสถานะเกมจากไฟล์ออฟไลน์

`import-state` แปลงหนึ่ง record เป็น `opencrbot.observation.v1` และส่งให้ตัวแนะนำเชิงรับเดิม ใช้คำสั่งจากโฟลเดอร์ OpenCRBOT:

```powershell
opencrbot import-state cr-mem snapshots.jsonl --line 1 --catalog ..\cr-memory-reader\deploy\cards.json --out runs\cr-mem.json
opencrbot import-state clashai board-state.json --catalog unit-vocab.json --out runs\clashai.json
```

สำหรับ `cr-mem` ใช้ record `runtime_snapshot` ของ Windows reader โดย `--line` นับจาก 1 สำหรับ JSONL สำหรับ `clashai` ให้ export `pipeline.obs_contract.BoardState` ด้วย `dataclasses.asdict` และ export `pipeline.vocab.UNIT_VOCAB` เป็น JSON array จาก checkout เดียวกัน ให้เลือกชื่อไฟล์ output ที่ยังไม่มีอยู่

ข้อมูลนำเข้าเป็นสถานะย้อนหลัง ไม่ใช้ timestamp เป็นเวลาของเฟรมสด ค่าไพ่ที่ไม่รู้จักและ elixir ฝั่งตรงข้ามจะเป็น `unknown` ส่วน HP ป้อม เอฟเฟกต์เวท และไพ่ถัดไปอยู่นอกข้อมูลที่ตัวแนะนำเชิงรับใช้ในตอนนี้ คำสั่งอ่านไฟล์เท่านั้นและไม่มีการเชื่อมอุปกรณ์

## โครงสร้าง

สำหรับทดสอบโมเดล ClashAI S1 หรือ GenModel FV3 แบบออฟไลน์ ใช้ `analyze --session` กับ `--clock-anchor FRAME ELAPSED_SECONDS` แล้วส่ง JSONL ให้ `tools/clashai_decide.py --compare-session` โดยระบุ checkout, checkpoint, ค่า elixir และ `--deck` YAML สำหรับ generalist. หากต้องการอ่านไพ่ Next ให้เพิ่ม `--next-templates` พร้อม template archive ที่ตรงกับ layout; ผลที่ไม่ผ่าน confidence threshold จะคงเป็น unknown. หากเป็น full tracked session ให้เพิ่ม `--use-tracked-history` เพื่อประมาณ history จาก track births; history นี้ไม่ใช่ event log ที่ยืนยันแล้ว. รายงานจะแยกเฟรมที่ข้อมูลไม่พอออกจากคำตัดสินใจ `wait`; inference ไม่ส่ง input เข้าเกม. Checkpoint Colab Training Camp ล่าสุดโหลดกับเด็คจริงได้ แต่ผล held-out และ CP3 comparison ยังไม่ดีพอสำหรับ live hints; IceBow checkpoint เดิมยังขาด `archers`.

ส่ง JSONL จาก `analyze --session` เข้า `tools/clashai_decide.py` พร้อม `--compare-session` เพื่อเทียบ ClashAI กับกฎเชิงรับเดิมบนเฟรมเดียวกัน รายงานแยกเฟรมที่ข้อมูลไม่พอออกจากคำตัดสินใจ `wait` ของโมเดล และบันทึก SHA-256 ของไฟล์วิเคราะห์กับ checkpoint

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

ยังไม่มี checkpoint ClashAI ที่ผ่านเกณฑ์คุณภาพหรือ multi-frame tracking ที่ผ่านเกณฑ์. มี action driver แบบยืนยันทีละ action และ post-action verification แล้ว แต่ยังไม่มี runtime evidence ว่าเล่นจบแมตช์ได้; ผล holdout ปัจจุบันจำกัดอยู่ที่ replay reconstruction และหน้าต่าง GPGPC ที่บันทึกไว้ ตรวจข้อจำกัดและลำดับถัดไปใน [roadmap](docs/ROADMAP.md) และ [checkpoint status](docs/CHECKPOINTS.md)
