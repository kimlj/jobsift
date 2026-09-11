# Residential worker: onlinejobs.ph from home, everything else on a server

onlinejobs.ph answers a datacenter IP with a Cloudflare 403 and serves the same
request from a home connection. Everything else jobsift reads - Gmail, the remote
JSON feeds - works from a server (measured table in [deploy.md](deploy.md)). This
mode splits the program along that one line:

```
                 ┌──────────── core (a server, 24/7) ────────────┐
 Gmail ─────────►│ the normal pipeline: SQLite, scoring, sheet,  │
 remote feeds ──►│ Telegram, drafting                            │
                 │        ▲ data/inbox/  (read by the loop)      │
                 └────────┼──────────────────────────────────────┘
                          │ SSH, one pinned command
                 ┌────────┴──────── worker (at home) ────────────┐
                 │ onlinejobs.ph: list pages, ask the core what   │
                 │ it has, detail pages for the rest, deliver.    │
                 │ Pushes the brief, career.yaml, resume, profile │
                 └────────────────────────────────────────────────┘
```

The worker is any machine on a home connection, which is usually the laptop you
already run jobsift on. When it sleeps, onlinejobs.ph waits; mail, feeds, alerts
and sheet drafting keep going.

## What it guarantees

- **One database, one writer.** The worker never opens `jobs.db`. Delivered
  batches land in the core's `data/inbox/`, and the core's own loop reads them
  as one more source, so the process writing the database is still the only
  one. A worker machine refuses the commands that need the database.
- **No detail page read twice.** Before reading any detail page the worker asks
  the core which listings it already has, counting both stored jobs and batches
  delivered but not yet processed. The core computes the dedup key, so the two
  machines cannot disagree about it.
- **Nothing lost when the link drops.** If the core cannot be asked, the pass
  stops before any detail page is read (the board keeps listings up for
  months). If a delivery fails, the batch waits in the worker's outbox and goes
  first next time. A batch delivered twice is kept once, and every job is
  deduplicated on arrival anyway.
- **Nothing lost when the core crashes mid-pass.** A batch is filed as done only
  after the pass that read it finishes; a pass that dies re-reads it.
- **The worker's key can do three things.** Pinned on the core to
  `--worker-serve`, it can ask `seen`, `deliver <batch>`, or `put` one of
  `brief`, `career`, `resume`, `profile`. Anything else, a shell included, is
  refused. Each `put` is validated first: a broken `career.yaml` is refused and
  the old one kept.
- **The laptop stays the source of truth for your files.** It builds the
  evidence brief (it has the repos, including ones never pushed to GitHub) and
  pushes the brief, `career.yaml`, `resume.txt` and `profile.yaml` whenever they
  change. No GitHub token has to live on the server, and a core with no repos of
  its own never rebuilds over the brief it was sent.

## Setting it up

### The core (server)

1. Install jobsift as usual (`git clone`, venv, `pip install -r requirements.txt`)
   and copy over `.env` and `service-account.json`.
2. In the core's `config.yaml`:

   ```yaml
   scrape_sources:
     onlinejobs_ph:
       enabled: false        # 403 from a datacenter; the worker reads it
     worker_inbox:
       enabled: true         # batches the worker delivers, read every pass
       dir: ./data/inbox
   evidence:
     enabled: true           # drafts read the brief the worker pushes
     roots: []               # nothing to count here, so nothing is rebuilt
   ```

3. **Move the database, don't copy it.** Stop jobsift on the machine that has
   been running it, then copy its `data/jobs.db` to the core's `data/`. From that
   moment the core is the only writer.
4. Pin the worker's key. On the worker:

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/jobsift_worker -N "" -C jobsift-worker
   ```

   No passphrase, because it runs unattended; the pin is what limits it. On the
   core, add one line to `~/.ssh/authorized_keys` for the user jobsift runs as
   (paths to match your install):

   ```
   command="cd /home/YOU/jobsift && .venv/bin/python -m jobsift --worker-serve",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding ssh-ed25519 AAAA... jobsift-worker
   ```

   Check the pin from the worker. Both of these must be refused:

   ```bash
   ssh -i ~/.ssh/jobsift_worker you@core ls          # refused: 'ls' is not a worker request
   ssh -i ~/.ssh/jobsift_worker you@core seen </dev/null   # refused: empty batch
   ```

5. Start the service (Option A in [deploy.md](deploy.md)).

### The worker (home)

In the worker's `config.yaml`, keep the `onlinejobs_ph` block with your search
keywords (the worker reads it whether or not `enabled` is set) and add:

```yaml
worker:
  enabled: true
  ssh: you@core                   # user@host of the core
  ssh_key: ~/.ssh/jobsift_worker  # the pinned key
  outbox: ./data/outbox
```

Nothing else changes. `python -m jobsift`, and so the Windows scheduled task or
systemd unit already running it, starts the worker loop instead of the pipeline
when `worker.enabled` is true.

## Checking it

- On the worker, each pass logs `worker pass: N listing(s), K already on the
  core, D delivered, Q waiting in the outbox`.
- On the core, a delivered batch logs `worker inbox: N job(s) from M batch(es)`
  and the jobs appear in the sheet as usual, with source `onlinejobs_ph`.
- `dryrun_worker.py` exercises every guarantee above offline, with the core
  called in-process and onlinejobs.ph stubbed.
