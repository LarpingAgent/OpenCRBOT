# OpenCRBOT — current findings and handoff

อัปเดตล่าสุด: 21 พฤศจิกายน 2026 (Asia/Bangkok)

เอกสารนี้รวมสิ่งที่ตรวจพบใน clone ทั้งห้า, สถานะโค้ด OpenCRBOT, การตรวจ ADB/Developer Emulator บนเครื่อง และการตรวจโฟลเดอร์ `D:\test\CRAI\avd\android_system` ไว้ในที่เดียว รายละเอียด source review รายโปรเจกต์อยู่ใน [PROJECT_AUDIT.md](PROJECT_AUDIT.md)

## เพิ่มหลักฐาน 20 พฤศจิกายน 2026

สถานะ ADB และ Developer Emulator ในบันทึกด้านล่างตรวจเมื่อ 18 พฤศจิกายนและยังอ้างถึง Developer Emulator เท่านั้น ต่อมาได้ทดสอบ capture ผ่านหน้าต่าง Google Play Games on PC ที่เปิด Clash Royale อยู่:

- OpenCRBOT บันทึก Training Camp ครบ 600 เฟรม ตรวจ manifest/hash ผ่าน และบันทึกภาพผล Winner ที่เฟรม 435; ผู้เล่นมีสามมงกุฎในภาพผล
- HUD holdout จากแมตช์เด็คเดิม: battle active 13/13, ไพ่รู้ค่า 35/36 และตรง 35/35 ที่รู้ค่า, tower status รู้ค่าและตรง 58/58 จาก 60 ช่อง, clock MAE 0 s, elixir MAE 0.388
- ผลยังจำกัดที่หน้าต่างและเด็คชุดนี้; unit tracking quality/identity gate, checkpoint quality gate, paired FirstLight evaluation และ runtime validation ของ action path ยังไม่ผ่าน
- ดู CP1–CP7 และหลักฐานไฟล์ใน [CHECKPOINTS.md](CHECKPOINTS.md)

หลักฐานเพิ่มเติม 21 พฤศจิกายน: Colab ฝึก ClashAI GenModel FV3 generalist บน Tesla T4 ครบ 20 epochs; เลือก epoch 12, ดาวน์โหลด checkpoint กลับมาและตรวจ SHA-256 ตรงกับ manifest. Card vocabulary ครบเด็ค GPGPC และ adapter offline โหลดได้. CP3 full tracked rerun ใช้ baseline geometry และ assets เดิม: 176 comparable frames, Next reader รู้ค่า 164 เฟรม, FV3 แนะนำ 94 plays / 82 waits; baseline เดิม 35 suggestions / 141 waits ตรงกันครบทุก 600 เฟรม. Agreement คือ 117/176; 59 plays เพิ่มจากเฟรมที่ baseline รอ. นี่เป็น same-match pilot; template และ labels มาจากแมตช์เดียวกัน และยังไม่มี label วัด action correctness. Held-out cell-tile accuracy 9.56%, card top-1 33.06% มาจาก state ที่สร้างจาก replay events จึงยังไม่ผ่าน CP3 และยังไม่เปิด live hints. รายละเอียดอยู่ใน [CHECKPOINTS.md](CHECKPOINTS.md).

ตรวจ notebook `training_camp_generalist_fv3_train.ipynb` ใน Colab ซ้ำ: output แสดง Drive mount, hash ของ dataset/code bundle ผ่าน, Tesla T4, 20 epochs, evaluation และ run manifest อยู่ใต้ `MyDrive/OpenCRBOT/runs/clashai_training_camp_generalist_fv3_colab_20261120T183911Z/`. Checkpoint SHA-256 ตรงกับสำเนา local. Runtime ปัจจุบัน disconnected; ไม่ต้องเริ่ม train ซ้ำ.

## สถานะสั้น ๆ

- OpenCRBOT มี CLI สำหรับ ADB, capture/replay, ตรวจ session และวิเคราะห์ screenshot หนึ่งเฟรมด้วย YOLO + card templates
- Policy แสดง defensive hint หรือ ClashAI FV3 recommendation ได้. มี Windows action path แบบ opt-in ที่ต้องยืนยัน `PLAY TRAINING CAMP` ทีละ play และตรวจ hand/elixir transition หลัง click; คำสั่งรวมอยู่ที่ `tools/run_cp7_training_camp.ps1`. Prompt ยืนยันมี timeout 15 วินาที; เมื่อหมดเวลาจะ log `confirmation_timeout` และหยุด runner โดยไม่คลิก. โหมด action ต้องใช้ interactive TTY และตอนนี้ตรวจเงื่อนไขนี้ก่อนเริ่ม; proposal ถูก log เป็น `confirmation_requested` ก่อนรอคำตอบ จึงยังมีหลักฐานแม้ prompt ถูกยกเลิก. เมื่อ transition ผ่าน ระบบส่ง own-play history ที่ยืนยันแล้วให้ FV3 รอบถัดไป; คู่แข่งยังไม่มี history จากภาพ. Terminal TTY และ history-encoding smoke checks ผ่าน แต่ preview/action บนหน้าต่างเกมยังไม่ได้ตรวจ. ทดลอง live สามแมตช์แล้ว แต่ทั้งหมดจบด้วย loss และไม่มี bot action: ครั้งแรกเป็นก่อนแก้ DPI coordinates; ครั้งที่สอง preview Archers; ครั้งที่สาม preview Arrows ที่ tower โดยไม่มีกลุ่ม troop. จึงยังไม่มีหลักฐานว่า bot เล่นไพ่หรือเล่นจบแมตช์ได้
- ปรับ tracker เป็น `class-stable.short-gap-reid.v2` และเปิด `--track` ใน CP7 wrapper; live log เก็บ policy, class switches และ re-association events. Offline prefixes รักษา detections/positions ตาม baseline และ giant identity sample ไม่มี ID switch แล้ว แต่ tracker ยังไม่ได้ทดสอบบนหน้าต่าง live และไม่ทำให้ CP2 ผ่าน.
- มี Colab Gen FV3 checkpoint ครบเด็คและ optional Next icon reader; reader pilot รู้ค่า 164/176 เฟรมใน capture เดิม. Holdout จากสองแมตช์ใหม่รวม 16 เฟรมอ่าน battle 16/16, hand 64/64, Next 16/16, tower states ที่รู้ค่า 92/92 (มี 4 unknown ระหว่าง tower destruction), clock MAE 0 s และ elixir MAE 0.325. sample ยังเล็กและไม่มี action-quality labels; policy metrics จาก replay reconstruction ยังต่ำ จึงยังไม่เปิด live hints. ดู `runs/cp2-two-match-hud-summary.json`.
- เพิ่ม unit holdout 9 เฟรมจาก match attempt 2: จับคู่ 17/20 bodies จาก 19 detections (precision 0.895, recall 0.850), owner ถูก 16/17. รวม 2 matches ได้ precision 0.929 / recall 0.788. ByteTrack baseline class-stable.v1 full-session 300 เฟรมส่ง ID ให้ 76.4% ของ 619 non-tower detections; OpenCRBOT เก็บ raw detections ที่ยังไม่ยืนยันและแยก ID เมื่อ class เปลี่ยน. เทียบ tracker v2 กับ baseline ใน attempt2 full session หลังยืนยัน source hash: detection records เหมือนกัน 300/300 เฟรม (1,571 unit records), coverage non-tower 473/619, unique IDs 33 เทียบ baseline 36, re-association 3 ครั้ง และไม่มี output ID ข้าม class. Goblin Hut ตรวจได้และ owner ถูก 2/2 แต่ได้ ID เพียง 1/2 เพราะห่าง 20 วินาที; Giant holdout เดิม 12 observations มี ID 11/11 detections และ owner ถูก 9/11. ขยาย visual labels ของ Giant instance เดียวใน attempt3 เป็น 18 observations: detector พบ 17/18, มี ID ครบ 17/17 detections, owner ถูก 15/17 และ 0 ID switches; source-frame hashes ผ่านการตรวจ. non-tower ID coverage ทั้ง match 93.3%. ยังมี identity labels ของ Goblin Hut เพียง 2 observations และ Giant เพียง instance เดียว จึง CP2 ยัง partial. ดู `runs/cp2-attempt2-tracker-reid-verified-session/detection-comparison.json`, `unit-evaluation-full.json`, `tracking-identity-evaluation-full.json`, `runs/cp2-attempt3-tracker-reid-verified-session/full-session-detection-comparison.json`, `runs/cp2-attempt3-tracker-reid-verified-session/unit-tracking-evaluation-full.json`, `runs/cp2-attempt3-tracker-reid-verified-session/unit-tracking-evaluation-expanded-20261121.json`, `runs/cp2-attempt3-tracker-identity/unit-labels-expanded-20261121.json`, `runs/cp2-attempt3-tracker-identity/unit-labels-expanded-source-manifest.json` และ `runs/cp2-tracker-reid-comparison.json`.
- ภาพตรวจหน้าต่างเกมล่าสุดแบบ read-only อยู่ที่ `runs/cp7-current-window-check/frame-20261121T024421Z-463015300/`; เป็นหน้าผลของแมตช์อื่น ไม่ใช่ Training Camp, `battle_active=false` เป็น inferred (confidence 0.6) และไม่มี input ถูกส่ง. การจับภาพผ่าน Windows control helper timeout สองครั้งแม้ refresh target แล้ว; ก่อนทำ CP7 ต่อ ให้ผู้ใช้เปิด Training Camp จนพร้อมเริ่มแมตช์. ยังห้ามใช้ภาพ/ผลนี้เป็นหลักฐานของบอท.
- CP5 corrected paired RoyaleGym evaluation ทำซ้ำ byte-identical: Gen FV3 ได้ `my_next` จาก engine, ชนะ seeded-random 3/3 ด้วย 103 accepted plays; defensive baseline แพ้ 3/3. รายงานเก่าที่ Gen FV3 รอ 977 decisions ใช้ `my_next=-1` จึงถูกแทนที่. ผลนี้ยังไม่ใช่ paired test กับ FirstLight หรือ GPGPC จริง
- ตรวจ `.pt` ทั้ง workspace: FirstLight 5 รุ่นตรง contract/weights กับ V4 source ปัจจุบัน (General เคย smoke แล้ว); RoyaleFish 5 รุ่นมี V4 schema แต่ strict contract/config/catalog ไม่ตรง; YOLO/placement `.pt` เป็น perception models. ไม่มีไฟล์เหล่านี้ที่ใช้แทน ClashAI Gen FV3 ได้. Colab FV3 checkpoint ไม่มี optimizer state และ trainer ปัจจุบันยังไม่มี general warm-start option.
- FirstLight official full-match evaluator มีอยู่ แต่เครื่องยังขาด `.env`, virtualenv, offline APK และ ADB device; CP4 จึงยังเป็น partial. ไม่ใช้ผลจากการ bridge observation ข้าม engine จนกว่า state/action mapping จะผ่านการยืนยัน
- ติดตั้ง optional vision dependencies ใน `.venv` แล้ว; CLI help, `pip check` และ import ของ vision ผ่าน
- Google Play Games Developer Emulator มี ADB binary ที่ path มาตรฐานแล้ว แต่ตอนตรวจยังไม่มี emulator เชื่อมต่อ: `adb devices` ว่าง และ `adb connect localhost:6520` ถูกปฏิเสธ `10061`
- Google Play Games ตัวเล่นปกติเคยเปิด Clash Royale อยู่ จึงไม่ได้ปิดหรือเปิด Dev Emulator ทับ session นั้น; Google ระบุว่า consumer client และ Developer Emulator เปิดพร้อมกันไม่ได้
- ยังไม่ได้จับ screenshot จาก Developer Emulator ผ่าน ADB; แต่ Windows window capture ของ GPGPC มี calibration/labelled holdout บางส่วนแล้ว และยังไม่ได้ทดสอบ action driver

ต่อ Colab ผ่านบัญชี Google ที่ลงชื่อเข้าใช้แล้วและเปิดโฟลเดอร์ผล Training Camp generalist ได้. ดาวน์โหลด checkpoint อีกครั้งจาก Drive เพื่อตรวจ provenance; SHA-256 ตรงกับไฟล์ local `8fae9a846b1ab57432230c436ffa7a9e7ad17ce9e7a4185cfe92d48f4c689a95` ทุก byte. Colab runtime แสดง disconnected due to inactivity; ผลการ train และ evaluation บันทึกใน Drive ครบ จึงไม่ต้องเริ่ม training ซ้ำ.

หลักฐาน CP7 live: รอบแรกจบด้วยผลแพ้ Trainer Jonas; รอบสองบันทึก 300 เฟรมและภาพ `WINNER! Trainer Jonas` 3–0, SHA-256 `86e2a5c033fc191b9516ba1f65cd9490c0b7a693e02071721e6ac44b022302f9`. รอบสามบันทึกครบ 360 เฟรมและ `verify-session` ผ่าน; ภาพ `frame-000243.png` แสดง Trainer Jonas ชนะ 3–0, SHA-256 `a3e2b2fac4e8e17c2c47a3dfad8be25f79d6a11d1a13bb5bec4dd663b455bc17`. ทั้งสาม runner มี action log ว่าง. พิกัด input ถูกแก้ให้รองรับ DPI 125%; การนำทางเมนูทำงาน แต่ card click และการตรวจ hand/elixir หลัง click ยังไม่ผ่านการพิสูจน์. ผลแพ้เหล่านี้ไม่ใช่การแพ้ของ policy เพราะยังไม่มี action จาก bot. รายละเอียด: [CHECKPOINTS.md](CHECKPOINTS.md), `runs/cp7-training-camp-20261121-061358-346/run-summary.json` และ `runs/cp7-training-camp-20261121-063427-515/run-summary.json`.

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
| `cu-memory-reader` | memory reader, build fingerprint/canaries, event sequence และแยก exact/inferred/unknown | ต้อง root และ offsets ตรง ABI/build; profile ที่คู่มืออ้างไม่มีใน clone; ไม่พบ LICENSE | ไม่คัดลอก offsets/code; ยืมแนว fail-closed, provenance และ freshness |
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
- import `cv2`, `touch`, `ultralytics`, `opencrbot.vision`, `opencrbot.policy` ผ่านหลังซ่อมไฟล์ OpenCV ใน virtualenv
- ADB ที่ติดตั้งตอบ `Android Debug Bridge version 1.0.41`, `Version 33.0.3-8952118`
- `opencrbot doctor` รายงาน ADB พร้อม แต่ `No ADB devices found`
- การ connect ไป `localhost:6520` ถูกปฏิเสธ (`10061`); ยังไม่มี screenshot จริงจาก Developer Emulator
- ระหว่างนี้ไม่มีการรัน test suite, ไม่เรียก GPGPCPatcher, ไม่แก้/แฟลช image และไม่ส่ง touch เข้าเกม

## Licensing และขอบเขต

`cu-memory-reader` และ vegetableleaf `ClashAI` ไม่พบ LICENSE ใน clone; ไม่ copy code, offsets หรือ weights จากสอง repo นั้น `clash-ai` ระบุ MIT; มี attribution ใน [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) สำหรับส่วน feature extraction/geometry ที่ดัดแปลง และ assets ไม่ได้ถูก bundle

หน้า Developer Emulator ของ Google จำกัดการใช้เครื่องมือไว้กับการพัฒนา/ทดสอบเกมที่ผู้ใช้ได้รับอนุญาตให้เข้าถึงเพื่อวัตถุประสงค์ดังกล่าว ส่วน `clash-ai` README ระบุว่าการ automate เกม Clash Royale อาจละเมิดเงื่อนไขของ Supercell; OpenCRBOT รุ่นปัจจุบันจึงเป็น screen-analysis/assistive MVP ไม่มี automated gameplay

## แหล่งข้อมูลหลัก

- [Google: Download Google Play Games on PC Developer Emulator](https://developer.android.com/games/playgames/emulator)
- [Google: Developing with the Developer Emulator / ADB](https://developer.android.com/games/playgames/pg-emulator)
- [Android SDK Platform release notes](https://developer.android.com/tools/releases/platforms)
- [Android API levels](https://developer.android.com/guide/topics/manifest/uses-sdk-element)
- [cu-memory-reader](https://github.com/vegetableleaf/cu-memory-reader)
- [FirstLight_CR](https://github.com/Jaasssoooonnnnn/FirstLight_CR)
- [sacha9214/clash-ai](https://github.com/sacha9214/clash-ai)
- [vegetableleaf/ClashAI](https://github.com/vegetableleaf/ClashAI)
- [RoyaleFish](https://github.com/fabiospalla9-tech/RoyaleFish)
- [GPGPCPatcher](https://github.com/sekedus/GPGPCPatcher)
