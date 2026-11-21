# ADR-0015: Start on a shared Aurora cluster, keep the move-out cheap

- Status: Accepted (plan only; nothing is deployed in Phase 1)
- Date: 2026-11-20

## Context

AWS is the eventual host. An Aurora PostgreSQL Serverless v2 cluster with pgvector already
exists in the target account for another project. It has a minimum capacity of 0 and pauses
when idle. Starting PolicyPilot on that cluster, instead of paying for a new database, keeps
costs low while PolicyPilot is a demo. Aurora pauses only when no user connection is open, so
sharing saves money only if PolicyPilot leaves the cluster idle when it is not in use.

The plan is in [docs/deployment/aws.md](../deployment/aws.md).

## Decision

- Share the account-level services: the AWS account and region, DNS (a PolicyPilot subdomain),
  the GitHub OIDC provider, the CDK bootstrap, and the **Aurora cluster and its VPC**.
  - On the cluster, PolicyPilot gets a separate database, separate roles, its own secrets,
    per-role limits and an ingress rule for its own tasks only.
  - In the VPC, it adds its own public subnets.
- Do not share anything that carries another project's security policy or deploy rights. These
  are separate: the CloudFront distribution, the S3 bucket, the response-headers policy (CSP), the
  deploy role, the secrets, the model API keys and the log groups.
- **Compute:** ECS Express Mode on Fargate (ARM64) for the API, and a plain Fargate service for
  n8n, with private service discovery and no load balancer. No NAT gateway.
- **The Phase 1 code is built to be idle-friendly:**
  - `min_size=0` pools;
  - an event-driven outbox and WhatsApp drainer;
  - a `/healthz` that never touches the database;
  - browser polling only while tabs are visible;
  - n8n on SQLite, not on the shared cluster.
- PolicyPilot's CDK app (a later phase) references the shared resources by ID, never modifies
  another project's stack, and supports `--context mode=shared|standalone` from the start.

## Consequences

- While PolicyPilot is mostly idle, sharing saves about $10 a month against a standalone
  db.t4g.micro. With daily dashboard use the saving disappears. With connections always open,
  sharing costs more than standalone.
- The first request after an idle period waits about 15 seconds while the cluster resumes. The
  WhatsApp webhook stores events durably and relies on Meta's retries, so nothing is lost.
- The two projects share one blast radius: maintenance windows, engine versions,
  `max_connections`, and the cluster admin credential. `aws.md` lists the mitigations.
- Moving out is a `pg_dump` of one database plus a redeploy with `mode=standalone`.

## Alternatives considered

- **Fully standalone from day one** (own VPC and RDS): about $72 a month against about $60 shared
  while idle. It is the planned next step once usage grows.
- **Lambda with the RDS Data API:** the LangGraph Postgres checkpointer needs a real driver
  connection, and SSE streaming would need a web adapter.
- **App Runner:** closed to new customers since 2026-04-30.
