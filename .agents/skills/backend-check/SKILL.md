---
name: backend-check
description: Verify the AgentPulse backend before a commit or after backend changes. Report findings without fixing code.
---

# Backend verification

1. Confirm the working directory is the AgentPulseBackend repository.
2. Read AGENTS.md, README, dependency configuration, and Docker Compose
   files that exist.
3. Find and run the project's documented lint and test commands.
   If commands or tools are missing, report that instead of inventing them.
4. Review changed API behavior against its OpenAPI documentation.
   Check request validation, authentication, response shapes, and errors.
5. Check .env.example and .gitignore for environment configuration.
   Never print secrets or the contents of a real .env file.
6. If the local backend is already running, check its documented health
   endpoint and Swagger/OpenAPI availability.
7. Report:
   - Checks passed, with commands and results.
   - Checks failed, with actionable explanations.
   - Checks not run, with reasons.
   - Any API changes that affect the frontend.

Do not modify source files, install dependencies, deploy, or commit.
If the backend has not been implemented yet, report that clearly.