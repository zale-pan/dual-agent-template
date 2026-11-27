"""Run the installed governance tools from the business project, without PYTHONPATH."""
import sys
from pathlib import Path

from governance import daily_round, event_append, round_close, round_draft, round_promote, review_views, round_package, steward_workflow


def main(argv):
    if argv and argv[0] in ("activate-project", "check-setup", "dispatch-round", "submit-round"):
        return daily_round.main(Path(__file__).absolute().parent, argv)
    if argv and argv[0] == "close-round":
        return round_close.main(Path(__file__).absolute().parent, argv[1:])
    if argv and argv[0] == "append-event":
        return event_append.main(Path(__file__).absolute().parent, argv[1:])
    if argv and argv[0] == "promote-round":
        return round_promote.main(Path(__file__).absolute().parent, argv[1:])
    if argv and argv[0] == "new-round":
        return round_draft.main(Path(__file__).absolute().parent, argv[1:])
    directory = Path(__file__).resolve().parent
    tools = {"review_views": review_views, "round_package": round_package, "steward_workflow": steward_workflow}
    if not argv or argv[0] not in tools:
        print("Usage: python -B .dual-agent/tools.py {check-setup,activate-project,dispatch-round,submit-round,close-round,promote-round,new-round,append-event,review_views,round_package,steward_workflow} [options]")
        return 2
    root = directory if argv[0] == "review_views" else directory.parent
    return tools[argv[0]].main(["--root", str(root), *argv[1:]])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))

