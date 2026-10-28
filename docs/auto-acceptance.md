# 自动验收与逐轮改进

验收分为受控浏览器流程、真实模型教学评测和 Android 观察。每轮保留输入、截图、播报文字、检查结果与失败原因；修复后重跑对应场景，不把样本题号、答案或坐标拼接进生产代码或提示词。通用教学规则的修改也须复验。所有命令从仓库根目录执行，需要现有 Python 开发环境、Node.js；Android 另需 ADB 与可调试 APK。

## 浏览器与模型验收

先启动前端：

```powershell
npm run dev --prefix src/client -- --host 127.0.0.1 --port 5186
```

只跑受控流程，不调用真实模型：

```powershell
uv run python scripts/acceptance/run.py run --output output/acceptance/demo --browser controlled
```

真实模型评测需在另一个终端启动独立 API。`--live` 明确启用已配置的模型，会产生真实调用；服务使用运行目录内的独立数据库和新生成的密钥，不读取家庭学习数据库。

```powershell
uv run python scripts/acceptance/serve.py --output output/acceptance/demo --port 8016 --frontend-port 5186 --live
uv run python scripts/acceptance/run.py run --output output/acceptance/demo --base-url http://127.0.0.1:8016 --live --browser both --limit 6
```

需要修复时先保留失败证据，再执行复验。可用 `--cases` 指定 manifest 中的场景 ID；不覆盖基线结果。

```powershell
uv run python scripts/acceptance/run.py run --output output/acceptance/demo --base-url http://127.0.0.1:8016 --live --round rerun --browser none --cases arithmetic_wrong
uv run python scripts/acceptance/run.py report --output output/acceptance/demo
uv run python scripts/acceptance/run.py watch --output output/acceptance/demo --seconds 120
```

`run.py --browser ...` 是通用模型／旧浏览器冒烟入口，不会自动调用最新 `teaching-paced`、`grading_flow.py`、`grading-ui.mjs` 或 `task-cycle`。当前专项的一条命令入口、独立家庭准备及串行运行代码块见 [BROWSER.md](../scripts/acceptance/BROWSER.md)，代码块未作为整体执行验证。分步辅导测试“下一步／再讲这步／慢一点”和退出；批改另测授权、结果、复检修订、暂停隐藏与重新授权；任务另测收页、提交、家长退回、补做说明、重交和确认。不要把其中一个报告的通过状态外推到其余专项。

批改测试支持服务端 `CORRECTION_REQUIRE_PARENT_APPROVAL=false`，重启后孩子端直接批改。验收须记录该开关：测试模式通过不能算作家长授权流程通过。默认 `true` 与测试 `false` 应分别检查；两种模式都需确认其他家庭或设备不能越权、暂停和结束阻止继续批改，切回 `true` 后无授权会话重新等待家长。测试模式不创建或延长家长授权，不改变家长最终确认作业的流程。

持续播放验收优先使用 `npm run build --prefix src/client` 后的静态预览：`npm run preview --prefix src/client -- --host 127.0.0.1 --port 5187`。若使用 Vite 开发模式，验收期间冻结前端文件；HMR／文档重载会中断播放器与拍摄弹窗，须保留为环境中断并重新运行，不能直接判成模型失败或自动补作通过。

已授权保存的摄像头 fixture 位于 `output/acceptance/fixtures/camera-20261023-060842/`：`source.json` 记录采集时间、1280×960 尺寸、外部屏幕介质及后续仅供模拟的用途。后续将 `source-camera.jpg` 喂给 canvas 摄像头时，来源必须写“保存图模拟”，不能因原始照片曾来自真实 Pad 就把后续运行称为新实拍。模拟学生答案由 `grading_flow.py prepare` 新绘制并明确标注，不修改该原图；`manifest.json` 中的标准答案只用于独立评分，不进入教师请求。

清晰电子作业按实际题目内容分页，不能只因它来自屏幕或截图而拒绝；模糊、遮挡、缺题或页界不足仍应拒绝猜框。任务收页须另验科目匹配：明确年级／学段／册次和“奥数”等可靠别称可归一，任意自定义科目仍按完整名称匹配。保存“异科目页未加入”的真实提示，不能把零页误报为重复页。

当前汇总入口为 [full-simulation-20261023-063600/report.html](../output/acceptance/full-simulation-20261023-063600/report.html)，本机预览端口为 5196。第六轮完成 18 段与 10 项独立教学必验检查；任务闭环由同家庭首次提交／退回与续测重交／确认两段证据组成。第五轮关联局部图裁切及旧 5195 失败仍保留；新结果不代表真人理解、实听或实物纸面通过。详见 [设备验收追加记录](device-validation.md)。数字／算式同步还应逐段核对实际原话，不能只统计标记数量。

## 每题两层辅导的验收门槛

第一层“读题与思路”须解释题意、条件和可执行的方法，不能只举一例再连续追问。孩子可退出作答；按需选择“解题思考”后，第二层须讲清完整过程、每步理由、可靠结果与检查，重听不得发出第三层请求。题号不明确时的正常澄清单独记载，不冒充已经完成第一层。

两层分别检查实际口述与原题关键词的小范围圈划。整题定位圈、只圈“小提示”标题、让孩子自行划线，都不能代替老师同步标出关键条件和所求。逐字原词、可靠位置和不裁字的限制仍生效；跨行短语应使用各行实际位置，不能捏造一个整词框。具体操作和已讲算式应在原图或图下过程区可见，方法、比较与理由保留在草稿；不能只统计笔迹数量。

保存两次实际上传 JPEG 的摘要与 `previous_hint_id` 绑定、真实 HTTP/SSE、分步截图和退出后的画面。另查唯一逻辑进度、无自动听答、核对说明不显示为整课完成、冷回看没有旧照片时不叠到新相机。竖屏至少核查宽题和竖长题，题图、草稿及过程区要可读、不互相遮挡。

数学正确性由独立参考复核，参考答案不得注入教师请求。使用保存实拍图时，必须标记为保存照片模拟；相机、OCR、API、模型、声音哪些真实、哪些受控须分别说明。真实模型返回、受控音频回调和新 Pad 声学验收不能相互替代。失败结果保留，修复后另建复验目录；未获授权的原家庭批改不得随辅导测试开启。

## 有限语音回环

使用已经启动的隔离 API 和该轮 `state.json` 合成家庭：

```powershell
uv run python scripts/acceptance/audio.py --output output/acceptance/demo --base-url http://127.0.0.1:8016
```

脚本先核对 `/health` 的运行标记，再发送合成家庭凭据；不接受生产端口 `8003` 或 Android 家庭配置。优先采用本轮 `units_wrong_thought` 的教师原话，否则使用明确标注的数字单位短句。真实 TTS 返回的 PCM 和 WAV 保存至 `audio/`，`audio-report.json` 附可点击音频证据与时长。

现有本地 ASR 就绪时才临时授权转写，将生成的 WAV 回送 `/v1/voice/transcribe`，比较阿拉伯数字和中文数字对应的单位是否保留。ASR 未就绪则单独记录未验证，仍保留 TTS 音频，不下载模型。结束后恢复合成家庭原来的语音开关。这是**合成语音到 ASR 的回环**，没有环境录音；`audio=live` 表示实际音频服务证据，不等于真人实听。只有取得有效音频字节的 TTS 门槛标为真实来源。

`run` 退出码为 `0`（有效硬门槛全部通过）、`1`（流程或检查失败）、`2`（仍有运行中或待复核项目）。开放题人审、缺少音频证据等不会变成成功退出。已有基线目录再次运行时须使用 `--round rerun`，防止覆盖旧证据。

## Android 画面与播报观察

安装和前台切换由操作者或设备流程明确发起，观察工具不会自行安装、打开相机或创建真实家庭任务。当前默认设备 `f864aef1`、孩子端包 `com.paperbetween.child`，ADB 为仓库内 `.tools/sdk/platform-tools/adb.exe`，独立 CDP 转发端口为 `9226`。工具每次重新查 PID，不依赖上次进程号。

```powershell
uv run python scripts/acceptance/android.py --run-dir output/acceptance/demo inspect
uv run python scripts/acceptance/android.py --run-dir output/acceptance/demo watch --duration 60 --interval 2
```

每两秒记录可见应用画面的 PNG、DOM 快照、页面异常，以及 `paper-speech` / `paper-speech-progress` 的播报文字。原生 TTS 开始、结束回调采用可恢复的委派包装，后续播放器赋值仍会生效；PCM 回调不包装，保留身份比较语义。**原生播报回调不证明声音实际可听，更不算真人实听验收**，观察输出的音频来源保持 `unknown`。目标页面不在前台时不截取其他应用画面；不采集输入框值、本地存储或任意控制台日志。

可选安装命令须明确给出已构建 APK：

```powershell
uv run python scripts/acceptance/android.py --run-dir output/acceptance/demo install --apk src/android/child/build/outputs/apk/debug/child-debug.apk
```

## 自动跑平板辅导与批改

先完成前端构建和孩子端 APK 构建，再显式运行设备流程。`--output` 必须是已经准备好合成试卷且有对应隔离 API 的验收目录：

```powershell
uv run python scripts/acceptance/device_flow.py --output output/acceptance/demo --apk src/android/child/build/outputs/apk/debug/child-debug.apk
```

流程先备份原配置，临时使用独立合成家庭；确认相机流来自合成纸面、禁用麦克风输入后，自动请求真实辅导和批改，每两秒保存实机截图与播报事件。原生 TTS 仍委派给真实 Android 桥；结束或失败后先停止观察线程，再恢复原配置和 API 转发。保留默认设备以外的设备时使用 `--serial`。只有安装时需要提供 `--apk`；不带该参数使用现有安装。

## 真实声学录音

需要实际验证外放时，可在明确授权的安静环境中启动 Pad 麦克风录音。先固定本轮 APK 与页面；录音期间不要安装或重载。以下 `start` 保持运行，打印 `recording` 后，再通过真实界面触发讲解；在另一个终端执行 `stop`：

```powershell
uv run python scripts/acceptance/acoustic.py --run-dir output/acceptance/demo start
uv run python scripts/acceptance/acoustic.py --run-dir output/acceptance/demo stop
```

脚本只使用真实 `getUserMedia` / `MediaRecorder`，不触发播报、不替换识别器或原生回调，不读取帐号或修改相机。浏览器和宿主均设 180 秒截止；停止、启动失败和延迟取得权限时清理全部录音音轨。原始环境音、实际采集设置与时间线仅保存至 `private/acoustic-*.webm` / `.json`，不进入公开预览。

声学录音与 TTS 文件回环分开记录。离线 ASR 只能根据录音字节转写，禁止用预期教师原话充当提示或回填识别结果。若导出教师节选供试听，应标出切片时间、增益等处理；零声音、错配或无法确认的内容保留失败或未验证。录到原生播报也不代表真人发言或真人已经听过。

## 测试配置与恢复

改动平板本地配置前先备份；备份与 JavaScript 求值结果只写入运行目录的 `private/`，命令不输出令牌或存储内容。

```powershell
uv run python scripts/acceptance/android.py --run-dir output/acceptance/demo backup-storage
uv run python scripts/acceptance/android.py --run-dir output/acceptance/demo evaluate --script output/acceptance/demo/private/check.js
uv run python scripts/acceptance/android.py --run-dir output/acceptance/demo configure-synthetic --state output/acceptance/demo/private/synthetic-state.json
uv run python scripts/acceptance/android.py --run-dir output/acceptance/demo restore-storage --backup output/acceptance/demo/private/STORAGE_BACKUP_FILENAME.json
```

合成配置文件必须声明 `source: "synthetic"`，并提供字符串键值的 `storage` 对象。工具在写入前自动备份当前存储，并标记测试来源。只使用隔离 API 创建的测试帐号，不把真实家庭令牌拷入验收材料；返回 `reload_required` 后由设备流程显式重新加载。恢复真实配置前必须先设置观察线程的 `stop_event`，并等待线程退出，随后恢复、重新加载，避免把真实家庭界面录入测试证据。

语音回环需要项目的可选依赖：`uv sync --extra dev --extra asr`，并需 `.env` 指向已经准备好的本地模型。音频脚本本身不会安装依赖或下载模型。保存的转写可不调用服务再次核查：

```powershell
uv run python scripts/acceptance/audio.py --output output/acceptance/demo --review-only
```

数字和单位检查与运算词检查分开；转写中的“减／件”等差异保留为待复核，需实听区分发音与识别错误，不自动改写转写或教师原话。

## 报告与来源边界

`audit.json` 与 `report.html` 在每次更新时原子替换，HTML 每两秒刷新。报告保留全部历史轮次，并合并各子系统仍有效的硬性门槛。完整浏览器复跑须显式声明 `supersedes`；模型局部复验只替换相同 `scope=model` 的稳定检查 ID。模拟结果不能替代真实来源的失败，未执行或缺少来源的检查保留为未验证。

合成试卷搭配真实模型是有效的模型评测；真实纸面、Android 设备和真实音频链路是不同维度，分别用 `requires_source.input/device/audio/model` 声明。旧 `requires_real` 等价于要求 `model=live`，不自动代表真人或真机通过。真人实听须另有明确验收记录，不能由 `audio=live` 或原生播放回调推断。

真实相机拍摄另一块屏幕的题图记为 `input=screen`、`medium=external_screen`。它可验证实际摄像头识题与辅导，不满足实体纸面 `input=live` 的门槛。受控回放另列布局检查，不能替代对应实景图示的失败。

截图与 JSONL 时间线位于运行目录的 `android/` 或对应浏览器/模型轮次；报告证据只接受目录内的相对路径，拒绝外部链接、路径逃逸和 `private/`。不要公开整个运行目录或私有配置。单独刷新 HTML 可运行：

```powershell
uv run python scripts/acceptance/report.py output/acceptance/demo
```

本机浏览器预览可运行 `uv run python scripts/acceptance/preview.py --output output/acceptance/demo --port 5191`，打开 `http://127.0.0.1:5191/report.html`；服务只开放报告与审计列明的证据，拒绝私有配置、数据库和目录列表。

报告、Android helper 与语音回环的回归测试：

```powershell
uv run pytest -q tests/test_acceptance_report.py tests/test_acceptance_android.py tests/test_acceptance_audio.py
uv run ruff check scripts/acceptance/report.py scripts/acceptance/android.py scripts/acceptance/audio.py tests/test_acceptance_report.py tests/test_acceptance_android.py tests/test_acceptance_audio.py
```
