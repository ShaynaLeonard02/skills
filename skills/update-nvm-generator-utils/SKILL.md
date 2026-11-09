---
name: update-nvm-generator-utils
description: 'Update the utility files in an NVM Generator repository from intel-innersource/firmware.ethernet.mountevans.nvm-team-utils. Use when asked to update, refresh, sync, or copy nvm-team-utils into NVM Generator; always ask for the destination NVM Generator repo path before cloning or copying.'
argument-hint: "<nvm_generator_repo_path>"
---

# Update NVM Generator Utilities

Clone or refresh `firmware.ethernet.mountevans.nvm-team-utils` at `/home/sleonarx/utils`, then copy its working-tree contents into the user's NVM Generator repository. Preserve destination files that are not supplied by the utility repo; do not copy Git metadata, delete destination files, or commit or push changes.

## When to Use

- "Update the utils in my NVM Generator repo."
- "Refresh nvm-team-utils in `/path/to/nvm-generator`."
- "Copy the latest utility repo into NVM Generator."

## Usage / Procedure

Invoke in chat, for example:

```text
/update-nvm-generator-utils /home/sleonarx/projects/ggl_ggl_repo/sources/imc/nvm-generator
```

1. **Obtain the destination path before doing anything.** Use the supplied `nvm_generator_repo_path` when present. If it is missing, ask the user for the NVM Generator repository path. Do not infer it from the current directory or select a repo from nearby workspace folders.

2. Resolve and validate the path. Require an existing directory and confirm it is the NVM Generator Git repository (for example, inspect its top-level path and expected NVM Generator files). Use its Git top-level directory as `<nvm_repo>`. Stop if the path is invalid or does not identify the intended repository; ask the user to correct it.

   ```bash
   git -C "<nvm_generator_repo_path>" rev-parse --show-toplevel
   git -C "<nvm_repo>" status --short --branch
   ```

3. Prepare the source at `/home/sleonarx/utils`:
   - If the directory does not exist, clone the specified repository there:

     ```bash
     git clone https://github.com/intel-innersource/firmware.ethernet.mountevans.nvm-team-utils /home/sleonarx/utils
     ```

   - If it exists, verify that it is a Git repository with that exact `origin` URL. If it is not, stop without modifying it. If it is the expected clone, update it with `git pull --ff-only`; do not discard local changes. If it has local modifications or the fast-forward update cannot proceed, stop and ask the user how to continue.

4. Review the destination worktree status. If it has uncommitted changes, determine whether any overlap paths that the utility copy would update. Do not overwrite overlapping user changes without first explaining the paths and obtaining confirmation. Leave unrelated user changes untouched. Never run `git clean`, `git reset`, or a destructive checkout.

5. Copy the full utility working tree, including hidden files, into `<nvm_repo>`, excluding only the source `.git` metadata. Do not use `--delete`; destination-only files must remain. Preview the changes first, then perform the copy after resolving any overwrite concern:

   ```bash
   rsync -avn --exclude='/.git/' /home/sleonarx/utils/ "<nvm_repo>/"
   rsync -av  --exclude='/.git/' /home/sleonarx/utils/ "<nvm_repo>/"
   ```

   If `rsync` is unavailable, stop and report that prerequisite rather than switching to a copy command that would include `.git` or omit hidden files.

6. Verify the destination changes and report the updated paths:

   ```bash
   git -C "<nvm_repo>" status --short
   git -C "<nvm_repo>" diff --stat
   ```

   Do not stage, commit, or push. Summarize the source revision, destination repository, and changed paths; call out any source or destination changes that prevented the update.
