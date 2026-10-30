# DoubleBlind

Two companies' AI agents build an integration between their systems. Neither agent sees the other company's code.

- Demo video: https://youtu.be/9-p-VpS4cKQ
- Live demo: https://13.60.103.181.sslip.io

## The problem

Companies connect their systems all the time: a shop to a courier, a merchant to a payment provider, a marketplace to its sellers, a supplier to a retailer. Each time, two teams have to make two private systems fit. Neither team can see the other's code, so they trade docs, emails and test calls for weeks.

This is common. In the State of SaaS Integrations 2024 report, 75% of B2B companies say one integration takes three months or more. In Cleo's 2023 survey, onboarding one EDI trading partner takes a week to a month for 44% of companies and more than a month for 16%. Both numbers come from vendors of integration tools.

Most of that time is not coding. It is coordination across the company boundary. Each side knows its own rules, edge cases and data formats. The other side only learns them by asking or by failing a test.

## The idea

Give each company its own agent that reads only its own code. The two agents agree on a contract with executable tests. Each agent then builds its own side. A neutral gateway runs both real systems against each other and certifies the result.

Only the contract and test results cross the company boundary. Code, prompts and internal docs stay inside each company.

## How it works

1. **Scope.** One company creates the integration, picks its side and describes the goal in one or two sentences. It invites the partner. The partner joins and accepts the scope.
2. **Connect agents.** Each company runs one command in its own repository. The agent starts next to the code, with that company's own Nebius Token Factory key.
3. **Private model and brief.** Each agent maps its own code into a private system model. It shares only a short brief: what it offers, what it needs and which rules apply.
4. **Negotiation.** The agents take turns editing one shared contract: operations, webhooks, schemas, business decisions and test scenarios. When a decision belongs to the business, the agent asks its own people in the dashboard. The partner does not see these questions.
5. **Signatures.** People at both companies review the contract and sign the same version.
6. **Implementation.** Each agent writes code for its own side inside its own sandbox and runs the contract tests there. The consumer tests against a stub of the provider. The provider tests by sending real webhooks to a capture server.
7. **End-to-end test.** Both real systems start in their own sandboxes. They talk only through the DoubleBlind gateway, which checks every request and response against the signed contract. If a scenario fails, both agents get a sanitized failure report and fix their own side.
8. **Certificate and patch.** When every scenario passes, DoubleBlind issues a certificate with an audit hash chain of all events. Each company gets a pull request description and a git patch in its own repository.

## What protects each company's code

- **Separate agents and sandboxes.** Each agent reads only its own repository. It runs in its company's sandbox, paid from its company's account.
- **Egress guard.** Every message that leaves a company passes two checks. Deterministic rules block code lines, secrets, long tokens, stack traces and personal data that is not in the company's synthetic test data. Then Nemotron 3.5 Lightning classifies the message and can block it or redact fragments. A blocked message goes back to the agent, which rewrites it up to three times.
- **Gateway.** In the end-to-end test, a request that is not in the contract is refused. Schema mismatches are counted as violations.
- **Company view.** Each company sees its own agent, its own questions and the shared contract. The partner's code, prompts, questions and logs show as locked tiles.

## NVIDIA Nemotron models

Each step uses a different Nemotron model on Nebius Token Factory. The mapping lives in `config/models.yaml`.

| Model                  | Job                                                                                            |
| ---------------------- | ---------------------------------------------------------------------------------------------- |
| Nemotron 3 Ultra 550B  | negotiates the contract                                                                        |
| Nemotron 3.5 Super VL  | writes the brief, writes the code, diagnoses failing tests, fixes rejected contract drafts     |
| Nemotron 3.5 Lightning | maps the company's code into a private system model, egress classifier, repairs malformed JSON |

Token Factory retires Nemotron 3 Super 120B and Nemotron 3 Nano 30B on November 9, 2026. The code uses the replacements Nebius recommends: Nemotron 3.5 Super VL for Super and Nemotron 3.5 Lightning for Nano. The official run below, the demo video and the certificate were made with the old models: Super 120B did the jobs of Super VL, and Nano 30B was the egress classifier and repaired JSON. I checked every role on the new models with its real settings, and the egress classifier scored the same as Nano on the leak test set.

The Cost tab in the Observer view shows calls, tokens and cost for each model and the cost of each sandbox.

## Nebius Token Factory sandboxes

- The agent packs the company's repository into a sandbox image. It installs dependencies once, using the `install` commands from the company's `doubleblind.yaml`, and tags the image for reuse.
- Every command of the coding agent runs as a job on that image: reading files, editing code, running tests. Networking is off. Each step produces a new image, so the work continues from the last state.
- For the end-to-end test, the company's app starts as a service in its sandbox. Its traffic is relayed through the gateway, never directly to the partner.
- The sandboxes and the model calls are billed to the company that runs the agent.

## Where Token Factory helped

- **One API for every model.** All Nemotron models sit behind one OpenAI-compatible endpoint. Moving a job to a bigger or smaller model is a one-line change in `config/models.yaml`, so I could give the hard reasoning to Ultra and the cheap checks to a small model without hosting anything.
- **Sandboxes without infrastructure.** Each company needs its own isolated machine with the network off. Token Factory sandboxes give that through the same account and API key, so a company only runs one command and nothing has to be set up on its side.
- **Image per step.** Every sandbox command returns a new image. The coding agent continues from the last good state, and the end-to-end test starts each company's app from the image its agent produced.
- **Low cost per run.** A full live integration, from scope to certificate, cost $3.92 for models and sandboxes together. That made it possible to iterate on real runs instead of mocks.

## A real run

Spokes, a bike shop written in Python, connects to Parcelly, a courier written in Node.js. I wrote both demo repositories for this project. Each one has internal rules that are not in its public docs.

Run `ses_6c794c434937`, live on Token Factory with Ultra 550B, Super 120B, Lightning and Nano 30B:

|                                |                                                        |
| ------------------------------ | ------------------------------------------------------ |
| Time from scope to certificate | 57 min 56 s                                            |
| Negotiation                    | 11 rounds, contract version 14                         |
| Questions for people           | 5                                                      |
| Scenarios                      | 8 of 8 green: Spokes 2/2, Parcelly 5/5, end-to-end 1/1 |
| End-to-end attempts            | 2. The first failed, both agents fixed their side      |
| Gateway                        | 46 requests, 0 refused, 0 violations                   |
| Egress guard                   | 1 message blocked, 1 redacted                          |
| Cost                           | $3.92: $3.02 models, $0.90 sandboxes                   |
| Tokens                         | 8,215,490                                              |
| Code shared between companies  | 0 lines                                                |
| Patch                          | Parcelly 8 files, +179 −7. Spokes 6 files, +506 −1     |

You can replay this run on the live demo with "Watch the recorded run".

## Try it

### Watch the recorded run

Open https://13.60.103.181.sslip.io and press "Watch the recorded run". No account is needed. Switch "View as" between Spokes, Parcelly and Observer to see what each company sees.

### Run an integration with your company

Each company needs a Nebius Token Factory account, Python 3.12 or newer and `pipx`.

1. On the live demo, one company creates an account, creates an integration and invites the partner. The partner creates its own account and joins.
2. Each company installs the agent once:

   ```bash
   pipx install git+https://github.com/galileo680/doubleblind
   ```

3. In the root of each repository, add a `.env` file with the company's own Token Factory credentials:

   ```bash
   NEBIUS_API_KEY=...
   NEBIUS_PROJECT_ID=...
   ```

4. Add a `doubleblind.yaml` that says how to install the app in the sandbox and which test data may appear in messages. See `demo/spokes/doubleblind.yaml` and `demo/parcelly/doubleblind.yaml`.
5. In the integration page, press "Generate agent key" and run the command it shows in the repository:

   ```bash
   doubleblind connect --broker https://13.60.103.181.sslip.io --key <agent key>
   ```

6. When both agents are online, press "Start".

To try it without your own code, run the two agents in `demo/parcelly` and `demo/spokes`.

### Run the broker yourself

```bash
docker compose up -d --build broker
```

The broker and the dashboard run on http://localhost:8000. Pass `--broker http://localhost:8000` to `doubleblind connect`. Settings are in `.env.example`. With `DOUBLEBLIND_SANDBOX=docker` the agents use local Docker instead of Token Factory sandboxes.

## Repository layout

| Path                | What it is                                                                                 |
| ------------------- | ------------------------------------------------------------------------------------------ |
| `packages/agent`    | the company agent and the `doubleblind connect` command                                    |
| `packages/broker`   | FastAPI broker: sessions, negotiation, gateway, certificates. It also serves the dashboard |
| `packages/contract` | contract format, test runner, provider stub, webhook capture server, egress rules          |
| `packages/llm`      | Token Factory client, model roles, spend ledger                                            |
| `packages/sandbox`  | Token Factory sandboxes, Docker fallback, sandbox images                                   |
| `dashboard`         | React dashboard                                                                            |
| `demo/spokes`       | demo bike shop in Python                                                                   |
| `demo/parcelly`     | demo courier in Node.js                                                                    |
| `demo/recordings`   | the recorded certified run shown on the home page                                          |
| `config`            | models, roles and prices                                                                   |

## Development

```bash
python scripts/tasks.py install
python scripts/tasks.py lint
python scripts/tasks.py test
```

The same tasks are available through `make`. `npm run test:e2e` in `dashboard` runs a Playwright test of a full session with scripted agents, at no model cost.

## Limitations

- A live run takes about an hour and costs a few dollars.
- Live runs do not always certify. My first certified run came after 16 attempts. When negotiation reaches the round limit, people can allow 6 more rounds or stop the run.
- I tested it on the two demo repositories I wrote, not on real company codebases yet.
- The egress guard lowers the risk of a leak. It does not prove that nothing leaked.
- I tried Tavily web search for the negotiator. It made negotiations worse, so it is off by default.
- DoubleBlind speeds up engineering and contract negotiation. It does not replace legal agreements, security reviews or regulatory certification.

## License

Apache 2.0
