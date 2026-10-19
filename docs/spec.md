# 省钱管家 Bot（Money Saver Bot）产品需求文档（PRD）

| 项目 | 内容 |
|---|---|
| 文档版本 | v0.1（草案，待 Charles 评审） |
| 日期 | 2026-10-18 |
| 产品负责人 | Charles |
| 读者 | ① Charles（评审）② Developer Bot（编码 Agent，负责实现） |
| 产品形态 | 基于 Grok Bot 的 AI Agent：Gmail 连接器 + 可操控浏览器 + Bland 电话连接器 + 定时 Routine + Web Search |
| MVP 范围 | 六大模块全部纳入（负责人已决定） |

> 给 Developer Bot 的阅读说明：第 5、6、7、8 章是实现依据；所有标为 **[MUST]** 的条目是验收硬性要求，**[SHOULD]** 为强烈建议，**[MAY]** 为可选。凡与第 8 章「确认与安全规则」冲突的地方，一律以第 8 章为准。

---

## 目录
1. 背景与目标
2. 目标用户
3. 成功指标
4. 用户故事（按模块）
5. 功能需求（按模块）
6. 数据模型
7. 架构与工具映射（含 Routine/调度）
8. 确认与安全规则
9. 隐私
10. 边界情况与失败处理
11. 不在范围内（Out of Scope）
12. 里程碑（MVP 内构建顺序）
13. 待决问题

---

## 1. 背景与目标

### 1.1 市场背景（X 平台调研，2026 年 10 月）
- **真实需求已被验证**：大量用户在 X 上发帖称 Grok Bot 帮他们取消订阅，每月节省 **$300–$720+**。
- **竞品痛点（Rocket Money）**：
  - 宣称支持「App 内取消」，但对大多数订阅实际无法完成取消，也**不会代用户联系商家**；本质是「发现问题」而非「解决问题」。
  - 一条**未经核实**的帖子称：Rocket Money 将于 **11 月 2 日**终止按效果付费（pay-for-results）的账单谈判服务，并将其迁移到 **$15/月**的付费档位。若属实，这是我们获客的时间窗口（需在 13 章中核实）。
- **隐私顾虑**：用户普遍**不愿意绑定银行账户**（Plaid 类授权）。
- **免费试用陷阱**：一条病毒式传播的抱怨指出，企业依赖用户「忘记取消免费试用」来赚钱。

### 1.2 核心差异化
> **我们把事情真正办完。** 不只是列出「你可能可以省钱的地方」，而是在用户确认后，由 Bot 通过浏览器操作、打电话、提交表单来**完成取消、申请索赔、谈判降价、改签重订**，并保存可审计的证据。

| 维度 | Rocket Money | 省钱管家 Bot |
|---|---|---|
| 数据来源 | 需绑定银行账户 | **仅 Gmail 即可**；信用卡账单上传可选；**不需绑定银行** |
| 取消订阅 | 部分支持，多数需用户自己办 | 浏览器 + 电话代办，附截图/确认邮件为证 |
| 账单谈判 | 按效果抽成 / 传闻转 $15/月 | 电话代谈判（Bland），结果记入省钱账本 |
| 购前比价、购后追价、旅行、找回钱 | 无或弱 | 六大模块全覆盖 |
| 证据 | 无统一账本 | 每一笔节省都有来源 + 证据（Savings Ledger） |

### 1.3 产品目标
1. **G1 办完事**：用户确认后的行动（取消/索赔/谈判/改签）端到端完成率 ≥ 70%（见第 3 章）。
2. **G2 可量化价值**：每月向用户报告「本月为你节省 $X」，且每一笔都可追溯到证据。
3. **G3 低门槛高隐私**：仅授权 Gmail 即可获得核心价值，零银行绑定。
4. **G4 安全可信**：任何有后果的动作都先经用户明确确认；绝不未经批准以用户名义发邮件。

### 1.4 非目标
- 不做记账/预算类 App（不追踪每一笔日常消费）。
- 不做投资、贷款、信用评分建议。
- 不替用户做任何未经确认的购买或资金操作。

---

## 2. 目标用户

| 画像 | 描述 | 主要使用模块 | 关键诉求 |
|---|---|---|---|
| P1 订阅过载者 | 25–45 岁，10+ 个订阅（流媒体、App、SaaS、健身房），常忘记免费试用 | 模块 1、6 | 一次性清理 + 永不再被试用扣费 |
| P2 家庭财务管理者 | 管理全家账单（网络、手机、保险），有配偶/子女重复订阅 | 模块 1、2 | 少打电话、少花时间，重复订阅合并 |
| P3 网购高频用户 | 每月多笔 Amazon/Costco/Target/Best Buy 订单 | 模块 3、4 | 买得最便宜，降价能退差价，不错过退货期 |
| P4 常旅客 | 每年多次机票/酒店，持有航空/酒店积分 | 模块 5、6 | 降价改签拿差价/积分，延误赔偿不放过 |
| P5 隐私敏感者 | 拒绝绑定银行账户 | 全部 | 只给 Gmail 也能用；数据可删除 |

**MVP 默认用户假设**：美国用户、英语邮件为主、美元计价、拥有 Gmail 账户。UI/对话语言跟随用户（本文档为中文，但 Bot 处理的商家邮件主要为英文）。

---

## 3. 成功指标

### 3.1 北极星指标
**经核实的累计节省金额（Verified Savings, USD）**：Savings Ledger 中 `status = verified` 的记录金额之和（年化与一次性分开统计）。

### 3.2 关键指标（MVP 上线后 60 天目标）

| 指标 | 定义 | 目标 |
|---|---|---|
| 首次扫描价值 | 新用户首次 Gmail 扫描后识别出的「可节省月度金额」中位数 | ≥ $50/月 |
| 订阅识别召回率 | 人工标注样本中被正确识别的订阅比例 | ≥ 90% |
| 订阅识别精确率 | 识别出的订阅中确实为有效订阅的比例 | ≥ 85% |
| 取消完成率 | 用户确认取消 → 拿到确认证据 的比例 | ≥ 70% |
| 试用零误扣率 | 被追踪的免费试用中，用户已选择取消但仍被扣费的比例 | ≤ 2% |
| 电话谈判成功率 | 谈判类电话中拿到降价/促销的比例 | ≥ 35% |
| 索赔提交成功率 | 用户确认的索赔（价格调整/延误赔偿/和解金）成功提交的比例 | ≥ 85% |
| 月度活跃 | 每月至少确认 1 个行动的用户比例 | ≥ 40% |
| 每用户月均节省 | 已核实节省 / 活跃用户 | ≥ $60/月 |
| 安全指标 | 未经确认执行的有后果动作数 | **= 0（硬指标）** |
| 未经批准发出的邮件数 | 以用户名义发出但未获批准的邮件 | **= 0（硬指标）** |

### 3.3 质量护栏
- 误取消（取消了用户未选择的服务）= 0。
- 周报打开率 ≥ 50%；用户「关闭提醒」比例 ≤ 15%。

---

## 4. 用户故事（按模块）

格式：`US-<模块>-<序号>`：作为<角色>，我希望<能力>，以便<价值>。附验收要点。

### 模块 1：订阅与免费试用
- **US-1-1** 作为订阅过载者，我希望 Bot 扫描我的 Gmail，列出所有订阅、自动续费和免费试用（含金额、扣费日、最后使用迹象），以便我一眼看清钱花在哪。
  - 验收：扫描完成后输出清单，每项包含名称、金额、周期、下次扣费日、最后使用信号、置信度。
- **US-1-2** 作为家庭财务管理者，我希望 Bot 标记重复订阅（例如我和配偶都在付 Spotify），以便合并成家庭套餐。
- **US-1-3** 作为用户，我希望上传信用卡账单（PDF/CSV）来补全 Gmail 漏掉的订阅，且不用绑定银行。
- **US-1-4** 作为用户，我希望在免费试用扣费前 1–2 天收到提醒，并可一键回复「取消」。
- **US-1-5** 作为用户，我希望勾选要取消的订阅，Bot 通过网页或电话办完，并保存截图/确认邮件作为证据。
- **US-1-6** 作为用户，若 Bot 无法自动取消，我希望得到精确的手动步骤和直达链接。

### 模块 2：账单谈判
- **US-2-1** 作为用户，我希望 Bot 识别我的网络、手机、保险账单及当前价格。
- **US-2-2** 作为用户，我希望 Bot 先查同地区竞品报价，再打电话给运营商的挽留部门争取促销价，以便不用自己排队 1 小时。
- **US-2-3** 作为用户，我希望每次谈判前确认「底线」（例如不接受合约延长、不降速），通话后收到录音摘要和新价格。
- **US-2-4** 作为有车有房的用户，我希望每年保险续保前 30–45 天，Bot 帮我比较车险/房屋险报价。

### 模块 3：购前比价
- **US-3-1** 作为网购用户，我希望把商品链接或名称发给 Bot，获得 Amazon/Costco/Target/Best Buy/Walmart 等的实时比价（含运费、会员价）。
- **US-3-2** 作为用户，我希望得到可用优惠码（已验证或标明未验证）和返现门户（Rakuten 等）比例。
- **US-3-3** 作为用户，我希望得到「现在买 vs 等促销」的建议，附价格历史和即将到来的促销节点。
- **US-3-4** 作为用户，我希望设置目标价，到价提醒（不自动下单）。

### 模块 4：购后追回
- **US-4-1** 作为用户，我希望 Bot 从邮件自动识别订单（商家、商品、价格、下单日、退货截止日、预计送达）。
- **US-4-2** 作为用户，我希望 Bot 每天追踪已购商品价格，降价时帮我申请价格调整（price adjustment）。
- **US-4-3** 作为用户，我希望退货截止日前 3 天和 1 天收到提醒。
- **US-4-4** 作为用户，我希望延迟送达时 Bot 帮我申请补偿（如运费退款、积分）。

### 模块 5：旅行省钱
- **US-5-1** 作为常旅客，我希望 Bot 识别机票/酒店确认邮件并建立行程。
- **US-5-2** 作为用户，我希望 Bot 追踪已订机票/酒店价格，降价时提示我改签/重订拿差价（如 Southwest、Alaska 的旅行信用）。
- **US-5-3** 作为用户，我希望航班延误/取消后，Bot 判断我是否有资格获得赔偿（美国 DOT 退款规则、EU261 等）并帮我提交申请。

### 模块 6：找回钱
- **US-6-1** 作为用户，我希望每周扫描邮件找出未领取的退款、退款承诺未到账的情况。
- **US-6-2** 作为用户，我希望 Bot 匹配我可能符合资格的集体诉讼和解（class-action settlement）并帮我填表。
- **US-6-3** 作为用户，我希望 Bot 帮我查询所在州的无人认领财产（unclaimed property）。
- **US-6-4** 作为用户，我希望在礼品卡、积分、里程即将过期前收到提醒。

### 横向能力
- **US-X-1** 作为用户，我希望每周收到一份简报：待确认事项、即将扣费、即将过期、本周节省。
- **US-X-2** 作为用户，我希望每月收到「本月为你节省 $X」报告，每一笔都能点开看证据。
- **US-X-3** 作为用户，我希望所有有后果的动作都先问我，且我能随时撤销授权、删除数据。

---

## 5. 功能需求（按模块）

编号规则：`FR-<模块>-<序号>`。每条注明优先级。

### 5.0 通用基础能力（所有模块共用）

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-0-1 | **Gmail 扫描器**：通过 Gmail 连接器 `search_threads` / `get_thread` 按查询模板拉取邮件；首次回溯 **13 个月**，之后每日增量（`after:` 上次游标）。 | MUST |
| FR-0-2 | **邮件分类器**：对每封邮件输出 `{category, merchant, confidence}`，category ∈ `subscription_receipt, trial_start, renewal_notice, order_confirmation, shipping_update, delivery, refund, travel_booking, travel_change, bill, insurance_policy, gift_card, loyalty_statement, settlement_notice, other`。 | MUST |
| FR-0-3 | **结构化抽取**：从邮件正文抽取金额、币种、日期、周期、订单号、确认号等，写入第 6 章实体；每个字段保留 `source_message_id`。 | MUST |
| FR-0-4 | **商家归一化**：维护 `merchant_registry`（名称别名、域名、取消 URL、客服电话、取消方式、价格调整政策），如 `netflix.com`、`Netflix Inc`、`NFLX` → `Netflix`。 | MUST |
| FR-0-5 | **确认网关（Confirmation Gate）**：所有有后果动作必须先生成 `ActionRequest`，经用户明确确认后才可执行（详见第 8 章）。 | MUST |
| FR-0-6 | **证据采集**：每个执行动作必须保存证据（截图、确认邮件 message_id、通话录音/转写、提交回执），存入 `Evidence`。 | MUST |
| FR-0-7 | **省钱账本写入**：每个成功动作在 `SavingsLedger` 记一笔，status 初始为 `pending`，在出现可核实证据（确认邮件、退款到账邮件、新账单金额）后改为 `verified`。 | MUST |
| FR-0-8 | **信用卡账单解析（可选）**：接受用户上传 PDF/CSV，使用 poppler `pdftotext` / CSV 解析抽取交易（日期、商户描述、金额），只保留疑似周期性扣费与相关交易，原文件解析后按第 9 章规则删除。 | MUST |
| FR-0-9 | **通知通道**：MVP 通过 Grok Bot 对话消息推送；邮件通知仅发送到用户本人邮箱（发给用户自己不需要额外批准，但不得抄送他人）。 | MUST |

### 5.1 模块 1：订阅与免费试用

**输入**：Gmail；可选信用卡账单。
**输出**：订阅清单、重复标记、试用提醒、取消执行与证据。

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-1-1 | 使用预置查询模板扫描，例如：`subject:(receipt OR invoice OR "your subscription" OR renewal OR "free trial" OR "trial ends" OR membership OR "auto-renew")`、`from:(apple.com OR google.com OR paypal.com OR stripe.com)` 等；模板存为配置，可迭代。 | MUST |
| FR-1-2 | 对 App Store / Google Play / PayPal / Stripe 等**聚合收据**拆分出真实底层服务（例如 Apple 收据中的 “Calm Premium”）。 | MUST |
| FR-1-3 | 推断周期：同一商家 ≥2 次扣费时根据间隔推断 `weekly/monthly/quarterly/annual`；仅 1 次时依据邮件文字（“per month”、“annual plan”）。 | MUST |
| FR-1-4 | 计算 `next_charge_date`：取最近一次扣费 + 周期，或邮件中明确的续费/试用结束日期（优先级更高）。 | MUST |
| FR-1-5 | **最后使用信号**（`last_used_signal`）：从邮件推断，如登录提醒、“你本周观看了…”、“我们想念你”(win-back) 邮件、活动摘要；输出 `active / likely_unused / unknown` 及依据。不得声称确定，需标注「推断」。 | MUST |
| FR-1-6 | **重复检测**：规则：(a) 同一商家出现 ≥2 个活跃订阅（不同账户邮箱/不同卡尾号）；(b) 功能重叠类别（如 ≥3 个视频流媒体、2 个云存储）；(c) 用户声明的家庭成员邮箱（可选配置）中出现相同商家。输出建议，如「合并为 Spotify Family 可省 $X/月」。 | MUST |
| FR-1-7 | **免费试用追踪**：识别 `trial_start` 邮件，抽取试用结束日与转正价格；若无明确结束日，按常见时长（7/14/30 天）推断并标注低置信度，提示用户核实。 | MUST |
| FR-1-8 | **试用提醒**：在 `trial_end - 2 天` 和 `trial_end - 1 天`（用户时区上午 9:00）推送提醒，选项：`[取消] [保留] [下次再提醒]`。 | MUST |
| FR-1-9 | **用户预授权取消**：用户可对某试用设置「到期前自动帮我取消」，此即对该具体动作的明确确认；Bot 在 `trial_end - 1 天` 执行，执行前再推送一次「将在 2 小时后取消，回复停止可撤销」。 | MUST |
| FR-1-10 | **取消执行 – 浏览器路径**：按 `merchant_registry.cancel_url` 打开浏览器；若需登录，提示用户在 Bot 的浏览器中自行登录（Bot 不索要、不存储密码）；逐步执行取消流程；遇到挽留优惠时**不接受**，而是记录优惠内容并询问用户（挽留优惠可能本身就是节省）。 | MUST |
| FR-1-11 | **取消执行 – 电话路径**：无网页取消或网页失败时，经用户确认后通过 Bland 拨打商家客服；通话脚本含：身份信息（仅用户已批准提供的字段）、取消请求、要求确认号、要求确认邮件。 | MUST |
| FR-1-12 | **取消执行 – 邮件路径**：若商家仅支持邮件取消，Bot 起草邮件，**必须经用户审批后**才能以用户名义发送。 | MUST |
| FR-1-13 | **证据**：取消完成页截图（全页）、确认号、确认邮件（执行后 48 小时内监听 Gmail 中来自该商家的取消确认邮件并关联）。48 小时未收到确认邮件 → 状态 `cancel_unconfirmed`，提示用户。 | MUST |
| FR-1-14 | **兜底手动指南**：自动取消失败时，输出逐步手动步骤 + 直达链接 + 电话号码 + 话术。 | MUST |
| FR-1-15 | **扣费后核实**：在原 `next_charge_date + 3 天` 扫描邮件/已上传账单，若仍有扣费 → 标记 `charged_after_cancel`，建议申请退款（生成 ActionRequest）。 | SHOULD |
| FR-1-16 | 订阅价格上涨检测：同一订阅金额上涨时提醒用户并给出替代方案。 | SHOULD |

**节省计算**：取消节省 = 月化金额 × 12（年化，`type=recurring`），并单独记录「本月实际避免扣费」金额。试用取消节省 = 转正后首期价格（一次性）+ 月化 × 12（若用户本会继续付费，标为 `estimated`）。

### 5.2 模块 2：账单谈判

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-2-1 | 识别账单类别：`internet, mobile, tv, insurance_auto, insurance_home, insurance_renters, utilities(仅识别不谈判)`；抽取提供商、月费、账户尾号、合约/续保日期、服务地址（仅邮编级别用于比价）。 | MUST |
| FR-2-2 | **竞品调研**：用 Web Search + 浏览器查询用户邮编下的竞品报价与该提供商的新客促销价，生成「谈判筹码表」（竞品名称、价格、速度/套餐、来源 URL、获取时间）。 | MUST |
| FR-2-3 | **谈判前确认**：生成 ActionRequest，包含：要拨打的号码、可披露的个人信息字段清单、目标价、底线（不可接受项：合约延长、降速、取消必要服务等）、是否授权「若不降价则威胁取消/实际取消」（默认仅允许「表达考虑取消」，不允许实际取消）。 | MUST |
| FR-2-4 | **通话执行**：通过 Bland `create_call` 发起，使用谈判 Pathway：开场 → 身份验证 → 请求转挽留部门 → 陈述竞品报价 → 争取促销 → 确认新价格/生效日/期限/有无合约 → 要求书面确认。`wait_for_call` 等待结束，`get_call_log` 获取转写。 | MUST |
| FR-2-5 | **身份验证限制**：若提供商要求 SSN 全号、完整卡号、账户 PIN 等 Bot 未获授权的信息 → 立即请求用户加入三方通话或改为「用户自行致电 + Bot 提供话术」，不得猜测或编造。 | MUST |
| FR-2-6 | **不得在通话中接受新条款**：凡涉及合约延长、套餐变更，Bot 只能说「我需要和账户持有人确认」，挂断后推送给用户确认，再回拨或由用户自行确认。例外：用户在 ActionRequest 中预先批准的明确条款（如「≤$55/月且无合约即可接受」）。 | MUST |
| FR-2-7 | **结果记录**：通话摘要、新价格、生效日期、促销期限（如 12 个月）、确认号；在促销到期前 30 天创建提醒以再次谈判。 | MUST |
| FR-2-8 | **核实**：下一期账单邮件到达后核对新金额，一致则账本 `verified`。 | MUST |
| FR-2-9 | **保险年度重新比价**：在保单续保日前 45 天触发；收集当前保单关键要素（从保单邮件/用户上传的声明页 declarations page 抽取：保额、免赔额、车辆、驾驶人），用浏览器在比价站/保险公司官网获取报价。需要填写个人信息的报价表单，必须先列出将提交的字段并获得确认。 | MUST |
| FR-2-10 | 保险比较输出：同保障对比表（保费、免赔额、保额、评级），明确标注「保障不完全一致」的差异。Bot **不代为购买或更换保单**，只提供报价与链接（购买为 Out of Scope，见 11 章）。 | MUST |

### 5.3 模块 3：购前比价

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-3-1 | 输入：商品 URL、商品名称、型号/UPC，或截图。先标准化为 `{brand, model, variant, UPC/ASIN 如有}`。 | MUST |
| FR-3-2 | 零售商覆盖（MVP）：Amazon、Walmart、Target、Best Buy、Costco、Home Depot、商品品牌官网；用浏览器读取价格、库存、运费、会员价（Prime/Costco 会员/Target Circle）。 | MUST |
| FR-3-3 | **同款校验**：比较型号、容量、颜色、套装内容；不一致的标注「非完全同款」，不得混入最低价。 | MUST |
| FR-3-4 | **优惠码**：Web Search 获取候选码，标注来源与「未验证」；MVP 不自动在结账页试码（避免触达结账流程），可选 [MAY] 在购物车页面验证但不下单。 | MUST |
| FR-3-5 | **返现门户**：查询 Rakuten、TopCashback、信用卡商户优惠（仅用户告知其持有卡时）的返现比例。 | SHOULD |
| FR-3-6 | **现在买还是等**：结合价格历史（camelcamelcamel/Keepa 等公开页面，若可访问）、即将到来的促销节点（Prime Day、黑五、返校季等）、新品发布周期，输出建议 + 理由 + 置信度。 | MUST |
| FR-3-7 | **目标价追踪**：用户设定目标价，创建 `PriceTracker(kind=wishlist)`，每日检查，到价通知。**不自动下单。** | MUST |
| FR-3-8 | 结果展示：表格（零售商、到手价=价格+运费+税估算−会员折扣、返现、链接、抓取时间）。 | MUST |

### 5.4 模块 4：购后追回

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-4-1 | 从订单确认邮件抽取 `Order`：商家、订单号、商品行（名称、数量、单价、SKU/ASIN、URL）、下单日期、支付总额、预计送达日期；从发货/送达邮件更新物流状态与实际送达日期。 | MUST |
| FR-4-2 | 根据 `merchant_registry` 中的退货政策计算 `return_deadline`（如送达后 30 天；节假日延长政策）；若无法确定，标注 `unknown` 并提示用户。 | MUST |
| FR-4-3 | **退货期提醒**：截止前 3 天、1 天推送。 | MUST |
| FR-4-4 | **每日价格追踪**：对价格政策允许调价的商家/在退货期内的商品创建 `PriceTracker(kind=post_purchase)`，每日一次（用户时区 06:00）浏览器抓取价格，追踪至 `max(价格保护期, 退货截止日)`。 | MUST |
| FR-4-5 | **降价处理**：降幅 ≥ max($5, 5%)（可配置）时，生成选项：(a) 商家价格调整（若政策支持，如 Target、Best Buy 等，以 registry 为准）；(b) 退货重买（仅退货期内，说明麻烦程度）；(c) 信用卡价格保护（若用户告知持有支持的卡）。 | MUST |
| FR-4-6 | **价格调整执行**：用户确认后，通过商家在线客服聊天（浏览器）/ 表单 / 电话（Bland）提交；需发邮件的，起草后经用户批准发送。保存证据（聊天截图、案例号）。 | MUST |
| FR-4-7 | **延迟送达补偿**：实际送达晚于承诺日期（或超过承诺 2 天未送达）时，检查商家/会员政策（如保证送达日期的运费退款），生成索赔 ActionRequest。 | MUST |
| FR-4-8 | 退款到账核实：监听 Gmail 退款邮件，匹配金额后账本 `verified`。 | MUST |

### 5.5 模块 5：旅行省钱

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-5-1 | 从确认邮件抽取 `Trip` 和 `Booking`：航司/酒店、确认号(PNR)、乘客/入住人姓名、航段（航班号、日期、出发/到达机场、舱位/票价类别）、酒店（地址、入住/退房、房型、是否可免费取消及截止时间）、总价、支付方式（现金/积分）。 | MUST |
| FR-5-2 | **票价规则库**：在 `merchant_registry` 中维护航司改签规则，如 Southwest（无改签费、差价退为旅行信用）、Alaska（主舱无改签费、差价退为信用）、以及 Basic Economy 不可改等。基础经济舱等不可改票价不建追踪器。 | MUST |
| FR-5-3 | **价格追踪**：可改签机票每日检查 1 次（出发前 7 天内每日 2 次）同航班同票价类别价格；可免费取消的酒店每日检查同房型同日期价格（官网 + 主要 OTA，仅作参考）。 | MUST |
| FR-5-4 | **降价提示**：净节省（差价 − 改签费 − 信用使用限制折算）≥ $20（可配置）时，推送 ActionRequest，说明退回形式（现金/旅行信用/积分）及信用有效期。 | MUST |
| FR-5-5 | **执行改签/重订**：用户确认后，浏览器登录用户航司账户（用户自行登录）执行同航班改签；酒店需「先订新的再取消旧的」：必须在一个 ActionRequest 中明确列出两步，且新预订必须为可免费取消；任何一步失败立即停止并报告。 | MUST |
| FR-5-6 | **延误/取消赔偿**：监听航司变更/延误/取消邮件；结合公开航班状态数据判断：美国航班取消或重大变更时 DOT 规定的现金退款权利；欧盟相关航班 EU261 赔偿（€250–€600）；航司延误补偿政策（餐券、里程）。生成索赔 ActionRequest，提交航司在线表单。 | MUST |
| FR-5-7 | 旅行信用追踪：记录 `TravelCredit`（金额、到期日、使用限制），到期前 60/30/7 天提醒。 | MUST |

### 5.6 模块 6：找回钱

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-6-1 | **未到账退款**：从邮件匹配「退款已发起/将退款」与实际退款确认；发起后 14 天未见到账 → 提醒并建议跟进（生成 ActionRequest）。 | MUST |
| FR-6-2 | **集体诉讼和解匹配**：每周 Web Search 公开和解列表（如 topclassactions.com、classaction.org 等公开页面），抽取：被告、资格条件、截止日期、是否需要凭证、预计金额、官方理赔网站。与用户邮件/订阅/订单历史中的商家匹配（例如用户邮件中有该公司的账户邮件）。 | MUST |
| FR-6-3 | **和解申请**：仅针对用户确认符合资格的和解；填写表单前展示全部将提交的字段；**Bot 不得替用户声明不真实的资格**，涉及「宣誓/伪证处罚」(under penalty of perjury) 声明的表单，必须由用户本人逐条确认资格陈述。只提交官方理赔网站（URL 必须来自和解公告或法院文件，防钓鱼）。 | MUST |
| FR-6-4 | **州无人认领财产**：用户提供姓名与曾居住的州（可多个），Bot 通过浏览器查询各州官方网站（或 MissingMoney.com 官方合作站点），展示结果；提交认领需用户确认，并说明可能需要的证明材料。 | MUST |
| FR-6-5 | **过期提醒**：从邮件中识别礼品卡（余额、卡号后4位、到期）、积分/里程余额与到期（航空、酒店、信用卡、零售会员）；到期前 60/30/7 天提醒，并给出「最低成本保活」方法（如一笔小额活动）。 | MUST |
| FR-6-6 | 每周扫描（与周报同一 Routine 前置运行）。 | MUST |

### 5.7 横向功能：周报、月报、省钱账本

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-X-1 | **周报**：每周日 18:00（用户时区）。内容：① 待你确认的行动（最多 5 条，按金额排序）② 未来 14 天的扣费与试用到期 ③ 即将到期（退货期、积分、礼品卡、旅行信用）④ 本周已完成行动与节省 ⑤ 本周新发现。长度控制在一屏内，提供「查看全部」。 | MUST |
| FR-X-2 | **月报「本月为你节省 $X」**：每月 1 日 09:00。拆分：已核实（verified）vs 待核实（pending）；一次性 vs 年化循环；按模块分组；每笔附来源与证据链接。累计节省（自开通以来）。 | MUST |
| FR-X-3 | **账本规则**：仅 `verified` 计入月报标题金额；`estimated`（例如「若未取消本会继续付费」）单独展示，不与已核实合并；同一节省不得重复计数（按 `dedupe_key`）。 | MUST |
| FR-X-4 | **账本导出**：用户可导出 CSV（日期、模块、商家、类型、金额、状态、证据链接）。 | SHOULD |
| FR-X-5 | **对话式查询**：用户可问「我现在有哪些订阅？」「这个月省了多少？」「Netflix 取消了吗，证据呢？」 | MUST |

---

## 6. 数据模型

存储：MVP 采用单用户 SQLite（或等价的 JSON 文件存储），位于 Bot 持久化目录，例如 `/workspace/money-saver/data/msb.db`；证据文件存于 `/workspace/money-saver/evidence/<entity>/<id>/`。所有金额以**整数美分**存储（`amount_cents`），币种 `currency` 默认 `USD`。所有时间以 UTC ISO 8601 存储，展示时转用户时区。

### 6.1 实体关系概览
```
User 1─* Subscription ─* Charge
User 1─* Order 1─* OrderItem ─* PriceTracker
User 1─* Trip 1─* Booking ─* PriceTracker
User 1─* Bill
User 1─* Claim
User 1─* Reminder
User 1─* ActionRequest 1─1 ActionRun ─* Evidence
SavingsLedgerEntry *─1 (Subscription|Order|Booking|Bill|Claim) ，*─* Evidence
SourceMessage（Gmail 邮件索引）被所有实体以 source_message_ids 引用
```

### 6.2 User / Settings
| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 主键 |
| timezone | string | 默认 `America/Los_Angeles` |
| gmail_accounts | string[] | 已授权的 Gmail 连接（可多个） |
| household_emails | string[] | 可选，家庭成员邮箱（用于重复检测，仅比对，不扫描他人邮箱） |
| home_zip | string | 可选，用于比价/谈判 |
| states_lived | string[] | 可选，用于无人认领财产 |
| cards_held | {issuer, product, last4}[] | 可选，用于价格保护/返现；仅存后 4 位 |
| approved_pii_fields | string[] | 用户允许 Bot 在通话/表单中披露的字段（如 name, service_address, account_last4） |
| thresholds | json | `price_drop_min_cents`, `price_drop_min_pct`, `travel_min_saving_cents` 等 |
| notification_prefs | json | 免打扰时段、周报开关 |
| scan_cursor | json | 每个 Gmail 账户的最后扫描时间 |

### 6.3 Subscription
| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | |
| merchant_id | string | 关联 merchant_registry |
| display_name | string | 如 “Netflix Standard” |
| billing_channel | enum | `direct, apple, google_play, paypal, amazon, carrier, other` |
| account_email | string | 订阅所用邮箱 |
| amount_cents | int | 当前每期金额 |
| currency | string | |
| period | enum | `weekly, monthly, quarterly, semiannual, annual, unknown` |
| monthly_equiv_cents | int | 月化金额 |
| status | enum | `active, trial, cancel_requested, cancel_in_progress, cancelled, cancel_unconfirmed, charged_after_cancel, paused, expired, ignored` |
| is_trial | bool | |
| trial_end_date | date? | |
| trial_converts_to_cents | int? | 转正价格 |
| next_charge_date | date? | |
| last_charge_date | date? | |
| last_used_signal | enum | `active, likely_unused, unknown` |
| last_used_evidence | string | 推断依据（邮件主题 + 日期） |
| duplicate_group_id | string? | 重复订阅分组 |
| auto_cancel_before_trial | bool | 用户预授权（FR-1-9） |
| user_decision | enum | `none, keep, cancel, remind_later` |
| confidence | float | 0–1 |
| source | enum[] | `gmail, card_statement, user_added` |
| source_message_ids | string[] | |
| created_at / updated_at | datetime | |

**Charge**（扣费记录）：`id, subscription_id?, date, amount_cents, merchant_raw, source(gmail|card_statement), source_ref, card_last4?`

### 6.4 Bill
`id, category(internet|mobile|tv|insurance_auto|insurance_home|insurance_renters|utility), provider, account_last4, monthly_cents, plan_desc, contract_end_date?, renewal_date?, promo_end_date?, last_negotiated_at?, status(active|negotiating|negotiated|switched), source_message_ids`

### 6.5 Order / OrderItem
**Order**：`id, merchant_id, order_number, order_date, total_cents, payment_last4?, ship_status(ordered|shipped|delivered|delayed|returned|cancelled), promised_delivery_date?, actual_delivery_date?, return_deadline?, return_deadline_confidence, price_protection_until?, source_message_ids`

**OrderItem**：`id, order_id, title, sku_or_asin?, product_url?, qty, unit_price_cents, returned(bool)`

### 6.6 Trip / Booking / TravelCredit
**Trip**：`id, name, start_date, end_date`
**Booking**：`id, trip_id, kind(flight|hotel|car), vendor_id, confirmation_code, travelers[], segments(json: flight_no, dep, arr, dep_time, fare_class), hotel(json: property, room_type, check_in, check_out, free_cancel_until), total_cents, paid_with(cash|points|mixed), points_amount?, changeable(bool), change_fee_cents, status(booked|changed|cancelled|completed), source_message_ids`
**TravelCredit**：`id, vendor_id, amount_cents, expires_on, restrictions, origin_booking_id?, status(active|used|expired)`

### 6.7 PriceTracker
| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | |
| kind | enum | `wishlist, post_purchase, flight, hotel` |
| target_ref | string | OrderItem / Booking / 商品标准化 ID |
| urls | {retailer, url}[] | 抓取地址 |
| baseline_cents | int | 购买价/当前预订价 |
| target_cents | int? | 目标价（wishlist） |
| last_price_cents | int? | |
| lowest_price_cents | int? | |
| price_history | {ts, retailer, price_cents, in_stock}[] | |
| check_schedule | enum | `daily, twice_daily` |
| active_until | date | 截止追踪日期 |
| status | enum | `active, triggered, paused, expired, error` |
| consecutive_errors | int | 连续失败次数（≥3 暂停并通知） |

### 6.8 Claim（索赔/申请）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | |
| type | enum | `price_adjustment, late_delivery, refund_followup, flight_compensation, travel_rebook_diff, class_action, unclaimed_property, charge_after_cancel_refund, other` |
| counterparty | string | 商家/航司/和解管理方/州政府 |
| related_ref | string? | Order / Booking / Subscription |
| expected_amount_cents | int? | |
| awarded_amount_cents | int? | |
| deadline | date? | 申请截止日 |
| channel | enum | `web_form, chat, phone, email, mail` |
| case_number | string? | |
| status | enum | `identified, awaiting_user, submitted, acknowledged, approved, denied, paid, expired, abandoned` |
| submitted_at | datetime? | |
| follow_up_at | datetime? | 下次跟进时间 |
| evidence_ids | string[] | |

### 6.9 ActionRequest / ActionRun（确认网关）
**ActionRequest**
| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | |
| action_type | enum | `cancel_subscription, negotiate_bill, place_call, submit_claim, send_email, rebook_travel, submit_form_with_pii, purchase(MVP 禁用)` |
| target_ref | string | 关联实体 |
| summary | string | 一句话说明，例如「通过网页取消 Hulu（$17.99/月），预计年省 $215.88」 |
| plan_steps | string[] | 将执行的具体步骤 |
| disclosed_fields | {field, value_masked}[] | 将披露给第三方的信息 |
| constraints | json | 底线、预批准条款 |
| draft_content | string? | 邮件草稿/通话脚本 |
| expected_saving_cents | int? | |
| risk_notes | string | 如「取消后将丢失观看记录」 |
| status | enum | `pending_user, approved, rejected, expired, executed, failed, cancelled_by_user` |
| approved_at / approved_by_message | datetime / string | 用户确认的原始消息引用 |
| expires_at | datetime | 默认 72 小时；试用类为 trial_end 前 |

**ActionRun**：`id, action_request_id, started_at, finished_at, executor(browser|bland_call|email|manual), outcome(success|partial|failed|needs_user), error_code?, log(json), evidence_ids[]`

### 6.10 Evidence
`id, kind(screenshot|confirmation_email|call_transcript|call_recording_url|form_receipt|chat_transcript|statement_line), path_or_ref, sha256, captured_at, related_ref, notes`

### 6.11 SavingsLedgerEntry（省钱账本）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | |
| module | enum | `subscriptions, bills, pre_purchase, post_purchase, travel, recovery` |
| saving_type | enum | `recurring`（循环）, `one_time`（一次性） |
| description | string | 例「取消 Hulu」 |
| merchant_id | string | |
| amount_cents | int | 一次性金额，或循环的**每月**金额 |
| annualized_cents | int? | 循环节省的年化值 |
| form | enum | `cash, statement_credit, travel_credit, gift_card, points` |
| points_amount | int? | 积分/里程数量（不折现计入标题金额，单独展示） |
| status | enum | `estimated, pending, verified, reversed` |
| verification_ref | string? | 核实证据（如退款邮件 id、新账单） |
| evidence_ids | string[] | |
| source_ref | string | 来源实体（Subscription/Claim/...） |
| action_run_id | string? | |
| dedupe_key | string | 如 `cancel:<subscription_id>` |
| occurred_at | date | |
| verified_at | datetime? | |

**计入规则**：
- 取消订阅：`recurring`，`amount_cents` = 月化金额；月报中「本月节省」按当月本应扣费的金额计入，从取消起最多计 12 个月。
- 谈判：`recurring`，金额 = 旧月费 − 新月费，计入期 = 促销期（最多 12 个月）。
- 价格调整/退款/赔偿/和解/无人认领财产：`one_time`，到账后 `verified`。
- 购前比价：节省 = 用户所选默认渠道价格 − 实际推荐成交价，仅当用户告知已按推荐购买时记录为 `pending`，有订单邮件后 `verified`。
- 旅行信用：`form=travel_credit`，月报中单列，不与现金合并到标题金额（标题金额可选显示「含信用」开关）。

### 6.12 MerchantRegistry（配置数据）
`id, name, aliases[], email_domains[], category, cancel_method(web|phone|email|chat|app_store|google_play), cancel_url?, support_phone?, cancel_notes, retention_offer_known(bool), price_adjust_policy(json: allowed, window_days, channel), return_policy(json: window_days, holiday_rules), late_delivery_policy?, fare_rules?(航司), updated_at, source_urls[]`
- MVP 预置 Top 150 订阅商家 + 主要零售商 + 主要美国航司/酒店集团 + Top 10 网络/运营商/保险公司。
- 缺失商家：运行时通过 Web Search 补全并标记 `unverified`。

### 6.13 Reminder
`id, kind(trial_end|return_deadline|credit_expiry|points_expiry|gift_card_expiry|promo_end|insurance_renewal|claim_deadline|follow_up), target_ref, fire_at, status(scheduled|sent|dismissed|snoozed), snooze_until?`

---

## 7. 架构与工具映射

### 7.1 能力清单
| 能力 | 具体工具/连接器 | 用途 |
|---|---|---|
| Gmail 读取 | Gmail 连接器：`search_threads`, `get_thread`, `get_message` | 扫描订阅、订单、旅行、账单、退款、和解通知 |
| Gmail 草稿/发送 | Gmail 连接器草稿 + 用户审批后发送（经 Grok Bot 的外发消息审批流程） | 仅在用户逐封批准后以用户名义发送（取消邮件、调价请求、索赔邮件） |
| Gmail 标签 | `create_label`, `label_thread` | [MAY] 给已处理邮件打 `MoneySaver/*` 标签（需用户在设置中开启，默认关闭） |
| 浏览器 | Bot 自有桌面浏览器（可操控） | 取消流程、抓价格、提交表单、客服聊天、改签、截图取证 |
| 电话 | Bland 连接器：`create_call`, `wait_for_call`, `get_call_log`, `stop_call`；Pathway/Agent 配置 | 取消电话、账单谈判、价格调整、跟进 |
| Web Search / Fetch | WebSearch, WebFetch | 竞品报价、优惠码、和解列表、商家政策、航班状态 |
| 定时 Routine | Grok Bot 定时任务 | 每日扫描、价格追踪、提醒、周报、月报 |
| 文件处理 | Box Shell（poppler、Python） | 信用卡账单 PDF/CSV 解析、账本存储、CSV 导出 |
| 通知 | Grok Bot 对话消息；可选发到用户本人邮箱 | 提醒、确认请求、报告 |

### 7.2 模块 × 能力矩阵
| 模块 | Gmail 读 | Gmail 发(需批准) | 浏览器 | Bland 电话 | Web Search | Routine | 文件解析 |
|---|---|---|---|---|---|---|---|
| 1 订阅与试用 | ●核心 | ○（邮件取消） | ●取消 | ●取消兜底 | ○补全商家信息 | ●每日扫描/提醒 | ○账单上传 |
| 2 账单谈判 | ●识别账单 | ○ | ●竞品报价/保险报价 | ●核心 | ●竞品报价 | ●促销到期/续保 | ○声明页 |
| 3 购前比价 | – | – | ●核心 | – | ●优惠码/返现 | ○目标价追踪 | – |
| 4 购后追回 | ●订单识别 | ○调价邮件 | ●抓价/客服聊天 | ○调价电话 | ○政策查询 | ●每日抓价/提醒 | – |
| 5 旅行 | ●行程识别 | ○索赔邮件 | ●抓价/改签/索赔表单 | ○航司电话 | ●航班状态/规则 | ●每日抓价 | – |
| 6 找回钱 | ●退款/礼品卡/积分 | ○跟进邮件 | ●州政府网站/和解表单 | ○ | ●和解列表 | ●每周扫描 | – |

（● 主要依赖，○ 次要/兜底，– 不使用）

### 7.3 Routine / 调度表（用户时区）
| Routine ID | 频率 | 时间 | 内容 |
|---|---|---|---|
| R1 `daily_email_scan` | 每日 | 05:30 | 增量 Gmail 扫描 → 分类 → 抽取 → 更新实体；处理确认邮件以核实账本 |
| R2 `daily_price_check` | 每日 | 06:00 | 遍历 `PriceTracker.status=active`（post_purchase、wishlist、hotel、flight） |
| R2b `flight_price_check_pm` | 每日 | 18:00 | 出发 7 天内的航班第二次检查 |
| R3 `reminder_dispatch` | 每小时 | 整点 | 发送到期 Reminder（尊重免打扰 22:00–08:00，试用/退货类提醒统一在 09:00 发送） |
| R4 `trial_guard` | 每日 | 09:00 | 试用 T-2/T-1 提醒；执行已预授权的到期前取消（含 2 小时撤销窗口） |
| R5 `post_action_verify` | 每日 | 07:00 | 检查 `cancel_unconfirmed`、扣费后核实、退款到账、谈判后新账单 |
| R6 `weekly_recovery_scan` | 每周日 | 16:00 | 模块 6：和解列表更新与匹配、退款跟进、积分/礼品卡到期 |
| R7 `weekly_digest` | 每周日 | 18:00 | 周报 |
| R8 `monthly_report` | 每月 1 日 | 09:00 | 月报 + 账本 |
| R9 `annual_reviews` | 每日检查 | 05:45 | 保险续保前 45 天、促销到期前 30 天触发谈判/比价 ActionRequest |
| R10 `action_request_expiry` | 每小时 | – | 过期未确认请求置为 `expired`，必要时（如试用）发最后提醒 |

**并发与限流**：浏览器任务串行队列（单浏览器实例）；同一零售商抓取间隔 ≥ 10 秒；每日价格检查总量上限 200 个 URL/用户；Bland 通话同一时间最多 1 通，单日最多 5 通（可配置）；营业时间外不拨打（按商家时区 9:00–17:00 或 registry 中的客服时间）。

### 7.4 组件划分（建议代码结构）
```
money-saver/
  config/
    merchant_registry.yaml      # 商家配置
    gmail_queries.yaml          # 扫描查询模板
    call_scripts/               # Bland pathway 脚本：cancel.yaml, negotiate_internet.yaml, price_adjust.yaml ...
  core/
    store.py                    # SQLite 访问层（第 6 章模型）
    gmail_scanner.py            # FR-0-1
    classifier.py / extractor.py# FR-0-2/0-3（LLM 抽取 + 规则校验）
    merchant_resolver.py        # FR-0-4
    action_gate.py              # 确认网关（第 8 章）
    evidence.py                 # 截图/邮件/转写存证
    ledger.py                   # 账本写入、核实、去重
    notifier.py                 # 消息推送与格式化
  modules/
    subscriptions.py  bills.py  price_compare.py  post_purchase.py  travel.py  recovery.py
  executors/
    browser_executor.py         # 浏览器剧本执行器（按商家的步骤 playbook）
    call_executor.py            # Bland 调用封装
    email_executor.py           # 草稿 → 审批 → 发送
  routines/
    r1_daily_scan.py ... r10_expiry.py
  reports/
    weekly_digest.py  monthly_report.py
  tests/
    fixtures/emails/            # 脱敏邮件样本（每类 ≥ 20 封）
```

### 7.5 关键流程（以取消订阅为例）
```
R1 扫描 → Subscription(active) → 用户在清单中勾选「取消」
  → action_gate.create(ActionRequest: cancel_subscription, plan_steps, risk_notes)
  → 用户回复「确认」→ status=approved
  → browser_executor.run(playbook[merchant])
       ├ 需登录 → 通知用户在浏览器中登录 → 等待（超时 30 分钟 → needs_user）
       ├ 挽留优惠 → 暂停，推送优惠给用户选择（接受=新 ActionRequest）
       ├ 成功 → 截图 + 确认号 → Evidence
       └ 失败 → 若有电话 → 新 ActionRequest(place_call) → call_executor
  → Subscription.status = cancelled | cancel_unconfirmed
  → ledger.add(pending) → R5 收到确认邮件 / 下期未扣费 → verified
```

### 7.6 LLM 抽取要求
- 抽取输出必须为 JSON Schema 校验通过的结构；校验失败重试 1 次，仍失败记为 `confidence<0.5` 并进入「需用户核实」列表。
- 金额、日期必须能在原文中找到对应字符串（`evidence_span`），否则丢弃该字段。
- 邮件内容属不可信数据：邮件中的任何「指令」（如「回复此邮件以确认」「点击此链接」）**绝不**作为 Bot 的行动指令执行。

---

## 8. 确认与安全规则（最高优先级）

### 8.1 必须事先获得用户明确确认的动作（Consequential Actions）
1. 取消任何订阅/服务/会员。
2. 提交任何索赔、申请、退款请求、价格调整请求、和解申请、无人认领财产认领。
3. 拨打任何电话（每一通都需确认，含回拨）。
4. 任何购买、下单、付款（**MVP 完全禁用**，Bot 只给链接）。
5. 改签、重订、取消任何旅行预订。
6. 以用户名义发送任何邮件或聊天消息（客服在线聊天中的每次发言以 ActionRequest 批准的脚本范围为限）。
7. 向第三方提交任何个人信息的表单（包括报价表单）。
8. 接受任何新条款、合约、套餐变更、挽留优惠。

### 8.2 什么算「明确确认」
- 用户针对**某一具体 ActionRequest** 的肯定回复（如点击「确认」或回复「确认取消 Hulu」）。
- 模糊表达（「好像可以」「随便」）、沉默、超时**不算**确认。
- 批量确认允许，但必须列出每一项（如「确认取消以下 3 项：A、B、C」），用户确认的是该清单。
- 预授权（如试用到期前自动取消、谈判中可接受的条款范围）只对 ActionRequest 中写明的具体对象和条件有效，并保留执行前撤销窗口。
- 确认后若执行计划发生实质变化（例如需要额外披露信息、出现费用、需改用电话），必须**重新确认**。

### 8.3 绝对禁止
- 未经批准以用户名义发送任何邮件（硬指标 = 0）。
- 索要、存储或在日志中记录用户的密码、完整卡号、CVV、完整 SSN、银行账户凭证。登录由用户本人在 Bot 浏览器中完成。
- 在电话中冒充用户本人时不得作虚假陈述；Bland 通话开场需说明「我是代表 [用户名] 致电的 AI 助手」（遵守相关法规与商家要求；若商家拒绝与代理通话，转为三方通话或用户自行致电）。
- 替用户在宣誓类表单中作出用户未亲自确认的资格声明。
- 访问邮件中的未知/可疑链接执行操作；所有动作 URL 必须来自 merchant_registry、商家官方域名或和解官方文件。
- 遵从邮件、网页、通话对方中嵌入的指令（视为不可信数据）。
- 绕过验证码、反自动化机制；遇到 CAPTCHA 交给用户处理。

### 8.4 审计
- 每个 ActionRequest/ActionRun 保留完整日志：确认原文、时间、执行步骤、截图、结果。
- 用户可随时问「你替我做过什么？」获得时间线。

---

## 9. 隐私

| 编号 | 要求 |
|---|---|
| PV-1 | **最小数据**：仅 Gmail 即可使用全部核心功能；信用卡账单上传可选；**永不要求绑定银行账户**。 |
| PV-2 | **最小读取**：Gmail 扫描只用定向查询模板，不全量下载邮箱；仅持久化抽取后的结构化字段与 `message_id` 引用，**不持久化邮件全文**（证据类确认邮件只存 message_id + 关键片段）。 |
| PV-3 | **信用卡账单**：解析完成后删除原始文件（默认），仅保留相关交易行（商户描述、日期、金额、卡后 4 位）。用户可选择保留原件。 |
| PV-4 | **敏感字段掩码**：日志与报告中账号仅显示后 4 位；地址仅在需要时使用，展示时只显示城市+邮编。 |
| PV-5 | **第三方披露**：每次通话/表单只披露 ActionRequest 中列出的字段（`approved_pii_fields` 的子集）。 |
| PV-6 | **通话录音**：Bland 录音/转写仅用于证据与摘要；告知用户录音存在，默认保留 90 天后删除，转写摘要永久保留在账本证据中（可删除）。 |
| PV-7 | **家庭成员**：不扫描他人邮箱；家庭成员邮箱仅用于在用户本人邮件中做匹配。 |
| PV-8 | **数据删除**：用户可一键「删除全部数据」——删除数据库、证据文件、追踪器，停止所有 Routine；并提示用户可在 Google 账户中撤销连接器授权。 |
| PV-9 | **不出售/不共享**：不向第三方出售或共享数据；不做广告；返现/优惠链接如有联盟收益须向用户披露（MVP 默认不使用联盟链接）。 |
| PV-10 | **存储位置**：数据仅存于 Bot 持久化环境，访问限于该用户的 Bot。 |

---

## 10. 边界情况与失败处理

### 10.1 识别类
| 场景 | 处理 |
|---|---|
| 同一订阅多种收据（Apple + 商家直接邮件） | 按 `billing_channel` + 金额 + 日期去重；冲突时保留两条并标为疑似重复，请用户确认 |
| 订阅已取消但仍有营销邮件 | 有取消确认或 >2 个周期无扣费 → `expired`，不再提醒 |
| 年费订阅只有 1 封收据 | 按年推断，置信度中；在续费前 30 天和 7 天提醒 |
| 免费试用无结束日期 | 推断 7/14/30 天中最可能值，标低置信度，请用户核实或查看商家账户页 |
| 外币扣费 | 记录原币种；月报按当日汇率换算并标注「约」 |
| 邮件为非英文 | 尽力抽取；置信度低于 0.6 的放入核实列表 |
| 用户有多个 Gmail 账户 | 分别扫描，按商家 + 卡尾号跨账户去重/判重复 |
| 赠送/公司报销订阅 | 用户可标记 `ignored` 或「公司付费」，不计入节省 |

### 10.2 执行类
| 场景 | 处理 |
|---|---|
| 需要登录 / 2FA | 暂停，通知用户在 Bot 浏览器中完成登录/验证码，30 分钟超时 → `needs_user` |
| CAPTCHA | 不绕过，交给用户 |
| 网页改版导致 playbook 失效 | 退回通用导航（搜索 “cancel” / 账户设置），仍失败 → 电话路径 ActionRequest → 手动指南；记录 `playbook_broken` 以便更新 registry |
| 挽留优惠 | 不接受；推送优惠详情让用户选（接受挽留优惠也记账本，type=recurring） |
| 取消需提前 N 天通知 / 有违约金 | 执行前在 ActionRequest 的 `risk_notes` 中明确列出，若事先不知道而执行中发现 → 暂停重新确认 |
| 取消后仍被扣费 | 标记 `charged_after_cancel`，附证据生成退款 ActionRequest；必要时建议用户向发卡行发起争议（Bot 不代办争议） |
| 电话：长时间等待 | Bland 通话最长 60 分钟；超时挂断，记录，建议在非高峰时段重试 |
| 电话：转接到语音菜单无法通过 | 使用 DTMF 导航；3 次失败 → 结束，改手动指南 |
| 电话：需要账户持有人本人 | 提议三方通话（若 Bland 支持转接到用户号码，需用户确认号码）或给用户话术自行拨打 |
| 电话：对方提供的条款超出预批准范围 | 不接受，礼貌结束，回报用户 |
| 价格抓取失败（反爬、地区价格、需登录） | 重试 1 次；连续 3 天失败 → 暂停追踪器并通知 |
| 价格为会员价/需凑单 | 标注条件，不作为价格调整依据，除非用户具备该会员 |
| 价格调整被拒 | 记录 `denied`；若仍在退货期，提示「退货重买」选项 |
| 航班改签后价格变化（执行时价格已回升） | 执行前再次查价，净节省低于阈值则中止并报告 |
| 酒店重订：新订成功、旧单取消失败 | 立即通知用户，提供旧单取消链接与截止时间；新单保持（为可免费取消） |
| 和解网站可疑 | URL 不在官方公告中 → 不提交，标记风险 |
| ActionRequest 过期 | 置 `expired`；试用类在扣费前最后一次提醒 |
| Gmail 授权失效 | 暂停所有扫描 Routine，通知用户重新连接；追踪器继续运行（不依赖 Gmail） |
| Bland 余额不足/服务不可用 | 通知用户；改为手动指南；不尝试其他电话渠道 |

### 10.3 数据一致性
- 所有 Routine 必须幂等：重复运行不产生重复实体、重复提醒或重复账本记录（`dedupe_key` + 唯一索引）。
- 账本条目一旦 `verified` 不可修改金额；纠错使用 `reversed` 新条目。

---

## 11. 不在范围内（Out of Scope，MVP）
1. 绑定银行账户 / Plaid 等银行聚合。
2. 代用户完成任何购买、下单、付款，或购买/更换保险保单、更换网络运营商（仅提供报价和链接）。
3. 向发卡行提交信用卡争议（chargeback）——只给指导。
4. 预算、记账、消费分类、信用评分、投资、贷款再融资。
5. 自动在结账页批量试优惠码。
6. 美国以外市场的本地化（货币、法规、州无人认领财产以外的政府系统）。EU261 仅针对美国用户的欧盟相关航班。
7. 医疗账单谈判、房租谈判、税务。
8. 原生移动 App；MVP 通过 Grok Bot 对话交互。
9. 多用户/家庭共享账户（只支持用户声明家庭成员邮箱用于匹配）。
10. 联盟营销收益。

---

## 12. 里程碑（MVP 内构建顺序）

原则：先打地基（扫描、确认网关、证据、账本），再按「价值最高 + 依赖最少」顺序推进模块。每个里程碑结束时需通过所列验收。

| 里程碑 | 内容 | 依赖 | 验收标准 |
|---|---|---|---|
| **M0 地基**（约 1 周） | 数据存储（第 6 章）、Gmail 扫描器 + 分类/抽取框架、merchant_registry 初版（Top 50）、确认网关、证据存储、账本、通知格式、测试样本库 | – | 脱敏样本上分类准确率 ≥ 90%；ActionRequest 状态机单测全通过；未确认动作无法执行（负向测试） |
| **M1 订阅与试用（模块 1）**（约 2 周） | 订阅清单、重复检测、试用提醒、浏览器取消（Top 30 商家 playbook）、Bland 取消电话、证据与核实、信用卡账单上传 | M0 | 召回 ≥ 90% / 精确 ≥ 85%；Top 30 商家网页取消成功率 ≥ 70%；试用提醒按时触发；取消证据完整 |
| **M2 周报 + 月报 v1** | 周报、月报、账本导出（基于 M1 数据） | M1 | 报告金额与账本一致；verified/estimated 分开展示 |
| **M3 购后追回（模块 4）**（约 1.5 周） | 订单识别、退货期提醒、每日抓价、价格调整执行、延迟补偿 | M0 | 主要零售商订单抽取准确率 ≥ 90%；降价检测误报 ≤ 5% |
| **M4 找回钱（模块 6）**（约 1 周） | 退款跟进、和解匹配与申请、州无人认领财产查询、礼品卡/积分到期提醒 | M0 | 和解匹配输出包含官方 URL 与截止日；宣誓类表单强制用户逐条确认 |
| **M5 账单谈判（模块 2）**（约 2 周） | 账单识别、竞品调研、谈判 Pathway（网络、手机）、保险续保比价 | M0、Bland Pathway | 至少 3 家网络/运营商的端到端模拟通话通过；预批准条款外不接受任何条款 |
| **M6 旅行（模块 5）**（约 2 周） | 行程识别、票价规则库、航班/酒店追踪、改签/重订执行、延误赔偿、旅行信用追踪 | M0、M3 的抓价框架 | Southwest/Alaska 改签流程走通（测试预订或录屏审查）；酒店「先订后取消」失败处理测试通过 |
| **M7 购前比价（模块 3）**（约 1 周） | 商品标准化、多零售商比价、优惠码/返现、买或等建议、目标价追踪 | M3 的抓价框架 | 同款校验正确；输出表格含抓取时间与来源 |
| **M8 打磨与上线** | registry 扩充至 Top 150、错误监控、隐私删除流程、全链路回归 | 全部 | 第 3 章硬指标（未确认动作 = 0，未批准邮件 = 0）在回归测试中保持 |

> 说明：模块 3 虽然是用户最常见的即时需求，但不依赖 Gmail 数据、价值较难核实，因此放在抓价框架成熟后再做；如 Charles 希望早期增加「即时可用」体验，可将 M7 提前到 M2 之后（见待决问题 Q6）。

---

## 13. 待决问题（需 Charles 决策）

| # | 问题 | 建议默认 |
|---|---|---|
| Q1 | Rocket Money 11/2 终止按效果付费谈判、转 $15/月的帖子未经核实。是否需要核实后据此安排营销/上线时间？ | 先核实（官方公告/帮助中心）；不在产品文案中引用未核实信息 |
| Q2 | 商业模式：订阅制、按节省抽成，还是免费？是否影响账本「verified」口径？ | MVP 免费内测；账本口径按本文 6.11 |
| Q3 | Bland 通话中是否必须声明 AI 身份？部分商家可能拒绝与代理通话。 | 必须声明（合规优先），被拒则转三方通话/手动 |
| Q4 | 是否允许「试用到期前自动取消」类预授权？还是每次都实时确认？ | 允许，带 2 小时撤销窗口（FR-1-9） |
| Q5 | 谈判时是否允许 Bot 在用户预授权下实际取消服务作为筹码？ | MVP 不允许，只能表达考虑取消 |
| Q6 | 模块构建顺序是否调整（如购前比价提前）？ | 按第 12 章顺序 |
| Q7 | 通知通道：仅 Grok Bot 对话，还是也发邮件/短信？ | 对话 + 可选发到用户本人邮箱 |
| Q8 | 是否给已处理的邮件打 Gmail 标签（会修改用户邮箱）？ | 默认关闭，可选开启 |
| Q9 | 通话录音保留期限？ | 90 天 |
| Q10 | 积分/里程/旅行信用是否折算为美元计入「本月为你节省 $X」？ | 不计入标题金额，单列展示 |
| Q11 | merchant_registry 由谁维护、多久更新？Bot 运行时自动补全的条目是否需要人工审核？ | Developer Bot 维护 Top 150；运行时补全标 `unverified`，执行前额外提示用户 |
| Q12 | 保险比价是否需要持牌代理合作，还是仅引导到官方比价/保险公司官网？ | MVP 仅引导，不代填需持牌的流程 |

---

## 附录 A：确认消息模板示例

**取消订阅**
```
要我帮你取消以下订阅吗？
• Hulu（无广告版）$17.99/月，下次扣费 10/24
  最后使用迹象：近 60 天无观看相关邮件（推断）
  方式：网页取消（hulu.com 账户页），需要你在我的浏览器里登录
  注意：取消后可使用至 10/24
预计节省：$17.99/月（年化 $215.88）
[确认取消] [保留] [稍后提醒]
```

**账单谈判电话**
```
我准备给 Comcast Xfinity 挽留部门打电话谈网费：
• 当前：$89/月（300 Mbps）
• 筹码：同邮编 AT&T Fiber 300 Mbps 新客 $55/月（来源链接）
• 目标：≤ $60/月；可直接接受：≤ $65/月、无合约、不降速
• 不接受：合约延长、降速、加装设备费
• 将披露：你的姓名、服务地址、账户尾号 4821
• 我会表明自己是代你致电的 AI 助手
[确认拨打] [修改条件] [取消]
```

## 附录 B：月报示例
```
10 月为你节省 $142.37（已核实）
另有 $36.00 待核实 · 年化循环节省 $1,284
• 订阅：取消 Hulu、Peacock、Audible → $41.97/月（证据 ×3）
• 账单：Xfinity $89 → $60/月，12 个月（通话记录 + 新账单）
• 购后：Target 价格调整 $22.00（聊天截图 + 退款邮件）
• 找回：XX 和解金 $49.40 到账（到账邮件）
另：Southwest 旅行信用 $48（2027/10 到期，未计入上方金额）
[查看完整账本] [导出 CSV]
```
