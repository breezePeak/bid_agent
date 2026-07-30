# PR-14.1 Golden / 评测基础设施 — 验收记录

- 状态：**基础设施完成；专家标注样本未完成（Gate A 未解锁）**
- 日期：2026-07-27
- 前置：Gate K PASS、Gate S PASS

## 已交付

1. `GoldenRegistry` / loader / 指标与报告契约：`src/document_pipeline/golden.py`
2. Registry manifest + 8 个样本槽位：`tests/fixtures/v3_golden/`
3. 错误分类表：`tests/fixtures/v3_golden/error_taxonomy.json`
4. 评测脚本：`scripts/evaluate_v3_bid_pipeline.py`
5. 测试：`tests/test_v3_golden_registry.py`
6. ADR 增补：ADR-03、ADR-12（与既有 ADR-01/02/11 及审查清单共用）

## 样本状态（诚实口径）

| sample_id | status | 说明 |
|---|---|---|
| G-A1-SYN-001 | synthetic | 合成 A1 烟雾样本，可用于 loader/eval 冒烟 |
| G-A1-SCAN-PLACEHOLDER | annotation_pending | 扫描件阻断场景占位，原件外置 |
| G-A-PLACEHOLDER-003～008 | scaffold | 预留 8 样本 DoD 槽位，待匿名真实项目入库 |

**没有 `expert_accepted` 样本，因此不得宣称 Golden 语义 baseline 或 Gate A 完成。**

## 策略对齐

- 不把历史 `92/198` 当业务规模或发布阈值
- Git 只存匿名 fixture / hash / 标签 / 报告；敏感原件外置

## 验证

```text
python -m unittest tests.test_v3_golden_registry -v
python scripts/evaluate_v3_bid_pipeline.py --suite A --layer A1
```

## 当前规则 baseline

- 报告：`artifacts/golden_eval/baseline_current_rules_a1.json`
- 样本：`G-A1-SYN-001`（synthetic）
- 口径：冻结当前确定性规则在合成样本上的抽取结果；**不调阈值迁就结果**；**不是 Gate A 证据**

## 后续

1. 入库至少 8 份匿名真实项目（覆盖计划所列形态）
2. 双人标注 + 第三方裁决，生成 A1～A4 / B / C / D 期望
3. 用专家样本替换 scaffold 槽位并重跑 baseline/current paired regression
4. 达成后申请 Gate A
