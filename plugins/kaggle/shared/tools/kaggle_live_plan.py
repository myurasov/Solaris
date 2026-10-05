# rev. 4

"""kaggle_live_plan: retired. The live plan is now part of the project's single status page.

kaggle_status.py, beside this file, builds that page (reports/status.pdf, from the hand-edited reports/status.json):
the current state, the leaderboard progress, the plan and timeline, the spending, the resources, the open questions
and the suggestions. Move the plan from submissions/live-plan.json into reports/status.json, then run

    python3 <plugin-dir>/tools/kaggle_status.py

This stub only says so and exits 2.
"""
import sys


def main(argv=None):
    print("kaggle_live_plan: retired - the live plan is now part of the single status page, which kaggle_status.py "
          "(beside this file) builds: reports/status.pdf from reports/status.json. Move the plan from "
          "submissions/live-plan.json into reports/status.json, then run kaggle_status.py.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
