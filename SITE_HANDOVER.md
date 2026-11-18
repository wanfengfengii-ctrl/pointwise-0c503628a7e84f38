# 单站观测交班

青石河门户一次冻结可包含多个站点。同页相邻原件也可能来自不同站。日班发布后，将各站回执通过独立的交班渠道留给各站。晚班分别领取本站包，到了没有门户、站点库或归档连接的环境，也能用先前取得的回执核对原件和账。

## 采集与领取

| 入口 | 结果 |
| --- | --- |
| `Collector(catalog, Publisher(store)).capture_frozen_with_receipts(source, export_id, capture_id, versions)` | 参数与 `capture_frozen` 相同。按原规则收齐整单，为 `versions` 中每个绑定站点签发回执，包含两栏均空的绑定站点。成功返回已提交的 `capture_id`。 |
| `CaptureReader(store).get_site_receipt(capture_id, station_id)` | 返回发布时固定的回执 `bytes`。回执只携带身份和核验材料，可单独保存。重开后取回同一字节。 |
| `CaptureReader(store).get_site_package(capture_id, station_id)` | 返回下文约定的单站包。可在门户及站点库关闭后从固定归档领取。 |
| `archive.site.verify_site(receipt, capture_id, station_id, package)` | `receipt` 是独立留存的回执字节，两个 ID 是核验者预期的成果和站点。只凭这四项核验。成功返回 `archive.codec.CaptureProjection`，含本站规范行和本站两栏账。 |

新入口与原入口使用同一个成果号空间，重复成果号沿用 `CaptureAlreadyExists`。新成果的原件、整单投影和所有站点回执同时生效。采集、写入或提交失败时新成果和新回执均不可领取，旧成果仍可领取和核对。沿用原采集异常及 `ArchiveCommitError`。原 `capture_frozen` 继续发布没有单站回执的成果。原 `CaptureReader.get` 与 `CaptureAuditor.audit` 继续接受旧成果及新成果。

两种单站领取入口对未知成果抛 `CaptureNotFound`，对经原入口发布的成果抛 `archive.site.SiteReceiptUnavailable`，对已签成果中未绑定的站点抛 `collector.bindings.UnboundStation`。应有证据缺失或损坏时拒绝交付并抛 `AuditError`。离线验真失败也抛 `AuditError`，整包无部分结果。全档 `CaptureAuditor.audit` 对已签成果须用固定原页和口径重建并核对全部应有回执，再返回 `True`。已签成果的回执丢失或损坏时须抛 `AuditError`，包括整组回执丢失。

## 单站包与核验

单站包用 Python 字典交接。各身份字段与原归档使用同一字符串。可附带由实现定义的 `proof` 核验材料。包及回执中的核验材料都须遵守本站披露边界，其他站原件和读数不得随包交付。

| 字段 | 含义 |
| --- | --- |
| `capture_id`、`export_id`、`station_id` | 固定成果、门户导出及本站身份。 |
| `calibration` | 含 `version`、`coefficient`、`offset`、`lower`、`upper` 的字典，沿用现有版本与精确整数口径。 |
| `columns` | 栏目字典，恰有 `pending` 和 `reviewed` 两个键。每个值是本站原件列表，按该栏原页行顺序排列，空栏用空列表。 |
| 每条原件 | 含 `observation_id`、`raw_value`、`page_number`、`row_number` 的字典。原值为整数，页行从 1 开始，保留混站原页上的绝对位置。导出、站点和栏目由外层给出。 |

核验须确认包所给的两个栏目恰好覆盖发布时该站的原件，并且原件、位置及换算口径属于预期成果和站点。包里的自报身份与核验材料须由独立回执约束。回执的内部表示和储存布局由实现决定。核验通过后，规范行按待复核、已复核的顺序排列，各栏保留原页行顺序，按 README.md 的闭区间口径复算两栏账。测试换值或换口径时，即使规范行和两栏账仍相同，核验也须拒绝。回执由交班方经可信渠道交付，本功能依靠这份独立留存的回执核验包。

## 混站样例与验证

`python3 -B -m examples.site_handover` 运行现有整档采集并检查混站原页。样例提供 `freeze_example(catalog, source)`，使用 `FixturePortal(page_size=3)` 后得到导出号 `shift` 和站点版本映射。待复核第一页依次为桥站 A 原值 3、堰站 B 原值 70、桥站 C 原值 4。已复核第一张为原来的空结束页。桥站固定口径为零系数、偏置 7、闭区间 [7,7]。

对这份样例发布单站回执后，`bridge` 的包应只有待复核 A 和 C，页行分别是 (1,1) 和 (1,3)，已复核为 `[]`。离线所得两个规范值均为 7。待复核账为观测数 2、最小值 7、最大值 7、越界数 0。已复核账为观测数 0、极值 `None`、越界数 0。

用已另存的原回执核验桥站原包，分别试验去掉 C、去掉空栏、把 C 的原行改为 2、把 A 的原值改为 30、换口径及混用另一成果或站点的回执。原包通过，其余拒绝。在隔离归档副本中核对回执缺失或改动后整档复核失败。可复用原回归中的延迟外键故障方式拒绝最终提交，检查重开后新成果及回执均不可见、旧成果可读，并核对没有签发过的旧成果使用新领取入口时报告未签。

沿用 `.pointwise-runtime.json` 的版本 1 和 `cli` 类型。`setup` 是准备命令的参数数组列表，`verify` 和 `demo` 分别是一条命令的参数数组。接入联合核验和停源重开演示，保留原回归及失败退出码。演示先另存回执，再关停门户与站点库，重开归档领包，最后关闭归档并凭另存回执展示本站原页行和两栏账。随实现简述发布与领取之间如何交接范围、原址和固定口径。
