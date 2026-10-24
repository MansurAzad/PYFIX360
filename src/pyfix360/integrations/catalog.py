"""Curated catalogue of external tools, CLIs, services and AI app builders.

Data only. Each entry is self-contained so the UI and the analyzer can use
it without importing third-party modules.

Two kinds of entry live here:

* **tools** — CLIs and services (git, GitHub, Vercel, Docker, ...). They are
  driven by a command line and are documented as command references.
* **platforms** — AI app builders and prompt workbenches (Lovable, Trickle,
  Replit, Emergent, v0, Builder.io, Google AI Studio). They are driven by
  natural-language prompts inside a web UI, so they carry a *playbook*:
  prompt patterns, the command dialect, the questions the platform asks and
  how to answer them, and the situations that come up mid-build.

Honesty rules for platform entries:

* ``verified_live`` is ``False`` unless PYFIX has actually driven that
  platform. It defaults to ``False`` for every entry, including the ones
  written from published documentation.
* ``reviewed_on`` records when a human last read the vendor's own docs.
* These services change their UI, their credit rules and their API surface
  without notice. The playbook is a starting point to verify, never a
  guarantee. Each entry says so in its ``pitfalls``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


def _with_cli_flag(entry: Integration) -> Integration:
    """Keep ``has_cli`` in step with the CLI binary the entry actually names."""
    return replace(entry, has_cli=bool(entry.cli))


@dataclass(frozen=True)
class Command:
    """A representative CLI command for an integration."""

    cmd: str
    purpose: str
    notes: str = ""


@dataclass(frozen=True)
class PromptPattern:
    """A prompt template that works well on a specific platform."""

    title: str
    prompt: str
    notes: str = ""


@dataclass(frozen=True)
class QuestionAnswer:
    """A question the platform asks, and how to answer it."""

    question: str
    answer: str
    notes: str = ""


@dataclass(frozen=True)
class Situation:
    """A situation that comes up mid-build, and the recommended response."""

    situation: str
    response: str
    severity: str = "info"    # "info" | "caution" | "blocking"


@dataclass(frozen=True)
class Integration:
    """One external tool / service / CLI / app-builder platform."""

    key: str
    label: str
    category: str            # "vcs", "hosting", "database", "auth", "ci", "container", "package", "observability", ...
    homepage: str = ""
    cli: str = ""           # name of the CLI binary, if any
    install: str = ""       # how to obtain the CLI
    auth: str = ""          # how authentication works
    summary: str = ""
    detection: tuple[str, ...] = ()   # file names / markers used for static detection
    commands: tuple[Command, ...] = ()
    pitfalls: tuple[str, ...] = ()
    docs_url: str = ""

    # --- platform playbook (empty for plain CLI tools) --------------------
    kind: str = "tool"                    # "tool" | "platform"
    has_cli: bool = False
    api: str = ""                         # API base URL, when one exists
    api_key_header: str = ""              # header the key is sent in
    api_key_url: str = ""                 # where the user obtains a key
    api_capabilities: tuple[str, ...] = ()  # what the API can actually do
    deep_link: str = ""                   # URL template that prefills a prompt
    prompt_patterns: tuple[PromptPattern, ...] = ()
    command_dialect: tuple[str, ...] = ()
    qa_flow: tuple[QuestionAnswer, ...] = ()
    situations: tuple[Situation, ...] = ()
    operation_mode: str = ""              # "api" | "browser" | "both" | ""
    verified_live: bool = False
    reviewed_on: str = ""


_TOOL_ENTRIES: tuple[Integration, ...] = (

    # ---------------------------------------------------------------- VCS
    Integration(
        key="git", label="Git", category="vcs",
        homepage="https://git-scm.com",
        cli="git", install="https://git-scm.com/downloads (or OS package manager)",
        auth="SSH keys / HTTPS tokens / credential helpers",
        summary="Distributed version control. The baseline every project starts from.",
        detection=(".git", ".gitignore", ".gitattributes"),
        commands=(
            Command("git init", "Initialize a repository"),
            Command("git status", "Working tree status"),
            Command("git add -A && git commit -m 'msg'", "Stage and commit"),
            Command("git branch -M main", "Rename current branch to main"),
            Command("git remote add origin <url>", "Add a remote"),
            Command("git push -u origin main", "Push and set upstream"),
            Command("git log --oneline --graph --all", "Compact history graph"),
            Command("git stash / git stash pop", "Stash working changes"),
            Command("git cherry-pick <sha>", "Apply a single commit"),
        ),
        pitfalls=(
            "Committing secrets — always check .gitignore before the first push.",
            "Detached HEAD after checkout of a tag/commit.",
            "Large files bloat history; use Git LFS.",
        ),
        docs_url="https://git-scm.com/doc",
    ),

    # ---------------------------------------------------------------- GitHub
    Integration(
        key="github", label="GitHub", category="vcs",
        homepage="https://github.com",
        cli="gh", install="https://cli.github.com (winget/brew/apt)",
        auth="`gh auth login` (browser or token); supports SSH/HTTPS remotes",
        summary="Git hosting plus issues, PRs, Actions, releases and packages.",
        detection=(".github", ".github/workflows", "github.com" ),
        commands=(
            Command("gh auth login", "Authenticate the CLI"),
            Command("gh repo create <name> --public --source=. --remote=origin --push", "Create repo and push"),
            Command("gh repo clone owner/repo", "Clone a repository"),
            Command("gh pr create --fill", "Open a PR from the current branch"),
            Command("gh pr list / gh pr view <n>", "List / inspect PRs"),
            Command("gh pr merge <n> --squash --delete-branch", "Squash-merge a PR"),
            Command("gh issue create --title '...' --body '...'", "Open an issue"),
            Command("gh issue list --assignee @me", "My open issues"),
            Command("gh run list / gh run watch", "CI run status"),
            Command("gh release create v1.0.0 --generate-notes", "Create a release"),
            Command("gh secret set NAME --body value", "Set a repository secret"),
            Command("gh workflow run <file>", "Trigger a workflow manually"),
        ),
        pitfalls=(
            "gh auth token is stored in the OS keyring; on headless machines use GH_TOKEN env var.",
            "Actions secrets are per-repo; org secrets need admin rights.",
            "Default branch rename (master -> main) must be done both locally and on GitHub.",
        ),
        docs_url="https://cli.github.com/manual",
    ),

    # ---------------------------------------------------------------- GitLab
    Integration(
        key="gitlab", label="GitLab", category="vcs",
        homepage="https://gitlab.com",
        cli="glab", install="https://gitlab.com/gitlab-org/cli (brew/apt/winget)",
        auth="`glab auth login` (token or OAuth)",
        summary="Git hosting with built-in CI/CD, registry and issue tracking.",
        detection=(".gitlab-ci.yml", "gitlab.com"),
        commands=(
            Command("glab auth login", "Authenticate"),
            Command("glab repo create <name> --public", "Create a project"),
            Command("glab mr create --fill", "Open a merge request"),
            Command("glab mr list", "List merge requests"),
            Command("glab ci status / glab ci view", "Pipeline status"),
            Command("glab issue create", "Open an issue"),
            Command("glab release create v1.0.0", "Create a release"),
        ),
        pitfalls=(
            "Self-hosted GitLab needs GITLAB_HOST env var.",
            "CI variables masked/protected flags must be set per environment.",
        ),
        docs_url="https://gitlab.com/gitlab-org/cli/-/tree/main/docs",
    ),

    # ---------------------------------------------------------------- Vercel
    Integration(
        key="vercel", label="Vercel", category="hosting",
        homepage="https://vercel.com",
        cli="vercel", install="npm i -g vercel",
        auth="`vercel login` (email/GitHub/GitLab/Bitbucket) or VERCEL_TOKEN",
        summary="Frontend/serverless hosting with preview deployments per branch.",
        detection=("vercel.json", ".vercel", "vercel.com"),
        commands=(
            Command("vercel login", "Authenticate"),
            Command("vercel", "Deploy (interactive, creates project)"),
            Command("vercel --prod", "Deploy to production"),
            Command("vercel dev", "Local dev server mirroring platform"),
            Command("vercel env add NAME production", "Add an env var"),
            Command("vercel env ls", "List env vars"),
            Command("vercel env pull .env.local", "Pull env vars to file"),
            Command("vercel domains add example.com", "Add a domain"),
            Command("vercel logs <deployment>", "Tail deployment logs"),
            Command("vercel inspect <url> --logs", "Inspect a deployment"),
            Command("vercel rollback", "Roll back production"),
        ),
        pitfalls=(
            "Preview env vars vs production env vars are separate scopes.",
            "Serverless function size/time limits (Hobby plan).",
            "Framework detection can be overridden in vercel.json `builds`.",
            "Never commit .vercel/project.json if it contains secrets (it usually doesn't).",
        ),
        docs_url="https://vercel.com/docs/cli",
    ),

    # ---------------------------------------------------------------- Netlify
    Integration(
        key="netlify", label="Netlify", category="hosting",
        homepage="https://netlify.com",
        cli="netlify", install="npm i -g netlify-cli",
        auth="`netlify login` or NETLIFY_AUTH_TOKEN",
        summary="Static/JAMstack hosting with forms, functions and deploy previews.",
        detection=("netlify.toml", ".netlify"),
        commands=(
            Command("netlify login", "Authenticate"),
            Command("netlify init", "Link/create a site"),
            Command("netlify dev", "Local dev with functions"),
            Command("netlify deploy", "Draft deploy"),
            Command("netlify deploy --prod", "Production deploy"),
            Command("netlify env:set KEY value", "Set env var"),
            Command("netlify env:list", "List env vars"),
            Command("netlify functions:create", "Scaffold a function"),
            Command("netlify open", "Open site in browser"),
        ),
        pitfalls=(
            "Draft vs prod deploys are different commands.",
            "Function timeout limits on free tier.",
        ),
        docs_url="https://docs.netlify.com/cli/get-started/",
    ),

    # ---------------------------------------------------------------- Supabase
    Integration(
        key="supabase", label="Supabase", category="database",
        homepage="https://supabase.com",
        cli="supabase", install="npm i -g supabase (or brew/scoop)",
        auth="`supabase login` (access token) or SUPABASE_ACCESS_TOKEN",
        summary="Postgres + auth + storage + realtime + edge functions, with local dev via Docker.",
        detection=("supabase", "supabase/config.toml", "supabase/migrations", "@supabase/supabase-js", "supabase-py"),
        commands=(
            Command("supabase login", "Authenticate"),
            Command("supabase init", "Initialize project (creates supabase/ dir)"),
            Command("supabase start", "Start local stack (Docker)"),
            Command("supabase stop", "Stop local stack"),
            Command("supabase status", "Show local service URLs/keys"),
            Command("supabase db reset", "Reset local DB and re-run migrations+seed"),
            Command("supabase migration new <name>", "Create a migration file"),
            Command("supabase db diff -f <name>", "Generate migration from schema diff"),
            Command("supabase db push", "Push migrations to remote project"),
            Command("supabase db pull", "Pull remote schema into migrations"),
            Command("supabase gen types typescript --local > types.ts", "Generate TS types"),
            Command("supabase functions new <name>", "Scaffold an edge function"),
            Command("supabase functions deploy <name>", "Deploy an edge function"),
            Command("supabase secrets set KEY=value", "Set function secret"),
            Command("supabase link --project-ref <ref>", "Link to a remote project"),
        ),
        pitfalls=(
            "Local stack requires Docker Desktop running.",
            "Service role key bypasses RLS — never ship it to the client.",
            "Migrations must be idempotent; `db reset` wipes data.",
            "RLS policies are the real security boundary, not the API key.",
        ),
        docs_url="https://supabase.com/docs/guides/cli",
    ),

    # ---------------------------------------------------------------- Firebase
    Integration(
        key="firebase", label="Firebase", category="backend",
        homepage="https://firebase.google.com",
        cli="firebase", install="npm i -g firebase-tools",
        auth="`firebase login` (Google account)",
        summary="Google's app platform: Firestore, Auth, Hosting, Functions, Storage.",
        detection=("firebase.json", ".firebaserc", "firebase"),
        commands=(
            Command("firebase login", "Authenticate"),
            Command("firebase init", "Scaffold project config"),
            Command("firebase emulators:start", "Run local emulators"),
            Command("firebase deploy", "Deploy everything"),
            Command("firebase deploy --only hosting", "Deploy only hosting"),
            Command("firebase deploy --only functions", "Deploy only functions"),
            Command("firebase functions:log", "View function logs"),
        ),
        pitfalls=(
            "Security rules are the real boundary; test them with the emulator.",
            "Blaze plan required for outbound network in functions.",
        ),
        docs_url="https://firebase.google.com/docs/cli",
    ),

    # ---------------------------------------------------------------- Docker
    Integration(
        key="docker", label="Docker", category="container",
        homepage="https://docker.com",
        cli="docker", install="https://docs.docker.com/get-docker/",
        auth="`docker login` (registry credentials)",
        summary="Container build/run/ship. Foundation for reproducible environments.",
        detection=("Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"),
        commands=(
            Command("docker build -t app:latest .", "Build an image"),
            Command("docker run -p 8000:8000 app:latest", "Run a container"),
            Command("docker compose up -d", "Start compose stack (detached)"),
            Command("docker compose down -v", "Stop and remove volumes"),
            Command("docker compose logs -f", "Follow logs"),
            Command("docker ps -a", "List containers"),
            Command("docker images", "List images"),
            Command("docker exec -it <ctr> sh", "Shell into a container"),
            Command("docker system prune -af", "Clean unused data (destructive)"),
        ),
        pitfalls=(
            "Layer caching: order Dockerfile instructions to maximize cache hits.",
            "Don't store secrets in images; use build args/secrets.",
            "Volume mounts on Windows need path translation.",
        ),
        docs_url="https://docs.docker.com/reference/cli/docker/",
    ),

    # ---------------------------------------------------------------- AWS
    Integration(
        key="aws", label="AWS CLI", category="cloud",
        homepage="https://aws.amazon.com/cli/",
        cli="aws", install="https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html",
        auth="`aws configure` (access key + secret + region) or SSO via `aws sso login`",
        summary="Amazon Web Services command-line interface.",
        detection=(".aws", "aws", "boto3", "botocore"),
        commands=(
            Command("aws configure", "Set credentials/region/output"),
            Command("aws s3 ls", "List S3 buckets"),
            Command("aws s3 sync ./dist s3://bucket", "Sync folder to S3"),
            Command("aws sts get-caller-identity", "Who am I"),
            Command("aws lambda list-functions", "List Lambda functions"),
            Command("aws cloudformation deploy --template-file t.yml --stack-name s", "Deploy stack"),
        ),
        pitfalls=(
            "Credentials in ~/.aws/credentials — protect the file (chmod 600).",
            "Region mismatch is the #1 cause of 'resource not found'.",
            "Use IAM roles on EC2/ECS instead of long-lived keys.",
        ),
        docs_url="https://docs.aws.amazon.com/cli/",
    ),

    # ---------------------------------------------------------------- GCP
    Integration(
        key="gcp", label="Google Cloud (gcloud)", category="cloud",
        homepage="https://cloud.google.com/sdk",
        cli="gcloud", install="https://cloud.google.com/sdk/docs/install",
        auth="`gcloud auth login` / `gcloud auth application-default login`",
        summary="Google Cloud Platform CLI.",
        detection=("gcloud", "google-cloud", "firebase.json"),
        commands=(
            Command("gcloud auth login", "Authenticate user"),
            Command("gcloud config set project <id>", "Set active project"),
            Command("gcloud run deploy", "Deploy to Cloud Run"),
            Command("gcloud app deploy", "Deploy App Engine"),
            Command("gcloud storage cp file gs://bucket/", "Copy to GCS"),
        ),
        pitfalls=(
            "Application Default Credentials vs user credentials are different.",
            "Quota project must be set for some APIs.",
        ),
        docs_url="https://cloud.google.com/sdk/gcloud/reference",
    ),

    # ---------------------------------------------------------------- Azure
    Integration(
        key="azure", label="Azure CLI", category="cloud",
        homepage="https://learn.microsoft.com/cli/azure/",
        cli="az", install="https://learn.microsoft.com/cli/azure/install-azure-cli",
        auth="`az login` (browser/device code)",
        summary="Microsoft Azure command-line interface.",
        detection=("azure", "azure-pipelines.yml", ".azure"),
        commands=(
            Command("az login", "Authenticate"),
            Command("az account set --subscription <id>", "Select subscription"),
            Command("az group create -n rg -l eastus", "Create resource group"),
            Command("az webapp up --name app --runtime PYTHON:3.11", "Deploy web app"),
            Command("az acr build -t img:tag .", "Build in ACR"),
        ),
        pitfalls=(
            "Subscription context resets between shells unless set persistently.",
        ),
        docs_url="https://learn.microsoft.com/cli/azure/reference-index",
    ),

    # ---------------------------------------------------------------- npm / pnpm / yarn / bun
    Integration(
        key="npm", label="npm", category="package",
        homepage="https://npmjs.com",
        cli="npm", install="Bundled with Node.js (https://nodejs.org)",
        auth="`npm login` (registry token)",
        summary="Node.js package manager.",
        detection=("package.json", "package-lock.json", "node_modules"),
        commands=(
            Command("npm install", "Install dependencies"),
            Command("npm ci", "Clean install from lockfile (CI)"),
            Command("npm run <script>", "Run a package script"),
            Command("npm test", "Run tests"),
            Command("npm run build", "Build"),
            Command("npm publish", "Publish a package"),
            Command("npm outdated / npm audit", "Check outdated / vulnerabilities"),
        ),
        pitfalls=(
            "package-lock.json must be committed for reproducible installs.",
            "npm audit fix can introduce breaking changes.",
        ),
        docs_url="https://docs.npmjs.com/cli/",
    ),
    Integration(
        key="pnpm", label="pnpm", category="package",
        homepage="https://pnpm.io",
        cli="pnpm", install="npm i -g pnpm  (or corepack enable)",
        auth="npm registry token via .npmrc",
        summary="Fast, disk-efficient Node package manager.",
        detection=("pnpm-lock.yaml", "pnpm-workspace.yaml"),
        commands=(
            Command("pnpm install", "Install"),
            Command("pnpm add <pkg>", "Add dependency"),
            Command("pnpm run <script>", "Run script"),
            Command("pnpm -r build", "Build all workspace packages"),
        ),
        pitfalls=(
            "Strict node_modules layout can break packages relying on hoisting.",
        ),
        docs_url="https://pnpm.io/cli/install",
    ),

    # ---------------------------------------------------------------- Python tooling
    Integration(
        key="uv", label="uv", category="package",
        homepage="https://docs.astral.sh/uv/",
        cli="uv", install="pip install uv / curl installer / brew",
        auth="none (PyPI token via UV_PUBLISH_TOKEN for publish)",
        summary="Extremely fast Python package manager and project tool.",
        detection=("uv.lock", "pyproject.toml"),
        commands=(
            Command("uv init", "Initialize a project"),
            Command("uv add <pkg>", "Add a dependency"),
            Command("uv sync", "Sync environment from lockfile"),
            Command("uv run pytest", "Run a command in the managed env"),
            Command("uv build", "Build sdist/wheel"),
            Command("uv publish", "Publish to PyPI"),
            Command("uv python install 3.12", "Install a Python version"),
        ),
        pitfalls=(
            "uv.lock must be committed.",
            "Mixing pip and uv in the same venv causes drift.",
        ),
        docs_url="https://docs.astral.sh/uv/reference/cli/",
    ),
    Integration(
        key="poetry", label="Poetry", category="package",
        homepage="https://python-poetry.org",
        cli="poetry", install="https://python-poetry.org/docs/#installation",
        auth="PyPI token via `poetry config pypi-token.pypi <token>`",
        summary="Python dependency management and packaging.",
        detection=("poetry.lock", "pyproject.toml"),
        commands=(
            Command("poetry init", "Initialize"),
            Command("poetry add <pkg>", "Add dependency"),
            Command("poetry install", "Install"),
            Command("poetry run pytest", "Run in venv"),
            Command("poetry build", "Build"),
            Command("poetry publish", "Publish"),
            Command("poetry lock --no-update", "Refresh lock without upgrading"),
        ),
        pitfalls=(
            "poetry.lock must be committed.",
            "Solver can be slow on large dependency trees.",
        ),
        docs_url="https://python-poetry.org/docs/cli/",
    ),

    # ---------------------------------------------------------------- Prisma / Drizzle / Alembic (DB tooling)
    Integration(
        key="prisma", label="Prisma", category="database",
        homepage="https://prisma.io",
        cli="prisma", install="npm i -g prisma (or npx prisma)",
        auth="DATABASE_URL env var",
        summary="TypeScript/Node ORM with migrations and a studio UI.",
        detection=("prisma/schema.prisma", "prisma/migrations"),
        commands=(
            Command("prisma init", "Initialize"),
            Command("prisma migrate dev --name init", "Create+apply migration (dev)"),
            Command("prisma migrate deploy", "Apply migrations (prod)"),
            Command("prisma generate", "Generate client"),
            Command("prisma studio", "Open DB browser"),
            Command("prisma db pull", "Introspect existing DB"),
            Command("prisma db push", "Push schema without migration (prototyping)"),
        ),
        pitfalls=(
            "migrate dev is for dev only; prod uses migrate deploy.",
            "Schema drift between DB and schema.prisma.",
        ),
        docs_url="https://www.prisma.io/docs/reference/api-reference/command-reference",
    ),

    # ---------------------------------------------------------------- Stripe
    Integration(
        key="stripe", label="Stripe CLI", category="payments",
        homepage="https://stripe.com/docs/stripe-cli",
        cli="stripe", install="https://stripe.com/docs/stripe-cli#install",
        auth="`stripe login` (browser) or STRIPE_API_KEY",
        summary="Payments platform CLI: webhooks, test data, logs.",
        detection=("stripe", "@stripe/stripe-js", "stripe-python"),
        commands=(
            Command("stripe login", "Authenticate"),
            Command("stripe listen --forward-to localhost:3000/webhook", "Forward webhooks locally"),
            Command("stripe trigger payment_intent.succeeded", "Trigger test event"),
            Command("stripe logs tail", "Tail API logs"),
            Command("stripe customers list", "List customers"),
        ),
        pitfalls=(
            "Webhook signing secret differs between listen and production.",
            "Test mode vs live mode keys.",
        ),
        docs_url="https://stripe.com/docs/stripe-cli",
    ),

    # ---------------------------------------------------------------- Terraform
    Integration(
        key="terraform", label="Terraform", category="iac",
        homepage="https://developer.hashicorp.com/terraform",
        cli="terraform", install="https://developer.hashicorp.com/terraform/install",
        auth="Provider-specific (AWS creds, GCP SA, Azure SP)",
        summary="Infrastructure as code for cloud resources.",
        detection=("*.tf", ".terraform", "terraform.tfstate"),
        commands=(
            Command("terraform init", "Initialize providers/modules"),
            Command("terraform plan", "Preview changes"),
            Command("terraform apply", "Apply changes"),
            Command("terraform destroy", "Destroy all resources"),
            Command("terraform fmt -recursive", "Format code"),
            Command("terraform validate", "Validate config"),
            Command("terraform state list", "List tracked resources"),
        ),
        pitfalls=(
            "State file contains secrets; use remote backend with locking.",
            "Never commit .terraform/ or *.tfstate.",
        ),
        docs_url="https://developer.hashicorp.com/terraform/cli/commands",
    ),

    # ---------------------------------------------------------------- Fly.io
    Integration(
        key="fly", label="Fly.io", category="hosting",
        homepage="https://fly.io",
        cli="fly", install="https://fly.io/docs/hands-on/install-flyctl/",
        auth="`fly auth login` or FLY_API_TOKEN",
        summary="Run full-stack apps close to users on a global edge.",
        detection=("fly.toml",),
        commands=(
            Command("fly launch", "Create app from current dir"),
            Command("fly deploy", "Deploy"),
            Command("fly status", "App status"),
            Command("fly logs", "Tail logs"),
            Command("fly ssh console", "SSH into a machine"),
            Command("fly secrets set KEY=value", "Set secret"),
            Command("fly scale count 2", "Scale"),
        ),
        pitfalls=(
            "Free allowance is usage-based; watch billing.",
        ),
        docs_url="https://fly.io/docs/flyctl/",
    ),

    # ---------------------------------------------------------------- Render
    Integration(
        key="render", label="Render", category="hosting",
        homepage="https://render.com",
        cli="render", install="npm i -g @render/cli (beta)",
        auth="RENDER_API_KEY",
        summary="Managed hosting for web services, static sites, cron, databases.",
        detection=("render.yaml",),
        commands=(
            Command("render services list", "List services"),
            Command("render deploys create <srv>", "Trigger a deploy"),
            Command("render logs <srv>", "Tail logs"),
        ),
        pitfalls=(
            "render.yaml is the IaC entry point; keep it in repo root.",
        ),
        docs_url="https://render.com/docs/cli",
    ),

    # ---------------------------------------------------------------- Railway
    Integration(
        key="railway", label="Railway", category="hosting",
        homepage="https://railway.app",
        cli="railway", install="npm i -g @railway/cli",
        auth="`railway login`",
        summary="Deploy apps and databases with minimal config.",
        detection=("railway.json", "railway.toml"),
        commands=(
            Command("railway login", "Authenticate"),
            Command("railway init", "Initialize project"),
            Command("railway up", "Deploy"),
            Command("railway logs", "Tail logs"),
            Command("railway variables set KEY=value", "Set variable"),
            Command("railway run <cmd>", "Run command with injected env"),
            Command("railway shell", "Open shell with env"),
        ),
        pitfalls=(
            "Env vars set in dashboard vs CLI must be kept in sync.",
        ),
        docs_url="https://docs.railway.app/reference/cli-api",
    ),

    # ---------------------------------------------------------------- Heroku
    Integration(
        key="heroku", label="Heroku", category="hosting",
        homepage="https://heroku.com",
        cli="heroku", install="https://devcenter.heroku.com/articles/heroku-cli",
        auth="`heroku login`",
        summary="Classic PaaS for web apps and add-ons.",
        detection=("Procfile", "app.json"),
        commands=(
            Command("heroku login", "Authenticate"),
            Command("heroku create <name>", "Create app"),
            Command("git push heroku main", "Deploy"),
            Command("heroku logs --tail", "Tail logs"),
            Command("heroku config:set KEY=value", "Set config var"),
            Command("heroku run python manage.py migrate", "Run one-off dyno"),
            Command("heroku ps:scale web=2", "Scale"),
        ),
        pitfalls=(
            "Ephemeral filesystem; use S3/DB for persistence.",
        ),
        docs_url="https://devcenter.heroku.com/articles/heroku-cli-commands",
    ),

    # ---------------------------------------------------------------- Cloudflare
    Integration(
        key="cloudflare", label="Cloudflare (wrangler)", category="hosting",
        homepage="https://developers.cloudflare.com/workers/",
        cli="wrangler", install="npm i -g wrangler",
        auth="`wrangler login` or CLOUDFLARE_API_TOKEN",
        summary="Workers, Pages, R2, D1, KV, Durable Objects.",
        detection=("wrangler.toml", "wrangler.jsonc"),
        commands=(
            Command("wrangler login", "Authenticate"),
            Command("wrangler init", "Scaffold a Worker"),
            Command("wrangler dev", "Local dev"),
            Command("wrangler deploy", "Deploy Worker"),
            Command("wrangler pages deploy ./dist", "Deploy Pages"),
            Command("wrangler d1 create <name>", "Create D1 database"),
            Command("wrangler d1 migrations apply <db>", "Apply migrations"),
            Command("wrangler secret put KEY", "Set secret"),
        ),
        pitfalls=(
            "Workers have CPU time limits; heavy work needs Durable Objects/queues.",
        ),
        docs_url="https://developers.cloudflare.com/workers/wrangler/commands/",
    ),

    # ---------------------------------------------------------------- Sentry
    Integration(
        key="sentry", label="Sentry CLI", category="observability",
        homepage="https://sentry.io",
        cli="sentry-cli", install="npm i -g @sentry/cli (or brew)",
        auth="SENTRY_AUTH_TOKEN",
        summary="Error tracking and performance monitoring.",
        detection=("sentry", "@sentry/*", "sentry-sdk"),
        commands=(
            Command("sentry-cli login", "Authenticate"),
            Command("sentry-cli releases new <version>", "Create release"),
            Command("sentry-cli releases files <ver> upload-sourcemaps ./dist", "Upload sourcemaps"),
            Command("sentry-cli releases finalize <ver>", "Finalize release"),
        ),
        pitfalls=(
            "Sourcemaps must match the deployed build exactly.",
        ),
        docs_url="https://docs.sentry.io/product/cli/",
    ),
)

CATALOG: tuple[Integration, ...] = tuple(_with_cli_flag(it) for it in _TOOL_ENTRIES)


# ======================================================================================
# AI app builders and prompt workbenches.
#
# These are driven by natural language inside a web UI rather than by a command
# line, so each entry carries a playbook: prompt patterns, the command dialect
# the platform understands, the questions it asks back, and the situations that
# come up mid-build.
#
# `verified_live` is False on every one of them: PYFIX has not driven these
# services from inside this build. `reviewed_on` records when the vendor's own
# documentation was last read. Re-verify before relying on any of it.
# ======================================================================================

_PLATFORM_ENTRIES: tuple[Integration, ...] = (

    # ---------------------------------------------------------------- Lovable
    Integration(
        key="lovable", label="Lovable", category="app-builder",
        kind="platform",
        homepage="https://lovable.dev",
        cli="", install="Web app; no local install",
        auth="Lovable account. The public REST API authenticates with the "
             "`Lovable-API-Key` header; the key is issued in account settings.",
        summary="Prompt-to-app builder. You describe the app, it writes a React "
                "app with a Supabase backend and a live preview.",
        detection=("lovable", "lovable.json"),
        commands=(
            Command("curl -H 'Lovable-API-Key: <KEY>' https://api.lovable.dev/v1/projects",
                    "List the projects the key can see"),
            Command("curl -H 'Lovable-API-Key: <KEY>' https://api.lovable.dev/v1/projects/<id>",
                    "Read one project"),
        ),
        api="https://api.lovable.dev",
        api_key_header="Lovable-API-Key",
        api_key_url="https://docs.lovable.dev/api-reference/introduction",
        api_capabilities=(
            "list projects", "read a project", "deploy an existing project",
            "read deployment status",
        ),
        deep_link="https://lovable.dev/#prompt={prompt}&images={images}&html={html}",
        operation_mode="both",
        prompt_patterns=(
            PromptPattern(
                "First build — one message, everything stated up front",
                "Build a {kind} called {name}. It must: {feature_1}; {feature_2}; "
                "{feature_3}. Stack: {stack}. Keep it to {n} screens. Do not add "
                "features I did not ask for.",
                "The first prompt is the one that decides the stack, the routing "
                "and the schema. Everything after it is a correction."),
            PromptPattern(
                "Add one feature",
                "Add {feature} to {page}. Do not change {invariant}. Reuse the "
                "existing {component}. Show the preview when you are done.",
                "Name the invariant explicitly — the model will otherwise "
                "refactor the file it is editing."),
            PromptPattern(
                "Fix a failing preview",
                "The preview fails with: {exact_error_text}. Find the cause in "
                "{file} and fix it. Do not change the API contract. Explain the "
                "cause in one sentence before the fix.",
                "Paste the exact console error. A paraphrase produces a "
                "plausible-looking fix to the wrong thing."),
            PromptPattern(
                "Wire a backend",
                "Use Supabase for storage. Tables: {table_1} ({columns}); "
                "{table_2} ({columns}). Enable row level security on every table "
                "and give the anon role read access only to {table_1}.",
                "Never let it generate a service-role key into client code."),
        ),
        command_dialect=(
            "Plain natural language, one request per message — the chat is a "
            "sequence of turns, not a command line.",
            "Be imperative and scoped: 'Change X', not 'Improve the app'.",
            "State the stack, the router and the state library in the first "
            "message; changing them later costs a full regeneration.",
            "Use Visual Edit mode for pure styling: click the element, then "
            "describe the change. Do not ask for CSS in chat when the "
            "visual editor exists.",
            "Attach screenshots or files to the message when a layout or an "
            "API document is the source of truth.",
            "Say 'do not add X' explicitly — absence of a prohibition is read "
            "as permission.",
        ),
        qa_flow=(
            QuestionAnswer(
                "Which framework / stack do you want?",
                "Name it: 'React + Vite + TypeScript + Tailwind', or 'Next.js "
                "App Router + TypeScript'. Do not accept the default silently.",
                "The stack choice is the most expensive thing to change later."),
            QuestionAnswer(
                "Do you want a database?",
                "'Yes — Supabase, tables {list}' or 'No backend, keep it static'.",
                "'Use whatever is easiest' produces a half-configured backend "
                "you have to unpick."),
            QuestionAnswer(
                "Do you want authentication?",
                "'No auth for now' or 'Yes — Supabase email+password, admin "
                "role on {table}'.",
                "Saying no is a legitimate answer and saves a round trip."),
            QuestionAnswer(
                "What should the app be called / branded?",
                "Give the exact name and any tagline. It ends up in the title, "
                "the manifest and the metadata.",
                ""),
            QuestionAnswer(
                "Do you want analytics, SEO or a custom domain?",
                "Answer each separately. 'No' is fine and cheaper.",
                "These are separate features that each consume credits."),
        ),
        situations=(
            Situation(
                "The build stops mid-way with no error",
                "Ask it to continue, then ask what it was doing. If it stops "
                "again, ask for the plan first: 'List the remaining steps "
                "before you edit anything.'",
                "caution"),
            Situation(
                "The preview shows a runtime error",
                "Read the error out of the browser console, paste the exact "
                "text, and name the file. Then ask for the smallest fix that "
                "does not change the data model.",
                "info"),
            Situation(
                "A change broke something that used to work",
                "Ask for a revert of the last message ('undo the last change'), "
                "then re-issue the request with the invariant spelled out.",
                "caution"),
            Situation(
                "Credit limit reached",
                "Credits are consumed per message on paid plans, not per edit. "
                "Wait for the reset, or break one big request into two small "
                "ones next time. Export the project to GitHub before the limit "
                "stops you mid-build.",
                "blocking"),
            Situation(
                "It generated a secret or a service-role key",
                "Stop. Ask it to remove the key, rotate it in the provider's "
                "dashboard, and confirm the key is only referenced through a "
                "server-side environment variable.",
                "blocking"),
            Situation(
                "The app works in preview but fails after publishing",
                "Compare environment variables and the build command between "
                "the preview and the published build. Published builds usually "
                "fail on a missing env var, not on logic.",
                "caution"),
        ),
        pitfalls=(
            "The public REST API manages and deploys existing projects — it "
            "does not build apps from prompts, and those calls do not consume "
            "AI build credits. Prompting itself needs the web UI.",
            "The prompt dialect and the credit model change often. Verify "
            "against the vendor's own documentation before automating.",
            "Your project lives in the vendor's cloud until you export it. "
            "Push to GitHub early and often.",
            "Visual Edit mode and chat editing can disagree about the same "
            "file; pick one per change.",
        ),
        docs_url="https://docs.lovable.dev/",
        verified_live=False,
        reviewed_on="2026-10-23",
    ),

    # ---------------------------------------------------------------- Trickle
    Integration(
        key="trickle", label="Trickle", category="app-builder",
        kind="platform",
        homepage="https://trickle.so",
        cli="", install="Web app; no local install",
        auth="Trickle account. No public API is published, so interaction goes "
             "through the signed-in web session.",
        summary="Agentic canvas for building live apps and websites. You prompt "
                "on a canvas that also holds the visual design, the database "
                "and the generated media.",
        detection=("trickle",),
        commands=(),
        api="",
        api_key_url="https://trickle.so/",
        api_capabilities=(),
        operation_mode="browser",
        prompt_patterns=(
            PromptPattern(
                "Describe the app once, completely",
                "Build a {kind} for {audience}. Pages: {page_1}, {page_2}. It "
                "must collect {form_fields} and show a {list_or_table} of "
                "submissions. Style: {style}. Use the built-in database for "
                "storage.",
                "Trickle is a canvas tool — a single well-scoped sentence "
                "produces a coherent layout; a vague one produces a collage."),
            PromptPattern(
                "Edit one canvas node",
                "Select {node} and change only {property} to {value}. Leave "
                "the rest of the layout untouched.",
                "Naming the node stops the change from rippling across the "
                "canvas."),
            PromptPattern(
                "Add data collection",
                "Add a form to {page} with fields {fields}. Store the rows in "
                "a table named {table} and show them in a list on {page_2}, "
                "newest first.",
                "Trickle's database is built in — say so explicitly or it may "
                "hard-code the data in the page."),
            PromptPattern(
                "Generate an asset",
                "Generate a {style} hero image of {subject} and use it as the "
                "background of {node}. Keep the file under {size} KB.",
                "Image generation is a separate credit pool from code "
                "generation."),
        ),
        command_dialect=(
            "Natural language on a visual canvas. The canvas is part of the "
            "input — selecting a node narrows what the next message applies to.",
            "Say what to change, not what is wrong. 'Make the header blue' beats "
            "'the header looks off'.",
            "One visual concept per message. The canvas applies each message "
            "as an edit, so a message with three asks gets three partial edits.",
            "Ask for the database explicitly when data must survive a reload.",
            "Media generation, layout changes and code changes are billed "
            "separately — keep them in separate messages.",
        ),
        qa_flow=(
            QuestionAnswer(
                "What kind of app is this (website, tool, dashboard)?",
                "Name it and name the audience: 'a lead-capture landing page for "
                "a dental clinic'.",
                "Trickle infers a lot from the nouns you use."),
            QuestionAnswer(
                "Should the content be static or backed by the database?",
                "'Static — no form submissions' or 'Backed — store {fields} and "
                "list them'.",
                "'Static' is the cheapest and the most common answer."),
            QuestionAnswer(
                "Which visual style do you want?",
                "Give a reference adjective plus a palette: 'clean, editorial, "
                "off-white background, dark text, one accent colour'.",
                "Styles are easier to specify than to iterate."),
            QuestionAnswer(
                "Should it be mobile-first?",
                "Answer explicitly. Both are equally easy to produce, and the "
                "wrong answer means redoing every node.",
                ""),
        ),
        situations=(
            Situation(
                "The canvas is half-edited and inconsistent",
                "Ask it to restate the layout it currently sees and then fix "
                "one page at a time. Do not ask for a global restyle in the "
                "middle of a multi-page build.",
                "caution"),
            Situation(
                "Data disappears on reload",
                "The content was written into the page instead of the "
                "database. Say: 'Move {data} into a table named {table} and "
                "read it from there on load.'",
                "info"),
            Situation(
                "The build stalls",
                "Ask for a summary of what has been done and what remains, then "
                "ask for one step only.",
                "caution"),
            Situation(
                "Credits exhausted",
                "Trickle bills generation per message. Export the project "
                "before the limit stops you and continue in a fresh session.",
                "blocking"),
            Situation(
                "Generated images are too large or off-style",
                "Regenerate one asset at a time with an explicit style line and "
                "a size cap rather than re-running the whole prompt.",
                "info"),
        ),
        pitfalls=(
            "No public API is published. Any automation has to drive the "
            "signed-in web session, and it breaks whenever the UI changes.",
            "The prompt dialect, the canvas model and the credit rules all "
            "change frequently. Verify before automating.",
            "The canvas is the source of truth — exporting only the code and "
            "not the layout settings loses work.",
        ),
        docs_url="https://trickle.so/",
        verified_live=False,
        reviewed_on="2026-10-23",
    ),

    # ---------------------------------------------------------------- Replit
    Integration(
        key="replit", label="Replit", category="app-builder",
        kind="platform",
        homepage="https://replit.com",
        cli="replit", install="npm i -g replit (optional; the GraphQL API is "
                             "the main automation path)",
        auth="Replit account. The GraphQL API authenticates with an "
             "`Authorization: Bearer <REPLIT_TOKEN>` header.",
        summary="Hosting platform with Replit Agent. The Agent builds, runs and "
                "publishes the app from a chat, inside a real workspace with a "
                "shell, a debugger and a deployment.",
        detection=(".replit", "replit.nix", "replit.workspace", "replit.json",
                   "replit.nix"),
        commands=(
            Command("curl -X POST https://api.replit.com/graphql -H 'Authorization: Bearer <TOKEN>' -H 'Content-Type: application/json' -d '{\"query\":\"...\"}'",
                    "Call the Replit GraphQL API"),
        ),
        api="https://api.replit.com/graphql",
        api_key_header="Authorization: Bearer",
        api_key_url="https://docs.replit.com/replitai/agent",
        api_capabilities=(
            "create a workspace (community-documented)",
            "send a message to the Agent (community-documented)",
            "read the conversation back (community-documented)",
            "read deployment state",
        ),
        operation_mode="both",
        prompt_patterns=(
            PromptPattern(
                "Describe the app in one message",
                "Build a {kind} called {name}. Features: {feature_1}; "
                "{feature_2}. Use {stack}. Deploy it so I can open a public URL.",
                "Replit Agent is the most literal of these agents — a spec "
                "with bullets produces a spec-shaped app."),
            PromptPattern(
                "Iterate against a running app",
                "The app is at {url}. Click through it and fix what breaks when "
                "I use {flow}. Do not change {invariant}.",
                "The Agent can open its own preview — telling it the URL and "
                "the flow to exercise is what makes this work."),
            PromptPattern(
                "Run a command in the workspace",
                "In the shell, run {command}. If it fails, show me the output "
                "and propose the fix before applying it.",
                "The Agent has a real shell; make it show the output instead "
                "of summarising it."),
            PromptPattern(
                "Answer a clarifying question",
                "{answer}. Continue with the rest of the build.",
                "The Agent asks its clarifying questions inline in the chat."),
        ),
        command_dialect=(
            "Chat with the Agent in the workspace. The conversation, the file "
            "tree, the shell and the preview are one shared context.",
            "Give a numbered feature list. The Agent works through the list "
            "and stops when it thinks the list is done.",
            "Reference files by path ('src/routes.tsx') rather than describing "
            "them.",
            "Ask for a command to be run, and ask for the raw output — it has "
            "a real shell and can fix what it sees.",
            "Ask it to publish or to open a preview URL when you need to test "
            "the real thing.",
            "Version control is available in the workspace: ask it to commit "
            "before a risky change.",
        ),
        qa_flow=(
            QuestionAnswer(
                "What should the app do / which features do you want?",
                "Give a numbered list. 'A dashboard for X with Y, Z and W'.",
                "The Agent will build exactly the list, so the list is the "
                "specification."),
            QuestionAnswer(
                "Which technology stack?",
                "Accept the default if it is what you want: 'Use Next.js with "
                "TypeScript and Tailwind'.",
                "Replit's defaults are good; say yes rather than leaving the "
                "question open."),
            QuestionAnswer(
                "Do you need a database?",
                "'Yes — a Postgres database for {tables}' or 'No database yet'.",
                "'Whatever is easiest' gets you a database you did not plan "
                "for."),
            QuestionAnswer(
                "Do you want to deploy it publicly?",
                "'Yes, publish it' or 'No, keep it in the workspace for now'.",
                "Publishing consumes the Agent budget."),
            QuestionAnswer(
                "Do you need secrets or environment variables?",
                "Name them and their values. Confirm they are set in the "
                "workspace's Secrets panel, not pasted into source.",
                ""),
        ),
        situations=(
            Situation(
                "The Agent stopped halfway and says it is 'done'",
                "Ask it to list what it has built against your numbered list "
                "and to continue with the missing items.",
                "caution"),
            Situation(
                "The preview throws an error",
                "Ask it to open the preview, read the console and the terminal "
                "output, and fix the root cause rather than the symptom.",
                "info"),
            Situation(
                "It changed files it should not have",
                "Ask it to revert the named files to the last commit, then "
                "re-apply the change. Replit Agent can use git in the "
                "workspace.",
                "caution"),
            Situation(
                "The Agent asks a question you do not understand",
                "Answer the question with a decision, not a description. If it "
                "asks 'database or not?', answer 'no database'.",
                "info"),
            Situation(
                "The build times out on a long task",
                "Split the request: 'First do {step_1}. Stop and wait for me "
                "before starting {step_2}.'",
                "caution"),
            Situation(
                "Agent budget / credits exhausted",
                "The Agent has a per-session runtime budget. Save the "
                "workspace, push it to GitHub and continue in a new session.",
                "blocking"),
        ),
        pitfalls=(
            "The programmatic 'create app from prompt' path is "
            "community-documented rather than described in the official agent "
            "docs. Treat the GraphQL operation names as a starting point and "
            "verify them against a live account before depending on them.",
            "GraphQL shape changes without a version bump. Pin the query you "
            "validated and keep a fallback to the web UI.",
            "The Agent has a real shell and real network access — it can run "
            "anything the workspace can run.",
            "Deployments, previews and the Agent are separate products with "
            "separate limits.",
        ),
        docs_url="https://docs.replit.com/features/agent/overview",
        verified_live=False,
        reviewed_on="2026-10-23",
    ),

    # ---------------------------------------------------------------- Emergent
    Integration(
        key="emergent", label="Emergent", category="app-builder",
        kind="platform",
        homepage="https://emergent.sh",
        cli="", install="Web app; no local install",
        auth="Emergent account. No public API is published, so interaction goes "
             "through the signed-in web session.",
        summary="Conversation-first app builder. You describe the product in "
                "plain language and the agent designs, codes and deploys it "
                "for you.",
        detection=("emergent",),
        commands=(),
        api="",
        api_key_url="https://help.emergent.sh/start-with-your-idea",
        api_capabilities=(),
        operation_mode="browser",
        prompt_patterns=(
            PromptPattern(
                "Start with one sentence",
                "{one_sentence_description}.",
                "The first prompt only has to establish the product. It is a "
                "conversation, not a contract."),
            PromptPattern(
                "Make it concrete",
                "It should have {pages}. Users are {roles}. The main flow is "
                "{flow}. Store {entities} so they persist.",
                ""),
            PromptPattern(
                "Ask before it builds",
                "Before you write any code, tell me the stack you would use "
                "and the schema you would create.",
                "Asking for the plan first is cheap and catches a wrong "
                "assumption before it is baked into ten files."),
            PromptPattern(
                "Change one thing",
                "Change {thing}. Leave everything else exactly as it is.",
                "Emergent tends to rewrite whole files; the constraint is "
                "what keeps the diff small."),
        ),
        command_dialect=(
            "Conversational. The agent asks short follow-up questions and "
            "expects short answers.",
            "Answer the question that was asked. Do not answer with a "
            "paragraph.",
            "It has a built-in backend with a database and auth — say "
            "'use the built-in database' rather than naming a provider, unless "
            "you specifically want one.",
            "Attachments (images, documents) become part of the design "
            "context; use them instead of describing a layout in words.",
            "It can test the app it built — ask it to exercise the flow "
            "before you accept the result.",
        ),
        qa_flow=(
            QuestionAnswer(
                "What is the app for?",
                "One sentence naming the audience and the outcome.",
                "The agent works from this sentence for the whole build."),
            QuestionAnswer(
                "Which framework / backend do you want?",
                "Accept its default or name one. 'Use the default stack' is a "
                "valid answer.",
                ""),
            QuestionAnswer(
                "Do you want authentication?",
                "'Yes — email and password' or 'No, single-user for now'.",
                "Single-user is much cheaper to build and to test."),
            QuestionAnswer(
                "Do you want the app deployed publicly?",
                "'Yes — give me a preview URL' or 'No, keep it in the editor'.",
                ""),
        ),
        situations=(
            Situation(
                "It builds a different app than you described",
                "Do not keep editing. Say: 'Stop. Here is what I actually want: "
                "{precise description}. Discard the current structure and "
                "rebuild.'",
                "caution"),
            Situation(
                "It asks a question mid-build",
                "Answer directly and briefly, then let it continue. An "
                "unanswered question stalls the build.",
                "info"),
            Situation(
                "The app is broken after a change",
                "Ask it to test the flow it just built and report what it "
                "sees before changing anything else.",
                "info"),
            Situation(
                "Credits run out",
                "New accounts get a small free credit allowance. Export the "
                "project before the allowance is spent.",
                "blocking"),
            Situation(
                "It wants to add a mobile app",
                "Say no explicitly unless you want one. Mobile is a separate "
                "build with separate credits.",
                "caution"),
        ),
        pitfalls=(
            "No public API. Any automation drives the signed-in web session "
            "and breaks when the UI changes.",
            "Its scope keeps expanding towards mobile and native features; "
            "state the scope you want.",
            "Verify the plan before it writes code — a wrong assumption is "
            "expensive to unwind.",
        ),
        docs_url="https://help.emergent.sh/start-with-your-idea",
        verified_live=False,
        reviewed_on="2026-10-23",
    ),

    # ---------------------------------------------------------------- v0
    Integration(
        key="v0", label="v0 (Vercel)", category="app-builder",
        kind="platform",
        homepage="https://v0.app",
        cli="", install="Web app; REST API and a TypeScript SDK",
        auth="v0 API key from the v0 settings page, sent as "
             "`Authorization: Bearer <V0_API_KEY>`.",
        summary="Vercel's app-building agent. It generates a working Next.js "
                "app from a prompt, hosts a live preview, and can deploy it to "
                "Vercel.",
        detection=("v0",),
        commands=(
            Command("curl -X POST https://api.v0.dev/v1/chats -H 'Authorization: Bearer <V0_API_KEY>' -H 'Content-Type: application/json' -d '{\"message\":\"Build a ...\"}'",
                    "Create a chat from a prompt"),
            Command("curl https://api.v0.dev/v1/chats/<chatId>/messages -H 'Authorization: Bearer <V0_API_KEY>'",
                    "Read the chat back"),
        ),
        api="https://api.v0.dev/v1",
        api_key_header="Authorization: Bearer",
        api_key_url="https://v0.app/chat/settings/keys",
        api_capabilities=(
            "create a chat from a prompt",
            "stream the chat messages back",
            "read the live preview URL",
            "create a deployment",
            "OpenAI-compatible chat/completions",
        ),
        operation_mode="both",
        prompt_patterns=(
            PromptPattern(
                "Short and imperative — v0 rewards brevity",
                "Build a {kind} using {stack}. {Two sentences of behaviour.} "
                "Use {ui_library}.",
                "v0 is the terse one. Long prompts are not better here."),
            PromptPattern(
                "Name the stack explicitly",
                "Next.js App Router, TypeScript, Tailwind, shadcn/ui, "
                "Postgres via Prisma.",
                "v0 defaults to Next.js + Tailwind; say otherwise if you need "
                "something different."),
            PromptPattern(
                "Iterate on one component",
                "In {component}, change {behaviour}. Keep the props "
                "signature.",
                "Component-scoped requests keep the diff reviewable."),
            PromptPattern(
                "Fix an error in the preview",
                "The preview shows {error}. Fix it in {file} without changing "
                "{invariant}.",
                "v0 can read the preview error itself if you let it."),
        ),
        command_dialect=(
            "A chat per project. The context is the chat plus the files it "
            "touched.",
            "Keep prompts to one change. v0 is good at narrow, surgical edits "
            "and worse at broad ones.",
            "Reference files by path.",
            "Use Design Mode when the request is about layout and appearance; "
            "use the chat when it is about behaviour.",
            "The live preview is the feedback channel — point v0 at the error "
            "it produces.",
        ),
        qa_flow=(
            QuestionAnswer(
                "Which framework and styling do you want?",
                "'Next.js App Router with TypeScript and Tailwind' — this is "
                "also the default, so answering 'yes, default' is fine.",
                ""),
            QuestionAnswer(
                "Do you want a database or authentication?",
                "'Prisma with Postgres' or 'No database for now'.",
                ""),
            QuestionAnswer(
                "Should it use your design system?",
                "'No, use shadcn/ui defaults' or paste your component library.",
                ""),
            QuestionAnswer(
                "Deploy to Vercel when it is done?",
                "'Yes' or 'Not yet, I want to review the preview'.",
                "Deployment to a production URL is a separate action with its "
                "own consequences."),
        ),
        situations=(
            Situation(
                "The generated app does not compile",
                "Paste the build error and name the file. Ask for the smallest "
                "fix rather than a rewrite.",
                "info"),
            Situation(
                "It rewrote more than you asked",
                "Ask it to revert the files you did not name and redo only the "
                "requested change.",
                "caution"),
            Situation(
                "The preview looks right but the data layer is missing",
                "Say explicitly which API routes or server actions it must "
                "create, and ask it to show them.",
                "caution"),
            Situation(
                "It stalls on a dependency",
                "Ask it to pin the dependency version and to avoid the "
                "optional peer dependency it is fighting.",
                "info"),
            Situation(
                "Rate limited or out of credit",
                "The API returns an error rather than queuing. Wait for the "
                "limit to clear and retry the same chat id if one was created.",
                "blocking"),
        ),
        pitfalls=(
            "The prompt dialect, the API surface and the credit rules change "
            "without notice. Verify this entry against the vendor's own "
            "documentation before you rely on it.",
            "v0 is owned by Vercel and the API is versioned (v1 and v2 exist "
            "side by side with different hosts and paths). Pin the version you "
            "validated.",
            "The API path changed from `https://v0.app/api/...` to "
            "`https://api.v0.dev/...`; both hosts are supported but the old "
            "one is the legacy surface.",
            "The OpenAI-compatible `/chat/completions` route is a different "
            "product from the platform API — it returns text, not a chat with "
            "a preview.",
            "Deployment URLs are public. Do not deploy anything with secrets "
            "still in it.",
        ),
        docs_url="https://v0.app/docs/api/v2",
        verified_live=False,
        reviewed_on="2026-10-23",
    ),

    # ---------------------------------------------------------------- Builder.io
    Integration(
        key="builder", label="Builder.io", category="app-builder",
        kind="platform",
        homepage="https://www.builder.io",
        cli="builder",
        install="npm i -g @builder.io/cli",
        auth="Builder API key (org-level). The Content API takes it as the "
             "`apiKey` query parameter; the CLI takes it with `--key`.",
        summary="Visual development platform: Figma designs to code, a "
                "component library, a visual CMS, and an MCP server for AI "
                "coding agents.",
        detection=("builder.io", "@builder.io", "builder.json"),
        commands=(
            Command("npm i -g @builder.io/cli", "Install the Builder CLI"),
            Command("builder create --key <KEY>", "Create a new Builder space"),
            Command("builder figma publish", "Import a Figma frame and generate code"),
            Command("builder sync", "Regenerate code from the Builder content"),
            Command("builder dev", "Run the local dev server with Builder content"),
        ),
        api="https://cdn.builder.io/api/v3",
        api_key_header="apiKey (query parameter)",
        api_key_url="https://www.builder.io/c/docs/api-intro",
        api_capabilities=(
            "fetch published visual content",
            "publish content",
            "generate code from a Figma frame (via the CLI or plugin)",
            "serve the visual editor (SDK)",
        ),
        operation_mode="both",
        prompt_patterns=(
            PromptPattern(
                "Describe what the design should become",
                "Turn the selected Figma frame into a React component. Use "
                "Tailwind for styling, keep the component prop-driven, and "
                "make it responsive at the {breakpoints} breakpoints.",
                "Builder's code generation is driven by a design, so the "
                "prompt is about how to implement it, not what to design."),
            PromptPattern(
                "Make it match the design system",
                "Use the components from {space} instead of raw HTML. Map "
                "{element} to {component} and keep the spacing tokens.",
                ""),
            PromptPattern(
                "Turn a page into a section",
                "Create a section for {block}. It must accept {props} as "
                "content props and render nothing when they are empty.",
                ""),
            PromptPattern(
                "Generate a variant",
                "Create a {variant} variant of {component}: {differences}. "
                "Keep the same props interface.",
                ""),
        ),
        command_dialect=(
            "Two surfaces: the Figma plugin (design in Figma, run the CLI "
            "command it prints) and the CLI itself.",
            "The CLI is the command dialect — `builder figma publish`, "
            "`builder sync`, `builder dev`.",
            "Content is edited in the visual editor and regenerated into code "
            "with `builder sync`; the code is a build artifact of the "
            "content, not the source of truth.",
            "For AI agents, Builder ships an MCP server — point the agent at "
            "it rather than scraping the web UI.",
            "Section-level prompts (one block at a time) produce cleaner code "
            "than page-level ones.",
        ),
        qa_flow=(
            QuestionAnswer(
                "Which Builder space should this go in?",
                "Name the space: 'the {space} space'. Content is namespaced by "
                "space.",
                ""),
            QuestionAnswer(
                "Which framework is the target codebase?",
                "'React + Next.js' or 'React + Vite'. The generated code "
                "differs.",
                "Builder can usually infer this from the project it runs in."),
            QuestionAnswer(
                "Do you want Tailwind or plain CSS?",
                "'Tailwind' is the common answer.",
                ""),
            QuestionAnswer(
                "Should the code be self-contained or use the SDK?",
                "'Self-contained React component' for portability, or 'use the "
                "Builder SDK' for live editing.",
                "The SDK version needs the Builder runtime at run time."),
        ),
        situations=(
            Situation(
                "The generated code does not match the design",
                "Adjust the design in Figma first, then re-publish. Nudging "
                "the code by hand is overwritten on the next `builder sync`.",
                "caution"),
            Situation(
                "`builder sync` overwrote your manual edits",
                "That is expected — the content is the source of truth. Move "
                "customisations into the visual editor or into a wrapper "
                "component that the generated code cannot clobber.",
                "caution"),
            Situation(
                "The API key is rejected",
                "Check the key's scope and that it belongs to the same space "
                "as the content you are fetching.",
                "info"),
            Situation(
                "The CLI cannot find the Figma file",
                "The plugin must be connected to the file and the printed "
                "command must be run in the target project directory.",
                "info"),
            Situation(
                "Content is served from a CDN and looks stale",
                "The Content API is cached at the edge. Cache-bust with the "
                "API's cache-clearing call or fetch with `cachebust=true`.",
                "info"),
        ),
        pitfalls=(
            "The prompt dialect, the API surface and the credit rules change "
            "without notice. Verify this entry against the vendor's own "
            "documentation before you rely on it.",
            "The Content API manages visual content, not AI code generation. "
            "The Figma-to-code path is the plugin plus the CLI.",
            "Generated code is regenerated. Never hand-edit a generated file "
            "and expect it to survive.",
            "API keys in the `apiKey` query parameter end up in URLs and in "
            "access logs. Scope them narrowly.",
            "The MCP server is the supported way to let an AI agent work with "
            "Builder; prefer it over browser automation.",
        ),
        docs_url="https://www.builder.io/c/docs/api-intro",
        verified_live=False,
        reviewed_on="2026-10-23",
    ),

    # ---------------------------------------------------------------- AI Studio
    Integration(
        key="ai-studio", label="Google AI Studio", category="ai-workbench",
        kind="platform",
        homepage="https://aistudio.google.com",
        cli="", install="Web app; also reachable through the Generative "
                        "Language API",
        auth="Google account. API keys are created in AI Studio and sent as "
             "`x-goog-api-key` (or `Authorization: Bearer`).",
        summary="Prompt workbench for the Gemini models. It is where you "
                "design a prompt, tune parameters, try structured output, run "
                "generated code, and promote a prompt into an API call.",
        detection=("aistudio", "ai-studio", "aistudio.json"),
        commands=(
            Command("curl -X POST 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions' -H 'x-goog-api-key: <KEY>' -H 'Content-Type: application/json' -d '{\"model\":\"gemini-2.5-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"...\"}]}'",
                    "Call Gemini over the OpenAI-compatible surface"),
            Command("curl 'https://generativelanguage.googleapis.com/v1beta/models' -H 'x-goog-api-key: <KEY>'",
                    "List the models the key can use"),
        ),
        api="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key_header="x-goog-api-key",
        api_key_url="https://aistudio.google.com/app/apikey",
        api_capabilities=(
            "list models",
            "chat/completions (OpenAI-compatible)",
            "structured output with a JSON schema",
            "run generated code in the workbench",
            "promote a tuned prompt into an API call",
        ),
        deep_link="https://aistudio.google.com/prompts/new_chat?prompt={prompt}&model={model}",
        operation_mode="both",
        prompt_patterns=(
            PromptPattern(
                "Give a role, a task and a format",
                "You are a {role}. {task}. Respond as {format}. If {input} is "
                "missing, ask for it and do not guess.",
                "AI Studio prompts are the same surface as the API, so a prompt "
                "that works here works in code."),
            PromptPattern(
                "Force a JSON schema",
                "Return JSON matching this schema: {schema}. Use null for "
                "unknown values; never invent a value.",
                "Structured output is enforced by the API, so the schema is "
                "the contract."),
            PromptPattern(
                "Ground it in supplied context",
                "Use only the context below. Quote the line you relied on. If "
                "the context does not answer the question, say so.\n\n--- "
                "CONTEXT ---\n{context}",
                "Asking for the quote makes hallucinations visible."),
            PromptPattern(
                "Make the task testable",
                "Solve the problem. Then list the cases your answer would "
                "fail on.",
                "The second half is what turns an answer into a reviewable "
                "one."),
        ),
        command_dialect=(
            "The workbench is prompt-and-parameter: system instructions, the "
            "user turn, temperature, top-p, and a response schema.",
            "Parameters live beside the prompt, not inside it. Do not repeat "
            "the temperature in the prompt text.",
            "'Run' executes code the model produced; use it for a quick "
            "sanity check, not as a pipeline.",
            "'Tune' and the structured-output tab are the places where the "
            "prompt becomes an API call — capture the final prompt text "
            "there, not from the chat.",
            "The `?prompt=` URL parameter prefills a new chat, which is the "
            "reliable way to hand a prompt to a browser session.",
        ),
        qa_flow=(
            QuestionAnswer(
                "Which model do you want to use?",
                "Name it: 'gemini-2.5-flash for speed' or 'gemini-2.5-pro for "
                "reasoning'.",
                "The free tier has daily per-model caps, so the model choice "
                "determines when you run out."),
            QuestionAnswer(
                "Do you want a system instruction?",
                "Yes, and put the role and the constraints there, keeping the "
                "user turn for the task itself.",
                ""),
            QuestionAnswer(
                "Structured output or free text?",
                "'Structured — use this JSON schema' or 'Free text'.",
                "Structured output is enforced by the API and is the safer "
                "default for anything a program will read."),
            QuestionAnswer(
                "Should it use your own context?",
                "'Only the attached context' when you need grounded answers.",
                ""),
        ),
        situations=(
            Situation(
                "Quota exhausted (429)",
                "The free tier caps requests per model per day. Switch to a "
                "different model, wait for the reset, or use a paid key.",
                "blocking"),
            Situation(
                "The model ignored part of the prompt",
                "Split the instruction across the system instruction and the "
                "user turn, and turn the soft requirement into a schema "
                "constraint.",
                "info"),
            Situation(
                "The answer invents facts",
                "Add the 'quote the line you relied on' instruction, or switch "
                "to structured output with a nullable field so the model has "
                "to mark the unknown instead of inventing it.",
                "caution"),
            Situation(
                "The prompt is too long",
                "The context window is large but the free tier still caps "
                "input tokens per request. Trim the context and keep the "
                "capability in a file the prompt points at.",
                "info"),
            Situation(
                "The key leaks into a commit or a log",
                "Revoke the key in AI Studio and issue a new one. Keys appear "
                "in query strings when the OpenAI-compatible surface is used "
                "with `key=`.",
                "blocking"),
        ),
        pitfalls=(
            "The public API and AI Studio are the same model family but "
            "different surfaces: the API applies your schema, the workbench "
            "shows the run.",
            "Model ids and the free-tier caps change often. Confirm the model "
            "list with the API rather than trusting a remembered id.",
            "Prompt text in the workbench is not automatically the prompt your "
            "code sends — capture it deliberately.",
            "Free-tier keys are still real keys. Scope them and treat them as "
            "secrets.",
        ),
        docs_url="https://aistudio.google.com/",
        verified_live=False,
        reviewed_on="2026-10-23",
    ),
)

PLATFORMS: tuple[Integration, ...] = tuple(
    _with_cli_flag(it) for it in _PLATFORM_ENTRIES
)


def by_key(key: str) -> Integration | None:
    for it in CATALOG:
        if it.key == key:
            return it
    for it in PLATFORMS:
        if it.key == key:
            return it
    return None


def by_category(category: str) -> list[Integration]:
    return [it for it in ALL if it.category == category]


def by_kind(kind: str) -> list[Integration]:
    """Return the tool entries or the platform (app-builder) entries."""
    return [it for it in ALL if it.kind == kind]


def platforms() -> list[Integration]:
    """The AI app builders / prompt workbenches."""
    return list(PLATFORMS)


def marker_names(project_root: str) -> set[str]:
    """Every file and directory name under ``project_root``, in one pass.

    Detection used to call ``rglob`` once per marker, which multiplied the
    cost of a full scan by the number of markers. One walk serves them all.
    """
    import os

    names: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__",
                                                       "node_modules", ".venv")]
        for d in dirnames:
            names.add(d)
        for f in filenames:
            names.add(f)
        # also record the relative path of the top-level entries so a marker
        # like "package.json" or ".github" still matches at the root
        rel = os.path.relpath(dirpath, project_root)
        if rel != ".":
            names.add(rel.replace(os.sep, "/"))
    return names


def detect_markers(project_root: str) -> list[str]:
    """Return integration keys whose detection markers exist under project_root.

    Static, read-only: checks file existence only, never executes anything.
    """
    from pathlib import Path

    root = Path(project_root)
    names = marker_names(project_root)
    found: list[str] = []
    for it in ALL:
        for marker in it.detection:
            # support glob-like simple patterns
            if "*" in marker:
                if any(root.glob(marker)):
                    found.append(it.key)
                    break
            elif marker in names or (root / marker).exists():
                found.append(it.key)
                break
    return found


ALL: tuple[Integration, ...] = CATALOG + PLATFORMS

CATEGORIES: tuple[str, ...] = tuple(sorted({it.category for it in ALL}))
