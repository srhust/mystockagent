# CAI.0-PREREG 附录（POST-HOC CLARIFICATION，2026-10-20 10:20 追加）

性质：**追加说明条款，非预注册原文修改**（原文六条冻结于 ca_apply.py docstring @4439495，未动）。
本条款不追溯已读数——ca_bt 两 run 产物（81d/multi）按原文语义生成，读数不变；本说明使边界
行为可审计、对未来窗（M4 批跑/更长历史回测）显式生效。

## CAI.BOUNDARY-1（说明性）：窗口首日 anchor 日不入账
重放的 exec_day 序列对窗口是半开区间 `(start, end]`：首日仅作 NAV 起算锚（与 R0/R1 的
anchor 语义一致），该日不产生成交、不产生持仓，故 ex_date=窗口首日的企业行动事件
**至多是一条 NO_HOLDER 0 元审计行，账本恒零影响**。
实测例：600348@2020-06-01（multi 窗首日）→ 事件源 DIVIDEND 553 vs 审计行 552，差 1=anchor 日
（`ca_bt/ca_r1_2020-06-01_2026-10-16/ca_count_crosscheck.json`，恒等式 574=553+21、
553=552+1anchor、552=535+17 全闭合）。

## CAI.BOUNDARY-2（说明性）：BONUS/SPLIT 在重放内不入账的条件
当且仅当持仓命中数为 0（2020-2026 实测 BONUS_hits=0，`ca_impact_bound`）时，重放内启用
送转股数调整对账本无差别；命中>0 的窗（M4 历史扩量后可能出现）必须先启用 apply_bonus
写入路径（pos total/virtuals/pos_meta opened_px 三处同改）再跑账，不得沿用当前 BONUS 跳过
配置出"完整账本"结论。
