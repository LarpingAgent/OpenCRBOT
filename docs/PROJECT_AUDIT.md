# Project audit: five repositories

วันที่ตรวจ: 2026-11-17. อ่าน README/คู่มือติดตั้ง, entry points, contract/state flow, device I/O, model/training interfaces, artifacts และ tests ที่มีอยู่ใน clone นี้ การ audit นี้เป็น static review; ไม่ได้ติดตั้ง dependency, รันเกม หรือรัน test suite

## สรุปผล

มีเพียง `sacha9214/clash-ai` ที่มีวงจรอ่านภาพและส่ง input ไปยังเกมจริงโดยตรง แต่ตัวเลือกอุปกรณ์เริ่มต้นออกแบบให้ใช้มือถือ USB และยังไม่ประกาศการรองรับ GPGPC Developer Emulator ส่วน `cr-memory-reader` อ่าน state ได้ละเอียดกว่า screen vision แต่ต้องมี root และตรงกับ build/ABI ที่รองรับอย่างเคร่งครัด อีกสาม repo เน้น simulation, training และ evaluation แบบ offline

ดังนั้น OpenCRBOT จะมี runtime แยกจาก model/training: เริ่มจาก ADB + screenshot + replay; เพิ่ม perception แบบทดลองโดยให้ผู้ใช้ระบุ YOLO weights/templates จาก checkout `clash-ai` เอง ไม่ copy assets เข้าโปรเจกต์หรือถือว่าผลใช้ได้ทันที; action ต้องรอ capture ที่แทนหน้าจอ GPGPC จริง

## Scorecard

| Repo | Runtime ที่ทำจริง | GPGPC/ADB fit | จุดแข็งหลัก | ข้อจำกัดหลัก | สิ่งที่นำมาใช้ |
|---|---|---|---|---|---|
| `cr-memory-reader` | อ่าน process memory บน Android x86_64 แบบ read-only | ADB + root และ game build ตรงเท่านั้น | ความเร็ว, build fingerprint, canary, sequenced events | root, offsets เปราะบาง, docs/source mismatch, profile ที่เอกสารอ้างไม่มีใน clone | fail-closed capability checks, provenance, stale-state/reducer design |
| `FirstLight_CR` | Offline native battle engine + Python environment + policy/trainer | MuMu Android 12 rooted, ARM64, Null’s Royale เวอร์ชันล็อก; มี AVD สำหรับ training แบบ offline | contracts, replay→IL→PPO, model serving, deterministic evaluation | ไม่ใช่ตัวอ่านหน้าจอ/เล่น GPGPC; ติดตั้งและ build ซับซ้อน | typed policy boundary, versioned data, offline evaluation |
| `clash-ai` จาก sacha9214 | Screen stream → YOLO/tracking → rules → taps | ใกล้สุด; ADB/scrcpy แต่ auto-select ตัด emulator ออก | verified tap, timestamps, stall detection, tactical rules, game logs | มือถือ USB เป็นค่าเริ่มต้น, deck/screen calibration เฉพาะ, online automation | capture pipeline, confidence/track history, action verification, replay/report |
| `ClashAI` จาก vegetableleaf | RoyaleSim simulator → imitation/PPO/evaluation | ไม่มี Android device adapter | FAIR/public observation, explicit schema, regression/stop gates | clean install chưa đượcทดสอบ, ไม่มี checkpoint/data ใน clone, ไม่ต่อเกมจริง, ไม่มี LICENSE ใน clone | visible-only contract, unknown/inferred separation, paired evaluation gates |
| `RoyaleFish` | FirstLight V4 native engine collectors + CUDA policy/training | Offline engine เท่านั้น | ghost evaluation, league/self-play, efficient packed inference | ระบุชัดว่า offline-only; engine ไม่รวม, CUDA/ทรัพยากรสูง, model tied to 16.635 | held-out scenarios, quantitative reports, packed data patterns |

## 1. `cr-memory-reader`

อ่านประกอบ: [README](../../cr-memory-reader/README.md), [x86_64 guide](../../cr-memory-reader/x86_64/内存读取技术指南.md), [capture entry point](../../cr-memory-reader/x86_64/tools/capture_native_entity_stream.py), [memory adapter](../../cr-memory-reader/x86_64/deploy/proc_memory.py).

### Data flow และสิ่งที่ดี

คู่มือ x86_64 อธิบายสามชั้น: native helper อ่าน `/proc/<pid>/mem`, Python `proc_mem` เป็น calibration/fallback, แล้ว host reducer รวม events เข้า StateStore/UI/API. ตัวระบบพยายามตรวจ package/version/ABI/`libg.so` digest ก่อนใช้ offsets; ใช้หลาย canary แทนเชื่อค่าจาก pointer เดียว; มี sequence ID, dedupe, bounded rings และจำแนก `exact`, `estimated`, `unknown`. มีบันทึก latency และ acceptance ของ MuMu/Null’s Royale.

แนวคิดสำคัญที่เหมาะกับ OpenCRBOT คืออย่าลดค่าที่เดาให้กลายเป็นค่าจริง, แยกความพร้อมของแต่ละ capability, ติดตามความสด/sequence ของ state, จำกัด buffer และปฏิเสธ source ที่ไม่ผ่าน validation.

### ข้อเสีย/ข้อจำกัด

- ต้อง root ADB และอ่าน process memory; จึงไม่ใช่ adapter ทั่วไปสำหรับ Android/Google Play runtime.
- Offsets ถูกผูกกับ ABI, version code, ขนาด/hash/Build ID ของ `libg.so`; GPGPCPatcher เปิด ADB/root ไม่ได้แปลว่า game build ตรง.
- มีความไม่ตรงกันระหว่าง README/entry point กับคู่มือ: README และ `capture_native_entity_stream.py` ตั้ง Null’s Royale, MuMu, `localhost:5557`; คู่มือ profile ระบุ `com.supercell.clashroyale`, x86_64 build เฉพาะ และ BlueStacks Rvc64. คู่มือยังอ้าง `profiles/cr-150535022-x86_64.json` แต่ไฟล์นั้นไม่มีใน clone นี้.
- หลาย capability ในเอกสารยัง `uncalibrated` เช่น target coordinates, spell effect lifetime และความหมาย evolution; state ที่ละเอียดจึงไม่ได้แปลว่าทุก field ถูกต้อง.
- ไม่มี test suite ใน clone และไม่พบ LICENSE; ไม่ควรนำโค้ด/offsets ไปวางใน OpenCRBOT โดยไม่มีสิทธิ/การตรวจ build.

### คำตัดสิน

**ไม่ใช้เป็น runtime sensor หลักของ OpenCRBOT.** นำเฉพาะวิธีสร้าง build manifest, capability status, confidence/provenance, monotonic sequence และการปฏิเสธข้อมูล stale/ผิด build. OpenCRBOT เริ่มจากข้อมูลที่เห็นบนหน้าจอ.

## 2. `FirstLight_CR`

อ่านประกอบ: [README](../../FirstLight_CR/README.md), [architecture](../../FirstLight_CR/docs/architecture.md), [policy service](../../FirstLight_CR/docs/policy_service.md), [getting started](../../FirstLight_CR/docs/getting_started.md), [training guide](../../FirstLight_CR/docs/training.md), `native_runner/contracts.py`, `native_runner/training/v4/serve_policy.py`.

### Data flow และสิ่งที่ดี

เป็น stack ที่ครบสำหรับการวิจัย: native battle engine → `BattleEnvV1`/typed contracts → tensorizer/recurrent V4 policy → offline agent, policy service, replay viewer และ trainers. Replays ถูกแปลงเป็น supervised sequences ก่อน imitation learning แล้วใช้ PPO self-play/matchmaking. `serve_policy` แยก model inference ด้วย JSON-line protocol; trainer และ evaluator ใช้ contracts เดียวกัน.

จุดแข็งคือ action/observation contracts ชัด, checkpoints มี metadata, replay/evaluation เป็นส่วนหนึ่งของ workflow, training ใช้ offline environment ที่ควบคุม seed และ opponent ได้, และมี test suite ขนาดใหญ่ใน repo.

### ข้อเสีย/ข้อจำกัด

- จุดประสงค์คือเล่น **offline** ใน engine ที่ build จาก Null’s Royale; README ล็อก Android 12 MuMu + root + ARM64 + version/content 15.535.
- การติดตั้ง engine เปลี่ยน APK, แทรก probe และตั้ง network isolation; จึงไม่ใช่เส้นทางต่อกับ GPGPC retail/Developer Emulator โดยตรง.
- Linux training AVD ก็ต้อง root adbd และรัน ARM64 library; Google Play image ปกติไม่ได้ตอบโจทย์ root requirement.
- Python 3.12, JDK, NDK, SDK และ native build ทำให้ setup หนัก; contracts/catalog ของเกมเวอร์ชันต่างกันจะเข้ากันไม่ได้.

### คำตัดสิน

นำแนวทาง contracts, policy adapter, replay format และ offline evaluation มาเป็นแบบอย่าง แต่ไม่ย้าย engine/weights เข้า runtime ของ OpenCRBOT. ต้องมี adapter แปลง public screen observation ให้ตรงกับ policy contractก่อนจึงจะใช้ model ได้.

## 3. `sacha9214/clash-ai`

อ่านประกอบ: [README](../../clash-ai/README.md), `clashai/device.py`, `clashai/actions.py`, `clashai/agent.py`, `clashai/brain.py`, `clashai/detect_yolo.py`, `clashai/opponent.py`, `clashai/strategy.py`, `tests/`.

### Data flow และสิ่งที่ดี

`Device` เริ่ม scrcpy server บนอุปกรณ์, รับ H.264 frames และส่ง touch ผ่าน control socket. Agent ประมวลผล frame ใหม่เท่านั้น, ตรวจ stream stall, รัน detector/tracker, อ่านมือ/elixir/tower UI, ประเมินคู่ต่อสู้จากสิ่งที่ปรากฏ, ตัดสินใจด้วย tactical rules และตรวจว่าการ์ดถูกยก/ออกจากมือจริงหลังแตะ. Logs ประกอบด้วย action, เหตุผล, latency, state snapshot และภาพสำหรับ replay/labeling. มี YOLO weights, placement model, templates และ tests อยู่ใน clone.

แนวทางนี้ใกล้กับ OpenCRBOT มากที่สุด: แยก perceive/think/act, เก็บ timestamp, ตรวจ input ที่ส่งไปแล้ว, ไม่เดินต่อบน frame ค้าง, ใช้ placement ที่เรียนรู้เป็นส่วนหนึ่งของ policy, และเรียนรู้ setting จากผลเกม.

### ข้อเสีย/ข้อจำกัด

- README ระบุชัดว่าทดสอบบนมือถือจริง; `first_usb_device()` ตั้งใจไม่เลือก serial ที่ขึ้นต้น `emulator`, และ scripts สร้าง `Device()` โดยไม่ส่ง serial. คลาส `Device` รับ serial เองได้ แต่ CLI/entry points ยังไม่เปิดตัวเลือกนี้.
- scrcpy server version, screen geometry, card templates, HUD sampling และ tactical coordinates ถูกปรับจากอุปกรณ์/ความละเอียดเฉพาะ; ต้อง calibrate ใหม่บน GPGPC.
- Deck/brain เน้น Giant deck ที่ระบุใน README; card catalogue และการประเมิน duel ไม่ใช่ policy ทั่วไป.
- detector CPU ช้าเมื่อเทียบ GPU; training/calibration data ผูกกับเกมและภาพที่เก็บมา.
- README เตือนว่า automation ขัดกับเงื่อนไขของ Supercell และแนะนำใช้ friendly/training scenarios.

### คำตัดสิน

เป็นแหล่งอ้างอิง runtime หลัก: ADB transport, frame age, confidence over track history, verified tap และ artifact logging. OpenCRBOT รับ path ของ YOLO `.pt` กับ card templates จาก checkout นี้เป็น optional experimental input โดยยังไม่ copy asset หรือ auto-download; geometry และ detector ยังไม่ validate กับ GPGPC และ card matcher ควรกรอก `known_deck` ก่อนใช้ผล. Repo ระบุ MIT; การนำหรือแจกจ่ายไฟล์ต่อให้คง notice และตรวจเงื่อนไขของ asset.

## 4. `vegetableleaf/ClashAI`

อ่านประกอบ: [README](../../ClashAI/README.md), `pipeline/obs_contract.py`, `pipeline/royale_env.py`, `pipeline/rl_royale.py`, `pipeline/e1_eval.py`, `pipeline/policy_io.py`, `tools/README.md`.

### Data flow และสิ่งที่ดี

โปรเจกต์ฝึก X-Bow policy บน RoyaleSim/RoyaleGym; `BoardState` เป็น schema normalized 18×32, แยก own/opponent, towers, units, hand/next card และ confidence/age ของ observations. README ระบุว่าป้อนเฉพาะสิ่งที่ผู้เล่นเห็น; opponent hand/next card ไม่ถูกส่งเข้ารุ่น และ opponent elixir คำนวณจาก public events. มี imitation data, PPO, self-play/league, paired evaluation, held-out screen และ stop rules เพื่อตัด candidate ที่ regression.

เป็นแบบอย่างที่ดีสำหรับ FAIR observation contract, common coordinate frame, deterministic replay, versioned vocabulary และการประเมินบนคู่ match เดิมแทนเชื่อ win rate ชุดเดียว.

### ข้อเสีย/ข้อจำกัด

- README บอกว่าขั้นตอนติดตั้งยังไม่ได้ทดสอบบน clean install.
- `RoyaleSim`/`RoyaleGym` เป็น dependency ภายนอกและ runtime config มีความเฉพาะ; บาง experimental models มาจาก engine ที่ไม่ได้อยู่ใน repo.
- ไม่พบ checkpoint หรือ replay pool ใน clone; training ต้องเตรียม checkpoint/data เพิ่ม.
- ไม่มี ADB, screenshot, OCR/vision หรือ device action layer; schema อาศัย simulator data.
- ไม่พบ LICENSE ใน clone นี้; ห้าม copy source/model โดยคิดว่าอนุญาตอัตโนมัติ.

### คำตัดสิน

นำ public-only observation rules, unknown/inferred/exact แยกกัน และ evaluation gates มาเป็นข้อกำหนดกลาง. ไม่ย้าย model จนกว่าจะ map input dimensions, vocabulary, timing และ catalog อย่างมีหลักฐาน.

## 5. `RoyaleFish`

อ่านประกอบ: [README](../../RoyaleFish/README.md), `royalefish/ever_collector.py`, `royalefish/ever_server.py`, `royalefish/ever_learner.py`, `patches/native_runner_16635.diff`, `NOTICE`.

### Data flow และสิ่งที่ดี

โมเดล V4 ใช้ FirstLight `native_runner` เพื่อรัน matches แบบ resident native engine; collectors pack observations, ใช้ inference server บน CUDA, parallelize rollout, แล้ว PPO learner update. Repo มี checkpoints ราว 67 MB ต่อไฟล์ และข้อมูล card 16.635. มีการประเมินกับ held-out human attacks และ league ที่ผสม opponent versions; README รายงานข้อดี/ข้อเสียและตัวเลขที่วัดไว้ ไม่พึ่งสโลแกนว่า “ฉลาด”.

สิ่งที่ควรเรียนรู้คือ held-out scenarios, paired comparison, model lineage, data packing และแยก learner จาก collector.

### ข้อเสีย/ข้อจำกัด

- README ระบุว่าเล่น **offline only** และเขียนชัดว่าไม่ให้ใช้ ladder/ranked/online; ไม่ใช่ real-device bot.
- ต้องจัดหา native engine เอง, ใช้ FirstLight V4 modules และ patch ต่างหาก; engine/binaries ไม่รวม.
- Requires Python 3.12, PyTorch/CUDA; ตัวอย่าง training ใช้ Xeon 72 threads, RAM 128 GB และ GPU สองใบ.
- Mainline model ฝึกกับ card data 16.635; FirstLight README รองรับ 15.535 content. นำ checkpoint สลับ engine/catalog ตรง ๆ ไม่ได้.
- README ระบุเองว่างานบางรุ่นทำเฉพาะ defense หรือมีข้อจำกัดด้าน attack; benchmark บน ghost data ไม่แทน reactive opponent ทุกแบบ.

### คำตัดสิน

นำวิธีประเมินและการรายงาน model lineage มาใช้; อย่าใช้ checkpoint เป็น live policy หรือเชื่อว่า model อยู่ใน observation/action contract ของ OpenCRBOT.

## การนำข้อดีมารวมใน OpenCRBOT

1. **Transport จาก clash-ai:** ADB per-device, frame timestamp, no stale frame loop, action verification ภายหลัง.
2. **Data discipline จาก memory reader:** source + confidence + age + capability status; unknown คงเป็น unknown; reject build/profile mismatch.
3. **Contract จาก FirstLight/ClashAI:** versioned observation/action schema, normalized board coordinates, stable enum/card vocabulary.
4. **Fair information จาก ClashAI/sacha:** policy ใช้ข้อมูลที่เห็นหรือ derive จาก event สาธารณะ; ไม่ใช้ hidden hand/queue.
5. **Learning/replay จากทุกสาย:** เก็บ screenshot, observation, decision, action result และ game result; offline review ก่อน train.
6. **Evaluation จาก RoyaleFish/ClashAI:** held-out captures/scenarios, paired seeds/fixtures, regression gates, model/data manifest.
7. **Fail-safe:** ก่อน perception calibrated ให้ OpenCRBOT แค่ capture/inspect; action mode ต้องมี action verification และ confidence/freshness gate.

ไม่ copy code/weights จาก repos ที่ไม่มี license ระบุใน clone นี้ (`cr-memory-reader`, `vegetableleaf/ClashAI`). สำหรับ MIT/Apache repos ต้องคง NOTICE/license และตรวจว่า model artifacts มีสิทธิเดียวกับ source.

## Dependencies, licensing และ QA maturity

| Repo | License ใน clone | ภาระ dependencies/artifacts | QA ที่พบจากไฟล์ |
|---|---|---|---|
| `cr-memory-reader` | ไม่พบ LICENSE | Python + pygame, Zig/native helper ที่ build สำหรับ Android x86_64, rooted emulator, offsets เฉพาะ build | ไม่พบ test modules; คู่มือมี acceptance log แต่ profile ที่อ้างไม่มีใน clone |
| `FirstLight_CR` | Apache-2.0 + NOTICE | Python 3.12, PyTorch, numerical/data packages, JDK/NDK/SDK สำหรับ build offline APK, game APK ที่ผู้ใช้จัดหา | มี test modules จำนวนมากและ contracts ครอบคลุม; ยังต้องติดตั้ง toolchain/native game data ให้ตรงรุ่น |
| `clash-ai` | MIT | PyTorch/Ultralytics, OpenCV, PyAV, tracker; มี YOLO/placement weights และ card templates | README อ้าง 129 tests; พบชุด test แยก 8 ไฟล์และผล replay/log ที่ใช้ทบทวนการเปลี่ยน brain |
| `ClashAI` | ไม่พบ LICENSE | PyTorch, RoyaleSim/RoyaleGym, YAML/msgspec และ replay/checkpoint ภายนอก | มี test modules หลายสิบไฟล์; README ระบุเองว่า clean install ยังไม่เคยทดสอบ |
| `RoyaleFish` | Apache-2.0 + NOTICE | Python 3.12, PyTorch/CUDA, engine ที่ต้องจัดหา, FirstLight V4 patch, 16.635 catalog | ไม่พบ test suite ระดับ root ใน clone; README มี held-out reports และ native-probe test sources ใน patch |

ตัวเลข test file เป็นการนับไฟล์ใน clone ไม่ใช่จำนวนกรณีทดสอบที่รันผ่าน; ไม่ได้รัน test suite ระหว่าง audit นี้.

## ความเข้ากันได้กับ Google Play Games on PC

- Developer Emulator มี ADB ที่ `localhost:6520` ตามเอกสาร Google; ADB screenshot/input เป็นเส้นทางเริ่มต้นที่ไม่ต้อง root.
- GPGPCPatcher เป็นทางเลือกสำหรับ production/dev image, เปิด ADB/root ผ่านการ patch และมี backup/restore; มันไม่ได้ทำให้ offsets ของ `cr-memory-reader` ตรงกับเกมเวอร์ชันที่ติดตั้ง.
- `clash-ai` มี reusable screen path แต่ต้องเปิดให้เลือก serial และทดสอบ scrcpy/screen dimensions กับ GPGPC.
- Offline simulator repos ไม่มี device adapter.
- Consumer client กับ Developer Emulator เปิดพร้อมกันไม่ได้.
