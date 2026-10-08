---
name: kaggle-kind-simulation
triggers: ["simulation competition", "kaggle simulations", "bot ladder", "game bot competition", "ladder rating"]
summary: Kind notes for Kaggle simulation competitions, where uploaded bots play each other on a rated ladder - rating bots by local games, the bot-ladder exception to using every slot, and the usual pitfalls. Read once the facts sheet records this kind.
---
_Rev. 1_

# Skill: kaggle-kind-simulation - Simulation Competitions and Bot Ladders <!-- omit in toc -->

- [When This Applies](#when-this-applies)
- [Validation](#validation)
- [Submissions](#submissions)
- [Pitfalls](#pitfalls)

## When This Applies

The competition scores uploaded bots by games against other teams' bots: each bot carries a skill rating that moves
with every game, and the board ranks those ratings, not answers on a fixed test set. Read this once the facts sheet
records the kind (a contest can mix kinds: read each that fits), on top of the playbook, whose general rules apply
where this file does not say otherwise. *General practice* marks widely known practice for the kind, without
evidence of our own yet.

## Validation

- *General practice:* rate a bot by many local games against a fixed pool of opponents (our earlier versions, the
  strongest public bots, simple baselines), with seats, sides and seeds swapped, and report its win rate with an
  interval; a handful of games decides nothing.
- *General practice:* play local games in the hosts' own environment package, at the version the ladder runs and
  with its per-step time limit, so rules, timeouts and errors match the ladder's.
- *General practice:* strategies can be non-transitive: a bot that beats our last version can lose to others, so
  keep the pool broad and read each matchup, not only the total. The ladder's public game replays show what the top
  bots do, and can serve as training data.

## Submissions

- **The bot-ladder exception to "use every slot":** where the latest uploads are the finals, upload only bots you
  would keep as finals.
- *General practice:* a new bot's rating starts uncertain and settles only over many games, often days: judge a
  change by local games, and read the ladder only once the rating has settled. An upload first plays a validation
  game against copies of itself; one that fails there is marked as an error.

## Pitfalls

- *General practice:* a bot that errors or overruns its per-step time in a ladder game counts as losing it: keep a
  margin under the limit on the ladder's hardware, and log how often the bot's own cutoff fires.
