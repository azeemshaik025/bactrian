"""Answer a request about web pages. The model that plans never sees a page.

    python3 web.py "tell me what this article is about https://example.com/post"
"""
import argparse
import http.client
import sys

import llm
import tools
from camel import Interpreter, PlanError, PolicyViolation


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("request")
    request = parser.parse_args().request
    interpreter = Interpreter(*tools.build(llm.quarantined))
    try:
        plan = llm.plan(request, interpreter.check)
        print(f"Plan:\n{plan}", file=sys.stderr)
        interpreter.run(plan)
    except PolicyViolation as blocked:
        sys.exit(f"Blocked: {blocked}")
    except (PlanError, RuntimeError, OSError, http.client.HTTPException) as failed:
        # A network error can carry text the site chose, such as the reason after a 404.
        sys.exit(f"Stopped: {' '.join(tools.clean(failed).split())}")


if __name__ == "__main__":
    main()
