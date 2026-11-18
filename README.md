# 青石河冻结观测归档

青石河监测站每天冻结门户的待复核、已复核两栏。日班按站点的指定换算版本采集观测。数据员按站点和栏目核账并沿页号、行号回查原件。夜班关闭门户后从归档领取并复核同一成果。

## 运行

使用 Python 3.11 标准库。在仓库根目录执行平台提供的回归入口和交接演示。

```text
python3 -B check_project.py
python3 -B -m examples.handover
```

`check_project.py` 由控制台在物化时提供，执行 `tests` 中的完整回归。演示在临时目录写入站点库及归档，发布后关闭两种来源并重开归档。断言或业务操作失败时进程返回非零退出码。

## 日班与夜班接口

| 操作 | 公开入口 | 输入与业务结果 |
| --- | --- | --- |
| 登记口径 | `StationCatalog(path).register_station(station_id, version, coefficient, offset, lower, upper)` | 用整数参数登记固定版本。同键同值复用，同键异值报 `RevisionConflict`；非法区间报 `InvalidRange`。`get_revision(station_id, version)` 按明确版本读取。 |
| 冻结原件 | `FixturePortal(page_size).freeze_export(export_id, columns)` | `columns` 同时提供 `Column.PENDING` 与 `Column.REVIEWED`。每条原件可用 `RawObservation(observation_id, station_id, raw_value)` 或相同字段的字典提交。冻结复制内容，返回新导出号。 |
| 读取原页 | `FixturePortal.read_page(export_id, column, page_number)` | 栏目可用枚举或 `pending`、`reviewed`。页序从 1 开始。在同一个导出和栏目中，取页固定且按输入顺序排列。末页带 `terminal` 标记。空栏有一张空结束页。未知导出、非法栏目、页序越界和源故障各有专门异常。 |
| 固定成果 | `Collector(catalog, Publisher(store)).capture_frozen(source, export_id, capture_id, versions)` | `versions` 是站点号到明确版本号的映射。先绑定全部版本，再按待复核、已复核的顺序读完两栏。错批、漏页、错序、重复观测号、未绑定站点或来源故障使本批失败。返回已提交的成果号。 |
| 夜班领取 | `CaptureReader(store).get(capture_id)` | 返回采集号、导出号、带原件定位的规范行和分栏账。页内行序从 1 开始。`CaptureNotFound` 表示成果号未知。 |
| 停源复核 | `CaptureAuditor(store).audit(capture_id)` | 校验固定原件摘要，重建清册和质量投影并与归档投影比较。通过返回 `True`。未知号报 `CaptureNotFound`，缺证、证据损坏及投影差异报 `AuditError`。 |

`StationCatalog` 和 `ArchiveStore` 可以用 `with` 管理本地 SQLite 连接。初始化只创建缺失的表和约束，重开或重复初始化保留已有配置与历史。归档使用单次事务写投影和证据，重复成果号报 `CaptureAlreadyExists`，写入或提交失败报 `ArchiveCommitError` 并回滚本批。已发布成果的证据可通过 `archive.codec.decode_evidence(store.read_capture(capture_id).evidence)` 恢复原页和换算版本，按规范行的原件定位回查。

## 核账口径

整数规范值以毫单位计，公式为 `原值 × 系数 + 偏置`。闭区间包含两端，支持零系数、负系数和相同的上下界。整数计算及持久化保持精确。缺失或非整数原值报 `InvalidRecord`。每个绑定站点都有待复核、已复核两组，输出先按站点号排序，再按固定栏目顺序排列。空组的观测数和越界数均为 0，极值为空。

演示给青石桥站登记系数 2、偏置 1、闭区间 [7,8]。待复核原值为 3、4，规范值分别为 7、9。该栏有 2 条观测，极值为 7 和 9，越界 1 条。空的已复核栏也会显示，并随同该栏的结束页一并归档。夜班重开读到原口径上的同一结果，并凭固定原页和固定版本复算。
