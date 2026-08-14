# GitHub access for `cairn author`

`cairn author` opens a pull request, so it needs a GitHub credential. This document
is about giving it the **smallest one that works**.

## Why not just use `gh auth login`

It will work — `cairn author` shells out to `gh`, which uses whatever credential you
already have. But check what that credential can do:

```
$ gh auth status
  Token scopes: 'gist', 'read:org', 'repo', 'workflow'
```

`repo` is **every repository you can access**, read and write. `workflow` means the
token can modify GitHub Actions workflow files.

Cairn's validator refuses to write outside your content directory, so it will never
*try* to touch `.github/workflows/`. But a defence you can only verify by reading
someone's code is weaker than one the credential enforces. If the token cannot edit
CI, then a bug in cairn cannot become a supply-chain problem.

`cairn author` warns when it detects an over-broad scope:

```
note  this token carries workflow scope — broader than this agent needs.
```

## The scoped credential

Use a **fine-grained personal access token**, limited to one repository.

**1. Create it** — GitHub → Settings → Developer settings → Personal access tokens →
Fine-grained tokens → *Generate new token*

**2. Resource owner**: you, or the org that owns the site repo.

**3. Repository access**: *Only select repositories* → pick the one site repo. Not
"All repositories".

**4. Repository permissions** — set exactly these two, leave everything else *No access*:

| Permission | Level | Why |
|---|---|---|
| **Contents** | Read and write | Push the `cairn/<slug>` branch |
| **Pull requests** | Read and write | Open the PR |

That is the whole list. In particular leave these at *No access*:

- **Workflows** — the agent has no business editing CI
- **Actions**, **Secrets**, **Environments**, **Deployments** — nothing to do with writing a post
- **Administration** — cannot change repo settings or delete anything

**5. Expiration**: 90 days is a reasonable default. This is a credential that writes
to your website; it should expire.

## Using it

`gh` reads `GH_TOKEN` and it takes precedence over the stored keyring login:

```bash
GH_TOKEN=github_pat_xxx uv run cairn author <brief-id> --repo ./my-site
```

Or put it in your `.env` and export it for the command. **Do not commit it** —
`.env` is gitignored, and a token that can write to your site is worth as much as
your site.

Verify which credential is in play before you rely on it:

```bash
$ uv run cairn doctor
  github (author)   ttokttokttok via GH_TOKEN
```

`cairn author` prints the same line and checks it **before** the agent writes
anything, so a missing or wrong token fails in a second rather than after a
four-thousand-word generation.

## What the token can actually do

Even with a correctly scoped token, the operations are bounded twice over:

| Layer | What stops what |
|---|---|
| **Cairn's validator** | Only markdown, only inside the content directory, create-or-narrow-edit, never overwrite, never delete |
| **The token's scope** | Only this one repository, no workflow edits, no admin |
| **The pull request** | A human reads the diff and merges |

The agent never pushes to your default branch. Every change lands on a fresh
`cairn/<slug>` branch.

## If you would rather not give it a token at all

`--no-pr` commits to a local branch and stops:

```bash
uv run cairn author <brief-id> --repo ./my-site --no-pr
git -C ./my-site diff main...cairn/<slug>
```

No GitHub credential is involved. This is a good way to build trust before granting
write access, and it is the recommended first run.
