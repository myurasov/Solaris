---
name: kaggle-kind-tabular
triggers: ["tabular competition", "tabular data competition", "time series competition", "forecasting competition", "grouped backtest"]
summary: Kind notes for Kaggle tabular competitions (rows of features and a target under a fixed metric, time series and forecasting included) - grouped backtests when the board cannot decide, leak-free folds, missing values that reveal a source, and the usual pitfalls. Read once the facts sheet records this kind.
---
_Rev. 1_

# Skill: kaggle-kind-tabular - Tabular and Time-Series Competitions <!-- omit in toc -->

- [When This Applies](#when-this-applies)
- [Validation](#validation)
- [Submissions](#submissions)
- [Pitfalls](#pitfalls)

## When This Applies

Rows of features with a target, scored by a fixed metric on a hidden test split; time series and forecasting
included. Read this once the facts sheet records the kind (a contest can mix kinds: read each that fits), on top of
the playbook. *General practice* marks widely known practice for the kind, without evidence of our own yet.

## Validation

- **With no useful board, a grouped backtest decides:** hold out whole periods, sites or sources, the way the hidden
  test is split off.
- **Audit missing-feature patterns:** columns absent only from one training source encode that source.
- *General practice:* split folds the way the test is split off: stratified for a rare class, grouped when rows
  share an entity (a customer, a patient, a site), forward in time for time series, with a gap when features look
  back over a window.
- *General practice:* fit every step learned from the data (target encoding, imputation, feature selection) inside
  each fold, never on all rows, and keep the out-of-fold predictions for blends and stacks.
- *General practice:* adversarial validation (a classifier trained to tell training rows from test rows) shows
  whether the two differ and which features carry the shift; its most test-like rows make a holdout.

## Submissions

- *General practice:* gradient-boosted trees (LightGBM, XGBoost, CatBoost) are the usual strong baseline; average
  folds and seeds, and blend diverse models with weights fitted on out-of-fold predictions.
- *General practice:* for forecasting, refit on the latest periods before predicting the test horizon, as far as the
  rules allow.

## Pitfalls

- *General practice:* target leakage: features built from information after the prediction time, or aggregates that
  include the row's own target, inflate validation and vanish on the test.
- *General practice:* duplicate rows split across folds, and ids or row order that track the target, inflate
  validation too: check for them, and keep duplicates in one fold.
