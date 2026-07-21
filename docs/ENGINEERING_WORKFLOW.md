# Engineering Workflow

## Team Roles

| Role              | Person   | Responsibilities                                         |
|-------------------|----------|----------------------------------------------------------|
| Technical Lead    | Van      | Requirements, final approval, Git history changes        |
| System Architect  | ChatGPT  | Architecture, planning, independent QA, code review     |
| Implementation    | Hermes   | Implementation, testing, repository prep, handoff reports|

## Standard Delivery Workflow

1. ChatGPT defines requirements, architecture, API contracts, database
   changes, and acceptance criteria.
2. Hermes creates or uses a dedicated feature branch.
3. Hermes implements only the approved scope.
4. Hermes runs linting, typing, tests, Docker validation, and health
   checks.
5. Hermes prepares the changes for review.
6. ChatGPT performs independent QA.
7. Van explicitly approves commits, pushes, pull requests, merges, and
   releases.

## Definition of Done

A task is not complete until ALL of the following are verified:

- [ ] Approved scope is implemented
- [ ] Relevant tests are added or updated
- [ ] Ruff passes (`ruff check .`)
- [ ] Mypy passes (`mypy app/`)
- [ ] Pytest passes (`pytest`)
- [ ] Docker Compose configuration validates (`docker compose config`)
- [ ] Required containers start successfully
- [ ] Liveness endpoint passes (`GET /api/v1/health/live` returns 200)
- [ ] Readiness endpoint passes (`GET /api/v1/health/ready` returns 200)
- [ ] Database data remains intact
- [ ] No secrets or local files are staged
- [ ] Documentation is updated
- [ ] Changed files and risks are reported
- [ ] The work is ready for independent QA

## Git Policy

### Hermes May

- Inspect Git status and history.
- Create a feature branch when explicitly included in an approved task.
- Stage intended files.
- Inspect staged diffs.
- Scan for secrets.
- Propose commit messages.
- Prepare pull-request descriptions.

### Hermes Must Not (Without Explicit Approval From Van)

- `git commit`
- `git push`
- Create or modify a GitHub repository.
- Add or change a remote.
- `git merge`
- `git rebase`
- `git commit --amend`
- `git push --force`
- `git tag`
- `git branch -d`
- Open or merge a pull request.

## Branching Strategy

| Branch Pattern       | Purpose                              |
|----------------------|--------------------------------------|
| `master`             | Stable integration branch            |
| `feature/<name>`     | New features                         |
| `fix/<name>`         | Bug fixes                            |
| `chore/<name>`       | Infrastructure and maintenance       |

Do not create unnecessary long-lived branches. Feature branches are
merged into `master` after QA and Van's approval, then deleted.

## Commit Messages

Follow Conventional Commits:

```
<type>: <short summary>

<optional body>

<optional footer>
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`.

## Handoff Format

When completing a task, Hermes produces:

```
## Completed
<what changed>

## Files Modified
<list>

## Tests Executed
<commands>

## Test Results
<outputs>

## Risks
<potential side effects>

## Recommended Next Step
<future improvements>
```
