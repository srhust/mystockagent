# M4 批1-sina 首遍纪要（2026-10-20 15:10）

| 年 | alive | ok | fail | empty | cov | 判定 |
|---|---|---|---|---|---|---|
| 2000 | 1062 | 862 | 198 | 2 | 0.8117 | MAJOR_GAP_SUSPEND |
| 2001 | 1141 | 934 | 205 | 2 | 0.8186 | MAJOR_GAP_SUSPEND |
| 2002 | 1209 | 997 | 210 | 2 | 0.8246 | MAJOR_GAP_SUSPEND |
| 2003 | 1269 | 1061 | 206 | 2 | 0.8361 | MAJOR_GAP_SUSPEND |

- **诊断**：各年 fail ~205 同构、耗时尾随速率下降（sina 日累计限流），且监控实证被 25s 弃的请求**后台最终成功**（文件计数持续增长）→ fail=超时口径假弃，非数据缺口。empty=2/年为边界事实（如年内极短存续）。
- **处置**：16:40 后补跑（60s 超时已校准 + manifest 每 100 券续传 skip 3924 成功项、仅重取 ~819 fail 项）。预期补跑后 cov→~0.98，四年轻松过 85% 门 → 批1 QC=PASS 再进批2（裁定 A3 门，无跳批）。
- **通道卫生**：批1 进程自然退出零孤儿（线程化 30/60s 有界的红利，对比 baostock 僵尸会话教训）。15:40-16:40 全网静默让位 FORWARD 直播（15:52 EM fetch / 16:05+16:20 sina probe+crosscheck）。
- **baostock 侧**：僵尸会话 2.5h 未清（登录通数据挂，疑服务端连接泄漏）——价格批不依赖它，ST/tradestatus 官方补标与 CA 事件子批待通道自然恢复后补，均已入缺口账（CORPORATE_ACTION_GAPS #4/#7 行）。
- 数据文件位置：data/ashare_history_2000/daily_sina/{Y}/（git-ignored 设计，审查者按文件系统读；每目录 manifest.json 含 per-file sha256/行数/日期范围）。
