# Contact

*Abhishek Mishra — engineer behind Aegis. The fastest way to reach me is the channel that matches the conversation you want to have.*

## Channels

| Channel | What it's good for |
|---|---|
| **Email** — [abhimishra9896@gmail.com](mailto:abhimishra9896@gmail.com) | Roles, partnerships, anything that benefits from a written record. I read it daily. |
| **LinkedIn** — [linkedin.com/in/abhishek-mishra-eng](https://www.linkedin.com/in/abhishek-mishra-eng) | Career conversations, recruiters, hiring managers, references. Connection requests with a one-line context are welcome. |
| **Portfolio** — [portfolio-self-seven-1zphd40voq.vercel.app](https://portfolio-self-seven-1zphd40voq.vercel.app) | The wider body of work — Aegis is one project among several. Shorter form than this GitBook. |
| **Hashnode** — [hashnode.com/@abhimishra-devops90](https://hashnode.com/@abhimishra-devops90) | Long-form engineering writing. The Aegis design rationale is published there in depth; new posts on AI-agent security, runtime governance, and platform engineering land there first. |
| **GitHub** — [github.com/Abhi-mishra998](https://github.com/Abhi-mishra998) | Code review, issues against Aegis or any of my other repositories, pull requests. Public commit history is the unfiltered version of the work in this GitBook. |

## What this project is

I built Aegis as a single-purpose runtime control plane for AI agents — an enforcement seam between the agent and the systems it acts on. The full design rationale, the architecture, the trade-offs, and the failure modes are documented in the rest of this GitBook. The repository is at [github.com/Abhi-mishra998/aegis](https://github.com/Abhi-mishra998/aegis).

The work spans:

- **Backend** — 13 FastAPI microservices, a hybrid Postgres + Redis + OPA stack, an ed25519-signed audit chain with Merkle transparency roots, transactional outbox for billing durability, and an eleven-stage middleware pipeline that gates every tool call.
- **Frontend** — a 38-page React + Tailwind UI wired entirely to live APIs, with an embedded LiveKit-powered voice agent in the navbar.
- **Infrastructure** — Terraform-managed AWS deployment (Graviton EC2, RDS, ElastiCache, S3, Secrets Manager) reproducible from `terraform destroy && terraform apply`. No GitHub credentials on production hosts.
- **AI-agent security specifically** — prompt-injection classifier, behavioural firewall, autonomy contracts, blast-radius simulation, kill switch with sub-5-second tenant-wide propagation, cryptographically verifiable audit trail.

If any of that maps to something you're working on, the channels above are the right places to start.

## What I'd like to hear about

- **Roles** — senior / staff / principal positions where this kind of platform engineering work matters. Open to remote and to relocation. Comfortable with hybrid threat-modelling + implementation + operating the production system through incident response.
- **Technical conversations** — if you're building an AI-agent platform and want to compare notes on runtime enforcement, audit-chain design, transactional billing, or the OPA / behavioural / decision-engine layering, I'm always interested.
- **Customer or evaluator inquiries** — if you're evaluating Aegis (or runtime governance more broadly) for a production deployment, ask. I can walk through the architecture, the threat model, and the operational trade-offs in detail.
- **Bug reports and pull requests** — open an issue on the [repository](https://github.com/Abhi-mishra998/aegis/issues); the bar is "we'd both rather know than not know."

## Response expectations

I work from `ap-south-1` time (UTC+5:30). Email and LinkedIn within one business day; GitHub issues within 48 hours; PRs reviewed on weekday evenings.

For anything time-sensitive — incident-class questions, production support requests — flag the urgency in the subject line and I'll prioritise it.
