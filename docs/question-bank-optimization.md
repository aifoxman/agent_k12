# 题库驱动的双角色优化

本流程使用本地题库的原版题干与图形，生成模拟拍摄和模拟学生作答，调用真实后端，再由独立教师角色评审。评测输出不等于真实摄像头、儿童手写识别或真人教师认证。原文件、当前家庭数据库与 `AGENTS.md` 不变。

## 两个角色与答案边界

- **教师评审**：独立上下文，根据原题、原卷答案、可用的独立计算和后端实际输出判断；原卷答案有冲突时标记待核查。严重解答错误、误批、编造作答不能被流畅或速度分抵消。
- **后端改进**：只依据开发题失败证据改进通用代码、提示词、解题核查、批注、流式衔接与缓存；禁止加入题号专用答案、坐标或放宽评测来消除失败。
- 题库参考、评分依据只进入教师评审；被测请求使用固定字段清单，只提交图片、年级配置和普通孩子求助。第二层必须引用真实第一层 ID。
- 通过的开发题输出进入候选蒸馏数据；本阶段不进行权重训练。留出题永不进入蒸馏数据。

## 准备

从仓库根目录执行。Python 使用现有开发环境；PDF 图像化另需预编译的 `pymupdf`（可选依赖 `.[corpus]`），DOCX 保留排版导出使用本机已安装的 Word。所有产物位于 Git 忽略的 `output/acceptance/`。

```powershell
$run = 'output/acceptance/question-bank-YYYYMMDD'
python -m scripts.question_bank --output $run snapshot
python -m scripts.question_bank --output $run index --source 'D:\华为云盘\题库'
python -m scripts.question_bank --output $run export
python -m scripts.question_bank --output $run prepare
python -m scripts.question_bank --output $run seal
```

索引识别正文中的答案边界，不只看文件名。压缩包不自动解压，无法解析、无可靠答案、听力缺音频或题干跨页的资料单列。60道目标配额：数学30、语文12、英语12、其他6；不足如实报告，不用自创题冒充原题。

准备阶段最多两个并发调用，检查参考引用确实存在、原题短语位于渲染页、题框包含该短语、有限数值表达式计算无冲突。教师仍须核对整体题意，以上检查不能独立证明全部答案正确。

封存时按试卷来源、相似题分组；同题换数字和照片变体不得跨开发/留出集。目标42道开发题、18道封存题；可用题不足时保持约70/30并报告缺口。封存后不重划样本。

## 真实调用与迭代

启动独立服务，端口8003明确禁止。题库限额库必须同时传给服务端，使后台任务、重试、模型回退和腾讯语音尝试均计数：

```powershell
python scripts/acceptance/serve.py --output output/acceptance/corpus-api-r1 --port 8016 --live --question-bank-budget output/acceptance/question-bank-YYYYMMDD/budget.sqlite3 --question-bank-round 1
python -m scripts.question_bank --output $run run --api-run output/acceptance/corpus-api-r1 --base-url http://127.0.0.1:8016 --round 1 --tag baseline
python -m scripts.question_bank --output $run review --round 1 --tag baseline
python -m scripts.question_bank --output $run report --tag baseline
```

`run` 默认测第一层、第二层、正确作答、错误作答、空白作答。可用 `--case ID --modes hint1 hint2` 定向复验；`--variant perspective` 等选项测试拍摄扰动；`--audio` 在首段讲解后请求真实正常语速语音并保存数据包。为防串题，每道题通过真实注册、设备配对、画面检查建立独立测试家庭。

结果逐项落盘，已有完成记录不覆盖。错误、超时、定位失败和中断保留；需要重新请求时使用新的 `--tag`。修改后重启新的隔离服务并切换对应轮次，再定向复验；构建与自动测试沿用 GitHub Actions，不在生产服务器构建。

采集前记录预期任务数；配对或拍摄检查失败单列为未开始任务，不以已收集的小分母冒充全量完成。拍摄检查只对网络故障和502/503/504最多重试一次，次数跨恢复保留；真正的画面不合格不会自动改为通过。

使用 `prepare-batch` 生成每年级一张三题混合页，再通过 `batch-run`、`review`、`report` 验证正确、错误与空白同页时是否漏题或串题。这些是开发题组合的模拟图，不是真实连拍。空间覆盖检查要求唯一且充分的区域重合，不以最近题号猜配。

教师输入隐藏模拟场景名称及错误答案示例；仅提供正确参考和实际图片。`hint1`保留最终答案、`hint2`完整解答，三类批改均允许给出参考答案。原PDF坐标不能直接评判模拟图批注；干净图使用变换后的坐标，扰动图缺少可靠映射时保留未评。两侧必须使用同一量表和相同输入哈希。

本次60题实际来自DOCX保真导出，数学32、语文12、英语10、科学6；三、六年级英语各缺一份合格样本，由数学替补，缺额记录在`coverage.json`。扫描索引中的PDF、图片和压缩包未纳入本次实测，不能据此声称覆盖全部文件格式。

每轮最多300个逻辑任务、1500次上游请求，最多3轮。两层辅导分别计任务，每张批改图计一个任务；题库整理、教师评审、重试、回退和语音也占上游额度。使用 SQLite 原子预留，重启不能清零；达到限额停止新增调用。`budget` 命令显示累计次数和已报告的 Token，无可靠单价不估算费用。

## 最终留出验收

候选后端冻结后运行 `freeze-final`。最终标签固定为 `holdout-baseline` 和 `holdout-candidate`；留出评测会核对当前代码未再变化、实际服务的源码哈希与封存版本相符。

基线服务使用 `serve.py --server-source $run/private/baseline-src`，候选服务使用当前源码，各自独立数据库与端口。对两个服务分别执行 `run/review --split holdout`，随后执行 `compare --baseline holdout-baseline --candidate holdout-candidate --split holdout`。先完成双方运行，再查看评语；留出失败只用于最终结论，不用于同批再次调参。

报告按年级、学科、题型、模式和图像扰动分层，列出通过、失败、待核查、未评审、严重错误及样本数。配对对比只比较同题、同模式、同变体，不拿不同样本总分宣称提升。

教师内容目标：无严重错误、五维均分至少4、各维至少3。响应目标：缓存命中时有效圈划/思考提示P95≤3秒，首段语音数据P95≤5秒。完整解答和批改另列；接口事件不能证明真实显示或听感，需使用现有浏览器播放流程补充截图和时间线。

保留最佳版本、未解决问题和失败证据；连续两轮无有效改善或达到限额即结束本批，不降低标准宣布通过。
