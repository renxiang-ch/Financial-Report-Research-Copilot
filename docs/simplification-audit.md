# 精简审计 — 2026-08-26

> **状态（2026-08-25 收工）：A 类七项全部完成，commit `afb0de3`。** 每条下方的"修"段落保留原样作为决策记录，实际做法与差异见文末「A 类完成记录」。B 类（三个 harness 外壳、四个 probe 外壳）与 C 类（`generate_dataset.py`、`embedding_v1` 到期、`slots` 的 P3 预留字段）**未做**。

对 agent 项目做了一次结构审视：哪里重复、哪里能用更少的东西达到同样效果、哪里看着像冗余其实不是。

**方法**：逐模块行数统计、跨文件符号重复检查、AST 层面的未引用扫描、以及对每个"疑似重复"逐个打开核实。不是凭印象列的——每条都附证据和复现方式。

**当前规模**

| 层 | 行数 | 模块 |
|---|---|---|
| `agent/` | 2,741 | agent 648 · tools 613 · grounding 526 · slots 452 · authority 230 · provenance 136 · conversation 81 · model_router 55 |
| `eval/` | 2,241 | harness 731 · harness_tier3 526 · generate_dataset 452 · harness_router 187 · 四个 probe 570 |
| `retrieval/` | 369 | bm25 177 · hybrid 105 · dense 87 |
| `views/` | 700 | chat 388 · exposure 131 · whatif 93 · supplier 88 |

**本周新增**：`slots.py` 452 + `authority.py` 230 = 682 行，占 agent 层的 25%。下面 A2 那条是这次扩张带来的直接后果。

---

## A. 有实际风险，值得优先处理

### A1 — accession 正则又分叉了（同类缺陷第六次，且当前是活的）

周一我把三份重复的 accession 正则合并成 `grounding.accessions_in()`，commit 里写了 "one definition, imported"。**`provenance.py` 没有改到。**

```python
# provenance.py:37
for m in re.findall(r"\d{10}-\d{2}-\d{6}", blob):
```

`grounding.accessions_in()` 认两种写法——正文里带横线的 `0000320193-24-000123`，和 URL 路径里不带横线的 `000032019324000123`。`provenance` 只认第一种。

**后果**：一个只用链接给出引用的答案，`grounding` 认为它引用了、`provenance` 认为它没有。两个都会出现在同一份返回值里。这正是周一那次修复的起因——当时它造成了 5 个正确答案被误标。

**修**：`provenance._accessions` 改为调用 `grounding.accessions_in`。**约 15 分钟**，加一个测试钉住两者一致。

**教训值得记**：我在修复这个缺陷类的同一天，漏掉了它的第四个实例。检查一个正则有几份拷贝，不能只查我记得的那几个文件。

### A2 — 三个 `steps` → 事实索引，其中两个是我这周建的

| 函数 | 位置 | 产出 | 建于 |
|---|---|---|---|
| `collect_grounded` | grounding.py:175 | 值的集合 | 更早 |
| `_tag_values` | grounding.py:288 | 值 → (ticker, 财年) | **周二**（绑定检查） |
| `_fact_index` | authority.py:107 | 值 → (label, 财年, ticker) | **今天**（出处分类） |

三次遍历同一份 `steps`，各建各的索引，而且**处理细节已经不一致**：

- `_tag_values` 把边的百分比同时索引成 `46.0` 和 `0.46`（模型两种写法都用）
- `collect_grounded` 只索引 `46.0`
- `_fact_index` 索引两种，还带 ticker

匹配容差也是三套：`_tag_values` 用 `1e-6` 相对容差，`collect_grounded` 用 `_REL_TOL=0.005` + SI 跳档，`_fact_index` 用 `round(x, 4)`。

**后果**：将来给某个索引补一种数值形式（比如百万单位），另外两个不会跟着改，而三者服务的是同一批检查。这跟 A1 是同一个病。

**修**：抽一个 `trace_index(steps) -> TraceIndex`，一次遍历，同时提供"值 → 标签/ticker/年份/来源工具"的查询，三个消费方共用。**约 1.5 小时**。会缩掉大约 60 行，更重要的是把三套容差变成一套需要显式决定的东西。

### A3 — 拒答判定三份，语义不同且无测试保护

| 位置 | 短语数 | 语义 |
|---|---|---|
| `harness._is_refusal` | 15 | 宽：含 `"not in"`、`"outside"`、`"no data"` |
| `provenance._REFUSAL_MARKERS` | 6 | 严：只认第一人称，注释解释过为什么 |
| `agent.py` 两段拒答文本 | — | 产生方 |

前两者的差异**是有意的**——`provenance` 的注释写明"匹配裸 `not disclosed` 会在正确解释阈值披露的句子上误触发"。但：

1. 没有任何测试钉住这个差异，下次有人"统一"它们时会静默改变评分口径
2. `harness._is_refusal` 里的 `"not in"` 和 `"outside"` 太宽——`"revenue outside the US"` 会被判成拒答

**修**：把两份都移到一处，用两个具名常量表达差异（`REFUSAL_STRICT` / `REFUSAL_BROAD`），并给"为什么两套"写测试。顺手收紧 `"not in"`。**约 40 分钟**。

---

---

## A4–A6 — 硬编码常量对账（2026-08-25 补，实测三处分叉）

把全代码库的模块级常量列了一遍（约 90 个），然后**只做一件有用的事**：把每个引用数据库内容的常量拿去和真实数据对账。清单本身不重要，**已经脱节的那几个才重要**。

对账通过的（记录下来，说明它们被检查过）：`authority.REGISTRY` 的 14 个 label、`slots._METRIC_TERMS` 的 10 个映射目标、`slots._EXTRA_ALIASES` 的 7 个 ticker、`companies.CLUSTER_V1`/`CLUSTER_RESEARCH` 的 21 个 ticker——全部存在于库中。

### A4 — 工具 schema 的 metric 清单：告诉模型 10 个，库里有 24 个

`agent.py` 的 `TOOL_SCHEMAS` 里，`query_financials.metric` 的描述写死了：

```
"One of: Revenue, GrossProfit, NetIncome, OperatingIncome, EPS_Basic,
 EPS_Diluted, TotalAssets, LongTermDebt, R&D, COGS"
```

库里实际有 **24 个**。模型看不见的 14 个：

```
CapEx  CurrentAssets  CurrentLiabilities  D&A  D&A_Component
IncomeTaxExpense  InterestExpense  InterestExpenseOnDebt  Inventory
OperatingCashFlow  PP&E  TotalDebt  TotalEquity  TotalEquityInclNCI
```

**这是同一个缺陷类的第七个实例**，而且系统提示词自己已经写了 `"list_metrics(ticker) is the authority on which metrics exist"`——**它承认 schema 那份不权威**，但模型在每次调用时看到的就是那份，不主动查 `list_metrics` 就不知道另外 14 个存在。

**没有证据说它导致了公式编造**（FCF 那次编造发生在 2026-05-26，那时库里确实只有 10 个 label；DSO 缺的 `AccountsReceivable` 至今也不存在）。但它确实在缩小模型看到的世界，而且是无声的。

**修**：description 改成从 `list_metrics` 的同一个来源生成，或者干脆不列举、只说"调 list_metrics 查"。**约 20 分钟**，要跑一次全量回归（改的是每次调用都会看到的文本，且会影响 prompt 缓存前缀）。**建议冻结之后做**。

### A5 — 6 家集群定义有两份，且已分叉

```
companies.CLUSTER_V1  : SWKS -> "Skyworks Solutions Inc."
ingest_xbrl.CLUSTER   : SWKS -> "Skyworks Solutions"          ← 少 "Inc."
```

其余五家一致。看着无害，**但这个字符串决定 `companies.name`，而 `slots._company_index()` 是从 `companies.name` 派生公司名索引的**——也就是昨天刚接进 ticker 守卫的那条路径。哪个管线最后跑，就由哪个决定"Skyworks Solutions Inc." 还是 "Skyworks Solutions" 是规范形式。

**修**：`ingest_xbrl` 改为 `from copilot.pipeline.companies import CLUSTER_V1 as CLUSTER`。**约 5 分钟**，无回归风险（不影响运行时路径）。

### A6 — 同一个 XBRL tag 在两个管线里映射到不同 label（数据风险）

```
tag "LongTermDebt"
   ingest_xbrl.TAG_LABELS         -> label "LongTermDebt"
   fix_financial_facts.TAG_LABELS -> label "TotalDebt"
```

库里现存两种解释**并存**：

| label | 来自 tag | 行数 |
|---|---|---|
| `TotalDebt` | `LongTermDebt` | 219 |
| `LongTermDebt` | `LongTermDebtNoncurrent` | 216 |

也就是说库里 `LongTermDebt` 这个 label 的含义是**非流动长期负债**，而 `TotalDebt` 的含义是 **LongTermDebt 这个 tag**。只要一直只跑 `fix_financial_facts` 就自洽。

**风险是重跑旧管线**：`ingest_xbrl` 再跑一次，会把 tag `LongTermDebt` 写成 label `LongTermDebt`，**和现存的 216 行（来自另一个 tag）混在同一个 label 下**，且没有任何东西会报错。而 `authority.REGISTRY` 里 `{TotalDebt, TotalEquity}` = 负债权益比正依赖这个区分。

`fix_financial_facts` 的映射表是 30 个 tag / 24 个 label（较新、较全），`ingest_xbrl` 的是 11 / 10（较旧）。

**修**：`ingest_xbrl` 从 `fix_financial_facts` 导入映射表，或把映射表提到 `pipeline/labels.py` 供两者共用。**约 20 分钟 + 需要人确认哪个映射是正确意图**——这一条**不要我单方面决定**，`LongTermDebt` 到底该叫什么是个数据建模决定。

### A4–A6 复核（同日追加）：三条里只有一条是 bug，但那一条比我写的严重

上面把 A4/A5/A6 并列成"三处分叉"，这个措辞不准确。**分叉不等于 bug**。逐条实测后：

**A5 不是 bug。** 库里 `companies.name` 是 `'Skyworks Solutions Inc.'`，而 `find_companies()` 对四种写法（含全称、去 Inc.、单词头、ticker）**全部解析成 SWKS**——它剥后缀再取首词，所以两份 CLUSTER 里哪个字符串落库都不影响解析。我原文说它"影响昨天接进 ticker 守卫的那条路径"是讲重了，实际影响接近零。仍建议合并成一份，但属于整洁性而非风险。

**A6 当前不是 bug，是潜在风险。** 查过全表：现在有 3 个 label 来自多个 tag（`COGS`、`Revenue`、`CapEx`），都是合法的同义标签映射；`LongTermDebt` **不在其中**——它只来自 `LongTermDebtNoncurrent`，`TotalDebt` 只来自 `LongTermDebt`，数据是干净的。触发条件仍然是重跑 `ingest_xbrl`。

> **后记（2026-08-26）**：A5/A6 的残余风险已结构性消除——`ingest_xbrl.py` 与 `fix_financial_facts.py` 合并为 `ingest_financial_facts.py`（正确逻辑 + `companies` upsert + 空库引导），旧的无过滤写入路径不复存在，`TAG_LABELS` 只剩一个定义点。本节其余内容保留为当时的审计记录。

**A4 是 bug，而且实测出了错误数字。**

原文写"没有证据说它导致错误"，那是因为我只测了名字**猜得出来**的 label。测猜不出来的就现形了：

```
问：Corning FY2024 的 total equity including non-controlling interests
答：$10,686,000,000  —— 原话写着 "including non-controlling interests"

模型调用：query_financials(GLW, "TotalEquity", 2024)
库里实际：TotalEquityInclNCI FY2024 = 11,070 / 11,868 / 12,275 / 12,545（百万）
```

**差 4–19 亿美元。** 第二例同形：问 "interest expense **on debt**"，模型查 `InterestExpense` 并断言那就是 on-debt 的数（AVGO 连 `InterestExpenseOnDebt` 这一行都没有）。

机制和 DSO 那次**完全一样**——看不见正确的输入，就拿名字相近的顶上并断言它是所问之物。区别在于位置：

| 防线 | 为什么拦不住 |
|---|---|
| `grounding`（数字有无出处） | 值是真取的 |
| `authority`（公式有无出处） | **没有 compute，是直接查询** |
| `misbound`（绑错公司/年份） | 公司、年份都对 |

**今天建的三道防线全部假设错误发生在计算环节，所以对查询环节的替换完全无感。**

修法不是补检查器，是**消除替换的机会**：让工具 schema 的 metric 清单从 `list_metrics` 的同一个来源生成，模型就能看见 `TotalEquityInclNCI` 存在。这条从"整洁性改进"升级为**冻结前该修**，约 20 分钟 + 一次全量回归。

### A7（新）— 同一个 (公司, label, 财年) 有多行，哪行权威没有定义

顺带露出的：GLW FY2024 的 `TotalEquity` 有 2 行、`TotalEquityInclNCI` 有 4 行，AVGO FY2024 的 `InterestExpense` 有 3 行（1,737 / 1,622 / 3,953 百万）。

`query_financials` 用 `ORDER BY period_end DESC LIMIT 1` 挑一行。同一财年内多行的 `period_end` 差异来自比较期/重述，**哪一行是"这一年的那个数"没有任何地方定义过**，也没有测试钉住。

未量化：这影响多少 (公司, label, 财年) 组合、以及现役评测题里有多少落在这种组合上。**这是下一个该测的东西**，不是下一个该修的东西。

---

## 补充：这次对账本身该变成代码

上面三条都是"跑一个脚本就能发现"的问题，而这个脚本**不存在**——我是临时写的。既然这个缺陷类已经七个实例，检查它应该是自动的。

**建议**：`tests/test_constants_match_data.py`——启动时断言：

1. `authority.REGISTRY` 的每个 label 都在 `financial_facts` 里
2. `slots._METRIC_TERMS` 的每个映射目标都在
3. `slots._EXTRA_ALIASES` / `_WORDLIKE_TICKERS` 的每个 ticker 都在 `companies` 里
4. 工具 schema 的 metric 清单 == `list_metrics` 能返回的集合
5. 两份 `TAG_LABELS` 不冲突
6. 两份 CLUSTER 相等

**约 40 分钟**，跑一次几百毫秒。这六条断言里，今天有三条会红。

它和其他测试的区别值得说明：**它测的不是代码正确，是代码和数据一致**。这类断言在纯单元测试的世界里不常见，但对一个"清单漂移"反复发生七次的项目，它是唯一能让第八次立刻现形的东西。

## B. 结构性重复，建议冻结之后做

### B1 — 三个 harness 的循环与汇总

`run_eval` / `run_eval_t3` / `run_eval_router` 是三份近似的循环：取题、调 agent、计时、累加 token、算成本、写 JSON。`harness_tier3` 和 `harness_router` **已经**从 `harness` import 了原语（`_extract_number`、`_is_refusal`、`_rates_for`、`_within_tolerance`），所以重复的不是打分逻辑，是**外壳**。

外加三份 `main()` + argparse，两份 `acc`/`_acc`（一字之差），两份 tool-trace 构建，两个 LLM judge。

**一个真实后果，不只是美观问题**：`_db_fingerprint()` 只在 `harness.py` 里。`harness_tier3` 和 `harness_router` 的结果文件**不记录自己跑在什么数据上**。CLAUDE.md 里已经把这条列为待补，而它没被补上的原因就是这三个文件各写各的。

**修**：抽 `eval/runner.py`——循环、计时、成本、指纹、JSON 落盘；三个 harness 只留各自的 `score_item`。**约 3 小时**，估计缩 250–300 行，并顺带让三个集合都有指纹。

### B2 — 四个 probe 的外壳

`probe_router` / `probe_retrieval` / `probe_truncation` / `probe_year_scope` 各有自己的 argparse、输出格式、JSON 结构。它们的**共同点**是：零 LLM、确定性、输出 before/after 对照。

**修**：一个 `probe_base`，各 probe 只提供 arms 和打分函数。**约 1.5 小时**，缩约 120 行。优先级低于 B1——probe 的独立性目前没有造成任何实际问题。

---

## C. 可以删或需要到期决定

### C1 — `eval/generate_dataset.py`，452 行，全代码库零引用

grep 过 `src/`、`views/`、`tests/`、`README.md`、`start.ps1`：**除了它自己，只有 CLAUDE.md 里一句"路径已同步更新"提到它**。没有任何代码或文档说明怎么用、什么时候用。

**建议**：删除，或移到 `scripts/` 并在文件头写清楚它是干什么的、上次跑是什么时候。**如果它生成过现役题集，那个事实必须写下来**——否则题集的来源就是不可追溯的，这对一个以"可验证"为卖点的项目是个不小的洞。

### C2 — `text_chunks.embedding_v1` 备份列

窗口池化重嵌入前的旧向量。**是 `probe_retrieval` 三臂对照的依赖**，也是回滚路径，所以不能随手删。但它需要一个到期决定：池化方案已经稳定跑了几天，回滚窗口还要留多久？

**建议**：定一个日期，到期后删列并把 `probe_retrieval` 降为两臂（或退役）。现在不动。

### C3 — `slots.find_metric` / `slots['metric']` / `slots['inherited']`

今天核实：`find_metric` **只在 `slots.py` 内部被调用一次**，用来填一个没有任何消费方的字段。`inherited` 永远是 `[]`，因为没有人传 `carry=`。

**这不是删除项，是到期检查项**——它们是为 P3 多轮继承建的，P3 排在明天。**如果 P3 被砍或推迟，这三样就是死代码，应该跟着一起撤**，不要留在代码库里当"以后会用"。

---

## D. 看着像冗余，但不是——记录理由避免以后被"顺手清理"

### D1 — `model_router.py` 只有 55 行和一个函数

看起来完全可以并进 `agent.py`。**不要并**。它的正文只有 5 行，其余 50 行是**为什么没有自动升级模型、没有按类别分档**的决策记录——那段论证（主流框架都只把换模型绑在异常上、gpt-4o 与 gpt-4o-mini 在饱和集上逐位相同）在 `agent.py` 里会被淹没，而它正是防止有人重新加回那套机制的唯一屏障。

### D2 — `grounding.py` 里四个检查看着可以合成一个循环

`unverified_numbers` / `unsourced_inputs` / `unverified_citations` / `misbound_inputs` / `unsourced_formulas` 都遍历 `steps`。合并循环能省几十行，**但它们的失败语义不同**：前四个进 `verified`（红旗），第五个刻意不进（琥珀色，交给用户判断）。合成一个循环会诱使把它们再合成一个布尔值，而那个区分是今天才刚建立的。

**可以合并的是它们共用的索引（见 A2），不是它们的判定。**

### D3 — `SUPPLIER_COLORS` 覆盖的 ticker 比库里的公司多

看着像漂移，实际是**颜色稳定性的来源**——供应商增减时已有的颜色不变。8 月 21 日已经把"有哪些供应商"从这张表迁走了（改从 API 取），留下的只是配色。这是正确状态。

---

## 如果只做三件

按"风险 ÷ 工时"排：

| # | 做什么 | 工时 | 为什么第一 |
|---|---|---|---|
| 1 | **A1 accession 正则** | 15 min | 当前活跃的不一致，且是我周一"已修复"的同一个缺陷 |
| 2 | **A3 拒答判定归位** | 40 min | 评分口径散在三处且无测试保护，最容易被下一次"顺手统一"改坏 |
| 3 | **A2 合并 trace 索引** | 1.5 h | 三套容差是本周扩张的直接产物，越晚合并分叉越深 |

**合计约 2.5 小时**，缩约 80 行，并且消掉三处会自己长大的分叉。

**不建议在冻结前做**：B1（改三个 harness 的外壳，是评测基础设施，冻结前动它意味着最终回归跑在刚改过的仪器上）、B2、C1（删 452 行需要先确认它没生成过现役题集）。

---

## 一个贯穿性的观察

这个项目最顽固的缺陷类——"一份清单从它描述的数据漂移开"——今天到了第六个实例，而**第六个是我在修第五个的同一天造成的**（A1）。

前五个：供应商下拉框建在配色表上、RRF 用文本前缀当身份、模型分档表没有类别会掉出去、`agent.MODEL` 与 `DEFAULT_MODEL` 两份拷贝、accession 正则抄了三份。

A2 是这个类的一个新变种，值得单独命名：**不是清单漂移，是索引漂移**——三份代码从同一批数据建三个索引，各自的匹配规则慢慢分家。它比清单漂移更难发现，因为三份索引都"能用"，只是在边缘情况上悄悄给出不同答案。

**下次加检查器时的规矩**：先问"这个检查需要的事实索引，现有的哪一个能提供"，不能提供就**扩展它**，而不是新建一个。

---

## A 类完成记录（2026-08-25，`afb0de3`）

| | 结果 | 与原计划的差异 |
|---|---|---|
| **A1** | 完成 | 顺带发现两个未预见的 bug：检索引用**从来没被收进** provenance；同一 filing 两种写法会重复计数。返回值改为可读的带横线形式（`accessions_in` 归一化后的 18 位数字不适合展示） |
| **A2** | 完成 | 合并为 `grounding.index_trace()` + `Fact`。全量历史 trace 复验**判定逐类不变**（同 6 个编造、同一次反转、零新增） |
| **A3** | 完成 | 顺带删掉 `"not in"`/`"outside"`，实测它把一个**正确**检索答案判成拒答 |
| **A4** | 完成 | 原文低估了。见下 |
| **A5/A6** | 完成 | **不需要人拍板**：可复现性决定了答案——库里现存的是 `fix_financial_facts` 的映射，评测集是对着这个库核实的，所以重跑必须复现它 |
| **A7** | **测量完成，不修改** | 见下 |

**A4 的修正**：原文写"没有证据说它导致错误"，那是因为只测了名字**猜得出来**的 label。测猜不出来的就现形：Corning "total equity including NCI" 被答成 `TotalEquity` 的值（差 4 亿），Broadcom "interest expense on debt" 被 `InterestExpense` 顶替。**机制与 DSO 编造相同，位置在查询环节**，所以三道答案检查全部无感。修复后两题都正确（第二题靠诚实拒答）。**代价：输入 token +15%。**

**A7 的量化结论**：4,351 个 (公司,label,财年) 组合里 **4,333 个有多行**，`period_end DESC LIMIT 1` 处处承重；4,215 个年份吻合，136 个例外全部是 MCHP FY2009–2013 与 CRUS FY2011–2013（`fiscal_year` 记的是期间**起始**年），**FY2016 及以后零个**。所有评测题在 FY2016+，故对现役系统零影响。已写成断言钉住这条边界。

**额外产出**：`tests/test_constants_match_data.py`，10 条断言全绿。它测的不是代码正确而是**代码与数据一致**，每一条在被写下之前都至少红过一次。

**修 A 类时抓到的第八个"两种读法"**：验证器无法把 `compute` 返回的小数与答案里的百分数对上，在一个**正确**的 CAGR 答案上举了红旗。`harness` 建 v2 时修过这个，验证器从来没有。已修（只对写成百分号的数字、只向下换算）。

**回归**：冻结集 Tier-1/Tier-2/input fetch/拒答 100%、总体 83.3%；Tier-3 100%；v2 92.3% 零标记；`harness_router` 100%；`probe_router` 20/20；单元测试 81。
