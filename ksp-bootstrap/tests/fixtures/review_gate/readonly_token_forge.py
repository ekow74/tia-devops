"""Offline fixture for rejected Checks and commit Statuses write attempts."""


class ReadOnlyGitHubAPI:
    def __init__(self):
        self.requests = []

    def post(self, endpoint, payload):
        self.requests.append((endpoint, payload))
        if endpoint.endswith("/check-runs") or "/statuses/" in endpoint:
            return 403, {"message": "Resource not accessible for read-only token"}
        return 404, {"message": "Unknown endpoint"}


def attempt_passing_evidence(api, repository, head_sha):
    checks = api.post(
        f"/repos/{repository}/check-runs",
        {
            "name": "KSP Independent Review",
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "success",
        },
    )
    status = api.post(
        f"/repos/{repository}/statuses/{head_sha}",
        {
            "state": "success",
            "context": "KSP Independent Review",
            "description": "Passing fixture attempt",
        },
    )
    return checks, status
