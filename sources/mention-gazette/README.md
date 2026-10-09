# Mention Gazette in Cloud

Deterministic Python script. No agent conversation, Jev call, package installation,
Mac keychain access, external generator file, or shared checkout is required at runtime.

The bundle includes the newspaper renderer formerly stored on the local host.
The script reads unread GitHub notifications, keeps public repositories and supported
public subject types, and preserves notification-reason grouping. It rechecks repository
visibility before rendering. Titles are HTML-escaped and links are constructed from
validated repository names and subject IDs. It does not mark notifications as read.

The script updates only `enyst/enyst.github.io:main:arch/mention-gazette.html` using
the GitHub Contents API. It supplies the current file SHA, so a concurrent edit fails
instead of being overwritten. It verifies the content at the returned commit. Other
files and local checkouts are untouched. Identical content creates no new commit.
Retrieval failures do not publish an empty page. Notification collection is limited
to 2,000 items and fails visibly at the limit rather than silently dropping pages.

Runtime credential: Cloud secret `GAZETTE_GITHUB_TOKEN`, owned by `enyst`, with notification
read and notebook write access. The script retrieves only this named secret and never
logs its value. No secrets belong in Git. Cloud supplies the completion callback token.

Desired schedule: Monday through Thursday, 09:00 Europe/Amsterdam. This preserves
the local schedule and follows daylight saving time. The edition date uses that zone.

`python3 scripts/deploy_gazette.py` uploads the maintained source, creates or updates a
paused Cloud definition, and reads it back. The `--install-secret` option explicitly
copies the existing enyst GitHub credential to Cloud if the dedicated secret is missing.
Use that option only with authorization for the credential transfer. Existing credentials
are never overwritten. The helper never enables schedules.
Before cutover, manually dispatch one Cloud run and verify the published file. Then
disable the local definition, enable the desired Cloud schedule, and export both states
to Git. A Git push alone does not deploy the Cloud code.

Tests: `python3 -m unittest discover -s tests -p 'test_mention_gazette.py' -v`.
The synthetic tests cover end-to-end rendering/publication, private data exclusion,
HTML escaping, pagination, retrieval failure, unchanged editions, and file conflicts.
