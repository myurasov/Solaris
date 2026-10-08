---
name: kaggle-kind-ranking
triggers: ["ranking competition", "retrieval competition", "candidate retrieval", "learning to rank", "recommendation competition"]
summary: Kind notes for Kaggle retrieval and ranking competitions, where each query gets a ranked list of candidates - anchoring candidate channels on public probes, measuring the whole list, query-grouped folds and candidate recall, full lists, popularity priors, refits on another population. Read once the facts sheet records this kind.
---
_Rev. 1_

# Skill: kaggle-kind-ranking - Retrieval and Ranking Competitions <!-- omit in toc -->

- [When This Applies](#when-this-applies)
- [Validation](#validation)
- [Submissions](#submissions)
- [Pitfalls](#pitfalls)

## When This Applies

Each query gets a ranked list of candidates (retrieval, recommendation, identification against a library), scored by
a ranking metric (top-1 accuracy, MAP@k, MRR, NDCG, recall@k). Read this once the facts sheet records the kind (a
contest can mix kinds: read each that fits), on top of the playbook. *General practice* marks widely known practice
for the kind, without evidence of our own yet.

## Validation

- **Anchor channel levels on public single-channel probes.** Someone else's public submission from a single channel
  (one candidate source alone, say) is a board-anchored level for that channel: calibrate the holdout's mixture of
  channels to it, not to the holdout's own level for that channel, which reflects the holdout's makeup rather than
  the hidden test's.
- **Protecting the top answer does not protect the rest of the ranking.** A re-ranker that keeps every first
  candidate can still demote correct answers further down and fail its confirmation. For ranked outputs, measure the
  whole list, not only top-1 agreement.
- *General practice:* keep all candidates of one query in one fold, and report candidate recall (how often the answer
  is in the list at all) apart from ranking quality: recall caps what any ranker can reach.
- *General practice:* train the ranker on lists from the same candidate generator the test runs through, not on
  lists with the answer forced in.

## Submissions

- *General practice:* fill every position the metric reads (k for MAP@k): padding a short list at its end never
  lowers MAP@k, MRR or NDCG@k, and can still catch the answer.

## Pitfalls

- A ranker refit on in-distribution rows over-trusted one feature and hurt a different population.
- **Popularity priors** look strong on benchmarks whose answers are famous and reverse on obscure ones; guard with
  test-like strata before trusting them. It recurred with a public notebook's popularity prior: a holdout drawn from
  public libraries read a large gain (its answers beat their decoys on popularity almost always), while the holdout
  drawn like the hidden test had answers less popular than their decoys.
