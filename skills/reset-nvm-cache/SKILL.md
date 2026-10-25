---
name: reset-nvm-cache
description: 'Reset both root-level NVM generator cache files in a given Git repository and create a local commit. Use when asked to reset caches, erase cache files, clear autoload hashes, invalidate the NVM cache, or reset al_path_to_hash.json and al_path_to_hash_bid.json and commit.'
argument-hint: "<target_repo_path>"
---

# Reset NVM Cache

Clear the autoload and CRIF hash maps in both root-level cache files, retain hash
caching, and commit only the resulting cache changes. Do not push.

## When to Use

- "Reset both cache files in `/home/sleonarx/mmg-800` and commit."
- "Erase the NVM cache for `/home/sleonarx/mmg`."
- "Clear the autoload hashes and create a cache-reset commit in this repo."

This is a cache-maintenance workflow, not an AL intake. Do not modify autoloads,
CRIFs, board definitions, or generated images.

## Usage / Procedure

Invoke in chat, for example:

```text
/reset-nvm-cache /home/sleonarx/mmg-800
```

1. Resolve the supplied path to its Git top-level directory. If no path is given,
   use the current workspace only when it identifies exactly one Git repository;
   otherwise ask the user to select one. Never guess between sibling repositories.

   ```bash
   git -C "<target_repo_path>" rev-parse --show-toplevel
   ```

   Use that absolute top-level path as `<repo>` in every subsequent command.
   Read applicable repository instructions before modifying files.

2. Inspect the branch, worktree, index, and both cache files:

   ```bash
   git -C "<repo>" status --short --branch
   git -C "<repo>" ls-files --error-unmatch -- al_path_to_hash.json al_path_to_hash_bid.json
   git -C "<repo>" diff -- al_path_to_hash.json al_path_to_hash_bid.json
   git -C "<repo>" diff --cached -- al_path_to_hash.json al_path_to_hash_bid.json
   ```

   Require both paths to be tracked, regular, non-symlink files at the repository
   root. Read and parse both as JSON objects before writing either. Require
   `use_hash` to be a boolean and `csv_files` and `crif_files` to be objects.
   If either file is missing, malformed, has unexpected top-level keys, or has an
   incompatible schema, stop and report the exact issue rather than inventing a
   replacement. Ask before overwriting any existing staged or unstaged changes
   to either cache. Leave unrelated changes, including staged ones, untouched.
   Stop during an in-progress merge, rebase, cherry-pick, or revert, on unmerged
   paths, or on detached HEAD; ask the user how to proceed.

3. Reset **both** files to the following JSON content, preserving `use_hash: true`:

   ```json
   {
       "use_hash":true,
       "csv_files":{

       },
       "crif_files":{

       }
   }
   ```

   This matches the reset state in `mmg-800` commit
   `ccceadaaf868d48cbe801ac91f6e4c2c234da83f` ("Erasing the cache files").
   Clear the maps; do not delete the files or set `use_hash` to false.
   Files already semantically equal to this state need no rewrite solely for
   formatting. If both are already reset and there is no cache diff to commit,
   report that fact and do not create an empty commit.

4. Parse both resulting files and verify their exact JSON value is
   `{"use_hash": true, "csv_files": {}, "crif_files": {}}`.
   Review the cache diff and check whitespace:

   ```bash
   git -C "<repo>" diff HEAD -- al_path_to_hash.json al_path_to_hash_bid.json
   git -C "<repo>" diff --check HEAD -- al_path_to_hash.json al_path_to_hash_bid.json
   ```

5. Create a local commit scoped to these paths, using `--only` so unrelated staged
   changes are not included. Do not use `git add .`, `git commit -a`, an amend,
   a force operation, or a push.

   ```bash
   git -C "<repo>" commit --only \
     -m "Erasing the cache files" \
     -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
     -- al_path_to_hash.json al_path_to_hash_bid.json
   ```

   Honor any applicable commit-message requirements or explicit user override of
   the message/trailer. If Git identity, hooks, signing, or another commit step
   fails, surface the failure; do not disable checks or claim success. Report the
   remaining cache edits so the user knows what is still uncommitted.

6. Verify the new commit contains only the cache paths (one path is acceptable
   if the other was already reset), both committed JSON values match step 4, and
   neither cache has remaining staged or unstaged changes. Check that unrelated
   staged and unstaged work is unchanged from step 2.

   ```bash
   git -C "<repo>" --no-pager show --stat --oneline HEAD
   git -C "<repo>" --no-pager show HEAD:al_path_to_hash.json
   git -C "<repo>" --no-pager show HEAD:al_path_to_hash_bid.json
   git -C "<repo>" status --short
   ```

7. Report the repository, the two reset files, commit hash, and verification
   outcome. Explicitly state that nothing was pushed. If no commit was needed,
   say so instead of presenting the previous HEAD as a new commit.
